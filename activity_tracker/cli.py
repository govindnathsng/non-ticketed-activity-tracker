"""Command-line interface for the Non-Ticketed Activity Tracker."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from dateutil import parser as dateparser
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .har_auth import build_curl, extract_auth, load_auth
from .sf_flow_client import AuraFlowClient, load_events_json

# Calendar / Salesforce-REST imports are loaded lazily inside the commands that
# need them, so users who only run flow-create / convert-calendar / update-task
# don't have to install the optional Google / simple-salesforce dependencies.

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_time=True, show_path=False)],
    )
    # Quiet down noisy libs
    for noisy in ("googleapiclient.discovery_cache", "urllib3", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _resolve_window(cfg, since: str | None, until: str | None) -> tuple[datetime, datetime]:
    from .calendar_client import default_window  # lazy: optional Google deps
    if since or until:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(cfg.google_calendar.timezone)
        except Exception:
            tz = timezone.utc
        start = dateparser.parse(since).replace(tzinfo=tz) if since else None
        end = dateparser.parse(until).replace(tzinfo=tz) if until else None
        if start is None or end is None:
            ds, de = default_window(cfg.sync.lookback_days, cfg.google_calendar.timezone)
            start = start or ds
            end = end or de
        return start, end
    return default_window(cfg.sync.lookback_days, cfg.google_calendar.timezone)


@click.group()
@click.version_option(package_name="non-activity-tracking", message="%(version)s")
def cli() -> None:
    """Sync Google Calendar events → Salesforce Tasks (non-ticketed activities)."""


@cli.command()
@click.option("-c", "--config", "config_path", default="config.yaml", show_default=True,
              help="Path to config YAML.")
@click.option("--since", help="Start date (inclusive), e.g. 2026-05-16.")
@click.option("--until", help="End date (exclusive), e.g. 2026-05-23.")
@click.option("--dry-run/--no-dry-run", default=False, show_default=True,
              help="Print what would be created without writing to Salesforce.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging.")
def run(config_path: str, since: str | None, until: str | None, dry_run: bool, verbose: bool) -> None:
    """Pull events for the window and push them as Salesforce Tasks."""
    from .calendar_client import CalendarClient, filter_events
    from .config import Config
    from .salesforce_client import SalesforceClient
    from .sync import sync_events
    _setup_logging(verbose)
    cfg = Config.load(config_path)
    start, end = _resolve_window(cfg, since, until)

    console.rule("[bold]Non-Ticketed Activity Tracker")
    console.print(f"[dim]Window:[/dim] {start.isoformat()}  →  {end.isoformat()}  "
                  f"({(end - start).days} day window)")
    console.print(f"[dim]Calendar:[/dim] {cfg.google_calendar.calendar_id}")
    console.print(f"[dim]Mode:[/dim] {'DRY RUN' if dry_run else 'LIVE — will write Tasks'}")
    console.print()

    gcal = CalendarClient(cfg.google_calendar)
    raw_events = gcal.list_events(start, end)
    events = filter_events(
        raw_events,
        skip_all_day=cfg.sync.skip_all_day,
        skip_declined=cfg.sync.skip_declined,
        min_duration_minutes=cfg.sync.min_duration_minutes,
        exclude_title_keywords=cfg.sync.exclude_title_keywords,
        skip_optional_attendee=cfg.sync.skip_optional_attendee,
    )
    filtered_out = len(raw_events) - len(events)

    table = Table(title="Events to consider", show_lines=False)
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Duration", justify="right")
    table.add_column("Title")
    table.add_column("Type-after-rules", style="magenta")
    from .sync import _resolve_type
    for ev in events:
        table.add_row(
            ev.start.strftime("%Y-%m-%d %H:%M"),
            f"{ev.duration_minutes} min",
            ev.summary,
            _resolve_type(ev, cfg),
        )
    console.print(table)
    console.print(f"[dim]({filtered_out} event(s) filtered out by your rules.)[/dim]\n")

    sf = SalesforceClient(cfg.salesforce)
    if not dry_run:
        info = sf.whoami()
        console.print(f"[green]Connected to Salesforce as[/green] [bold]{info.get('Name')}[/bold] "
                      f"({info.get('Username')})")

    result = sync_events(events, cfg, sf, dry_run=dry_run, window_start=start)
    result.filtered_out = filtered_out

    console.rule("[bold]Summary")
    console.print(f"  Fetched events:       [bold]{result.fetched}[/bold]")
    console.print(f"  Filtered out:         {filtered_out}")
    console.print(f"  Already synced:       {result.already_synced}")
    if dry_run:
        console.print(f"  [yellow]Would create:[/yellow]         {result.dry_run_would_create}")
    else:
        console.print(f"  [green]Created tasks:[/green]        {result.created}")
        console.print(f"  Failed:               {result.failed}")
    console.print()

    sys.exit(1 if result.failed else 0)


@cli.command(name="test-google")
@click.option("-c", "--config", "config_path", default="config.yaml", show_default=True)
def test_google(config_path: str) -> None:
    """Verify Google Calendar OAuth — lists today's events and exits."""
    from .calendar_client import CalendarClient
    from .config import Config
    _setup_logging(False)
    cfg = Config.load(config_path)
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(cfg.google_calendar.timezone)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    gcal = CalendarClient(cfg.google_calendar)
    events = gcal.list_events(start, end)
    console.print(f"Today on calendar [bold]{cfg.google_calendar.calendar_id}[/bold]:")
    if not events:
        console.print("  (no events)")
    for ev in events:
        console.print(f"  • {ev.start.strftime('%H:%M')}  {ev.summary}  ({ev.duration_minutes} min)")


@cli.command(name="test-salesforce")
@click.option("-c", "--config", "config_path", default="config.yaml", show_default=True)
def test_salesforce(config_path: str) -> None:
    """Verify Salesforce credentials — prints the connected user and exits."""
    from .config import Config
    from .salesforce_client import SalesforceClient
    _setup_logging(False)
    cfg = Config.load(config_path)
    sf = SalesforceClient(cfg.salesforce)
    info = sf.whoami()
    console.print(f"[green]✓ Salesforce OK[/green] — {info.get('Name')} ({info.get('Username')})")


@cli.command(name="extract-auth")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="HAR file exported from Chrome while clicking 'New PS Task'.")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="A 'Copy as cURL (bash)' dump from DevTools (recommended — includes cookies).")
@click.option("--out", "out_path", default=None, type=click.Path(dir_okay=False),
              help="Optional path to save the auth bundle as JSON (handy for re-use).")
@click.option("--show-curl/--no-show-curl", default=True, show_default=True,
              help="Print a ready-to-use cURL command (Postman import-friendly).")
def extract_auth_cmd(har_path: str | None, curl_path: str | None,
                     out_path: str | None, show_curl: bool) -> None:
    """Option 1 — Extract session/auth values from a HAR or cURL dump and print them.

    Use this to verify your captured session in Postman before running the
    full automation. Nothing is sent to Salesforce.
    """
    if not har_path and not curl_path:
        raise click.UsageError("Provide either --har or --curl.")
    auth = load_auth(curl_path or har_path)

    console.rule("[bold]Salesforce Aura session — extracted from HAR")
    console.print(f"[cyan]host[/cyan]                  : {auth.host}")
    console.print(f"[cyan]quickActionApiName[/cyan]    : {auth.quick_action_api_name}")
    console.print(f"[cyan]recordId (parent)[/cyan]     : {auth.record_id}")
    console.print(f"[cyan]aura.pageURI[/cyan]          : {auth.aura_page_uri[:100]}{'…' if len(auth.aura_page_uri)>100 else ''}")
    console.print()
    console.print("[bold]aura.token[/bold] (form field, primary auth — keep secret):")
    console.print(f"  {auth.aura_token}")
    console.print()
    console.print("[bold]aura.context[/bold] (form field, app envelope):")
    console.print(f"  {auth.aura_context[:200]}{'…' if len(auth.aura_context)>200 else ''}")
    console.print()

    if auth.cookies:
        console.print(f"[yellow]cookies[/yellow] ({len(auth.cookies)} captured):")
        for k, v in auth.cookies.items():
            preview = (v[:40] + "…") if len(v) > 40 else v
            console.print(f"  {k} = {preview}")
    else:
        console.print("[yellow]cookies[/yellow]: none captured")
        console.print("  [dim](Chrome strips cookies from HAR exports. If replay 401s, copy the[/dim]")
        console.print("  [dim] 'Cookie:' header from DevTools and pass it via --cookie-string.)[/dim]")
    console.print()

    if show_curl:
        console.rule("[bold]Postman / cURL snippet (startFlow)")
        console.print(build_curl(auth))
        console.print()

    if out_path:
        Path(out_path).write_text(
            __import__("json").dumps(auth.to_public_dict(), indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]✓ saved[/green] auth bundle → {out_path}")


@cli.command(name="flow-create")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="HAR file containing a valid Lightning Flow session.")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="A 'Copy as cURL (bash)' dump from DevTools (recommended — single file with cookies + fresh token).")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="JSON file with the events/tasks to create.")
@click.option("--cookie-string", default="", help="Override/add raw 'Cookie:' header value (HAR users only).")
@click.option("--dry-run/--no-dry-run", default=False, show_default=True,
              help="Show what would be sent without hitting Salesforce.")
@click.option("--limit", type=int, default=0,
              help="Only process the first N events (handy for testing). 0 = all.")
@click.option("-v", "--verbose", is_flag=True)
def flow_create_cmd(har_path: str | None, curl_path: str | None,
                    input_path: str, cookie_string: str,
                    dry_run: bool, limit: int, verbose: bool) -> None:
    """Option 2 — Replay the 'New PS Task' Flow for every event in the JSON.

    The JSON file should look like::

        [
          {"summary": "Monday Team Huddle [Pub PS APAC]", "duration_minutes": 30},
          {"summary": "CSD India CP Call", "duration_minutes": 60,
           "activity_type": "Meeting", "implementation_component": "None"}
        ]
    """
    _setup_logging(verbose)
    if not har_path and not curl_path:
        raise click.UsageError("Provide either --har or --curl.")
    auth = load_auth(curl_path or har_path)
    tasks = load_events_json(input_path)
    if limit:
        tasks = tasks[:limit]

    console.rule("[bold]New PS Task — Flow replay")
    console.print(f"[dim]Host[/dim]               : {auth.host}")
    console.print(f"[dim]Quick action[/dim]       : {auth.quick_action_api_name}")
    console.print(f"[dim]Parent record[/dim]      : {auth.record_id}")
    console.print(f"[dim]Events to create[/dim]   : {len(tasks)}")
    console.print(f"[dim]Mode[/dim]               : {'DRY RUN' if dry_run else 'LIVE'}")
    console.print()

    preview = Table(title="Tasks queued", show_lines=False)
    preview.add_column("#", justify="right", style="dim")
    preview.add_column("Subject")
    preview.add_column("Min", justify="right")
    preview.add_column("Activity Type", style="magenta")
    preview.add_column("Impl. Cmp", style="cyan")
    preview.add_column("Overrides", style="yellow")
    for i, t in enumerate(tasks, 1):
        overrides = t.post_create_field_map()
        ov_label = ", ".join(overrides.keys()) if overrides else "—"
        preview.add_row(
            str(i),
            (t.subject[:60] + "…") if len(t.subject) > 60 else t.subject,
            str(t.duration_minutes),
            t.activity_type,
            t.implementation_component,
            (ov_label[:40] + "…") if len(ov_label) > 40 else ov_label,
        )
    console.print(preview)
    console.print()

    if dry_run:
        console.print("[yellow]DRY RUN[/yellow] — no requests sent.")
        return

    client = AuraFlowClient(auth, cookie_string=cookie_string)
    ok = 0
    fail = 0
    for i, t in enumerate(tasks, 1):
        console.print(f"[dim]→[/dim] [{i}/{len(tasks)}] {t.subject[:70]}")
        result = client.create_ps_task(t)
        if result.ok:
            ok += 1
            tail = f" → {result.task_id}" if result.task_id else ""
            console.print(f"   [green]✓ created[/green]{tail}")
            if result.fields_updated:
                applied = ", ".join(f"{k}={v!r}" for k, v in result.fields_updated.items())
                console.print(f"   [green]✓ updated fields:[/green] {applied}")
            elif result.update_error:
                console.print(f"   [yellow]⚠ update failed:[/yellow] {result.update_error}")
        else:
            fail += 1
            console.print(f"   [red]✗ failed[/red]: {result.error}")

    console.rule("[bold]Summary")
    console.print(f"  [green]Created:[/green] {ok}")
    console.print(f"  [red]Failed:[/red]  {fail}")
    sys.exit(1 if fail else 0)


@cli.command(name="fetch-calendar")
@click.option("-c", "--config", "config_path", default="config.yaml", show_default=True)
@click.option("--days", type=int, default=7, show_default=True,
              help="How many days back to fetch (default 7).")
@click.option("--since", help="Start date (inclusive), e.g. 2026-05-16.")
@click.option("--until", help="End date (exclusive), e.g. 2026-05-23.")
@click.option("--out", "out_path", default="events.json", show_default=True,
              type=click.Path(dir_okay=False),
              help="Where to write the JSON ready for flow-create.")
@click.option("--default-activity-type", default="Meeting", show_default=True,
              help="Activity Type used for every fetched event.")
@click.option("--default-impl-component", default="None", show_default=True,
              help="Implementation Component used for every fetched event.")
@click.option("--default-currency", default="USD", show_default=True)
@click.option("--default-status", default="Completed", show_default=True)
@click.option("--default-priority", default="Normal", show_default=True)
@click.option("--include-participants/--no-include-participants", default=True, show_default=True,
              help="Append attendee emails to the long-form Task Description.")
@click.option("-v", "--verbose", is_flag=True)
def fetch_calendar_cmd(config_path: str, days: int, since: str | None, until: str | None,
                       out_path: str, default_activity_type: str,
                       default_impl_component: str, default_currency: str,
                       default_status: str, default_priority: str,
                       include_participants: bool, verbose: bool) -> None:
    """Pull events from Google Calendar and write them as events.json (for flow-create).

    Reads filter rules from config.yaml (exclude_title_keywords, min_duration_minutes,
    skip_all_day, skip_declined). Output is an array of objects in the exact format
    flow-create expects — no manual editing required.
    """
    from .calendar_client import CalendarClient, default_window, filter_events
    from .config import Config
    _setup_logging(verbose)
    cfg = Config.load(config_path)

    if since or until:
        start, end = _resolve_window(cfg, since, until)
    else:
        start, end = default_window(days, cfg.google_calendar.timezone)

    console.rule("[bold]Fetch Google Calendar")
    console.print(f"  Calendar : {cfg.google_calendar.calendar_id}")
    console.print(f"  Window   : {start.isoformat()} → {end.isoformat()}")
    console.print(f"  Filters  : skip_all_day={cfg.sync.skip_all_day}, "
                  f"skip_declined={cfg.sync.skip_declined}, "
                  f"min_duration={cfg.sync.min_duration_minutes}m")
    console.print(f"  Excluded : {cfg.sync.exclude_title_keywords}")
    console.print()

    gcal = CalendarClient(cfg.google_calendar)
    raw_events = gcal.list_events(start, end)
    events = filter_events(
        raw_events,
        skip_all_day=cfg.sync.skip_all_day,
        skip_declined=cfg.sync.skip_declined,
        min_duration_minutes=cfg.sync.min_duration_minutes,
        exclude_title_keywords=cfg.sync.exclude_title_keywords,
        skip_optional_attendee=cfg.sync.skip_optional_attendee,
    )

    filtered_out = len(raw_events) - len(events)
    console.print(f"  Fetched  : {len(raw_events)} raw → {len(events)} after filters "
                  f"({filtered_out} dropped)\n")

    from .sync import _resolve_type
    out: list[dict[str, Any]] = []
    table = Table(title="Events to write", show_lines=False)
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Min", justify="right")
    table.add_column("Subject")
    table.add_column("Type", style="magenta")

    for ev in events:
        derived_type = _resolve_type(ev, cfg)  # Call / Meeting / Other from title keywords
        # Map to our Flow's activity_type universe.
        activity_type = default_activity_type
        if derived_type in ("Meeting", "Call"):
            activity_type = "Meeting"

        attendee_emails = [
            a.get("email") for a in (ev.attendees or [])
            if a.get("email") and not a.get("self")
        ]

        long_desc_parts = []
        if ev.description:
            long_desc_parts.append(ev.description)
        if include_participants and attendee_emails:
            long_desc_parts.append(f"Participants: {', '.join(attendee_emails)}")
        long_desc_parts.append(f"Duration: {ev.duration_minutes} min")
        if ev.html_link:
            long_desc_parts.append(f"Calendar: {ev.html_link}")
        long_desc_parts.append(f"[GCAL:{ev.id}]")

        out.append({
            "subject": ev.summary,
            "duration_minutes": ev.duration_minutes,
            "activity_type": activity_type,
            "implementation_component": default_impl_component,
            "revenue_generating": "None",

            "task_type": activity_type,
            "task_sub_type": activity_type,
            "activity_currency": default_currency,
            "time_spent_in_minutes": ev.duration_minutes,
            "status": default_status,
            "priority": default_priority,
            "activity_date": ev.start.strftime("%Y-%m-%d"),
            "description": "\n".join(long_desc_parts),

            "_gcal": {
                "event_id": ev.id,
                "start": ev.start.isoformat(),
                "end": ev.end.isoformat(),
                "organizer": ev.organizer_email,
                "participants": attendee_emails,
                "location": ev.location,
            },
        })

        table.add_row(
            ev.start.strftime("%a %m-%d %H:%M"),
            str(ev.duration_minutes),
            (ev.summary[:60] + "…") if len(ev.summary) > 60 else ev.summary,
            derived_type,
        )

    console.print(table)
    console.print()

    Path(out_path).write_text(
        __import__("json").dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    console.print(f"[green]✓ wrote[/green] {len(out)} events → [bold]{out_path}[/bold]")
    console.print()
    console.print("[dim]Next:[/dim]")
    console.print(f"  [dim]# Preview without sending:[/dim]")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path} --dry-run")
    console.print(f"  [dim]# Push to Salesforce:[/dim]")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path}")


@cli.command(name="convert-calendar")
@click.option("--input", "in_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Raw calendar JSON file (with date/time/title/participants fields).")
@click.option("--out", "out_path", default="events.json", show_default=True,
              type=click.Path(dir_okay=False))
@click.option("--default-activity-type", default="Meeting", show_default=True)
@click.option("--default-impl-component", default="None", show_default=True)
@click.option("--default-currency", default="USD", show_default=True)
@click.option("--default-status", default="Completed", show_default=True)
@click.option("--default-priority", default="Normal", show_default=True)
@click.option("--exclude", "excludes", multiple=True, default=("[placeholder]",), show_default=True,
              help="Skip events whose title (case-insensitive) contains this substring. Repeatable.")
@click.option("--include-participants/--no-include-participants", default=True, show_default=True)
def convert_calendar_cmd(in_path: str, out_path: str, default_activity_type: str,
                         default_impl_component: str, default_currency: str,
                         default_status: str, default_priority: str,
                         excludes: tuple[str, ...], include_participants: bool) -> None:
    """Convert a raw calendar JSON dump (date/time/title/participants) into events.json
    that flow-create can consume directly. No Google OAuth needed.

    Input format expected (array of objects):
        {"date": "2026-05-19", "time": "08:45 - 09:15 IST",
         "title": "Standup", "participants": ["alice", "bob"]}
    """
    import json as _json
    import re

    raw = _json.loads(Path(in_path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise click.UsageError("Input must be a JSON array.")

    excludes_lc = [e.lower() for e in excludes]
    out: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []   # (title, reason)

    table = Table(title="Converted events", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Time", no_wrap=True)
    table.add_column("Min", justify="right")
    table.add_column("Subject")
    table.add_column("People", justify="right")

    time_re = re.compile(r"\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*([A-Z]{2,4})?")

    for idx, item in enumerate(raw, 1):
        title = (item.get("title") or "").strip()
        date_str = (item.get("date") or "").strip()
        time_str = (item.get("time") or "").strip()
        participants = item.get("participants") or []

        if not title or not date_str:
            skipped.append((title or "(no title)", "missing title or date"))
            continue

        title_lc = title.lower()
        kw_hit = next((kw for kw in excludes_lc if kw in title_lc), None)
        if kw_hit:
            skipped.append((title, f"excluded by keyword {kw_hit!r}"))
            continue

        m = time_re.match(time_str)
        if not m:
            skipped.append((title, f"unrecognised time format {time_str!r}"))
            continue
        sh, sm, eh, em, _tz = m.groups()
        start_min = int(sh) * 60 + int(sm)
        end_min = int(eh) * 60 + int(em)
        duration = end_min - start_min
        if duration <= 0:
            duration += 24 * 60  # past midnight
        if duration <= 0:
            skipped.append((title, "non-positive duration"))
            continue

        long_desc_parts = [f"Meeting time: {time_str}"]
        if include_participants and participants:
            others = [p for p in participants if str(p).lower() != "govind-nath.s"]
            if others:
                long_desc_parts.append(f"Participants: {', '.join(others)}")
        long_desc_parts.append(f"Duration: {duration} min")

        out.append({
            "subject": title,
            "duration_minutes": duration,
            "activity_type": default_activity_type,
            "implementation_component": default_impl_component,
            "revenue_generating": "None",
            "task_type": default_activity_type,
            "task_sub_type": default_activity_type,
            "activity_currency": default_currency,
            "time_spent_in_minutes": duration,
            "status": default_status,
            "priority": default_priority,
            "activity_date": date_str,
            "description": "\n".join(long_desc_parts),
        })
        table.add_row(
            str(idx),
            date_str,
            time_str.split(" IST")[0].strip(),
            str(duration),
            (title[:55] + "…") if len(title) > 55 else title,
            str(len(participants)),
        )

    console.rule("[bold]Convert calendar JSON → events.json")
    console.print(table)
    console.print()

    if skipped:
        console.print(f"[yellow]Skipped {len(skipped)} event(s):[/yellow]")
        for t, r in skipped:
            console.print(f"  • {t}  [dim]({r})[/dim]")
        console.print()

    Path(out_path).write_text(
        __import__("json").dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    console.print(f"[green]✓ wrote[/green] {len(out)} events → [bold]{out_path}[/bold]")
    console.print()
    console.print("[dim]Next:[/dim]")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path} --dry-run")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path}")


@cli.command(name="update-task")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="HAR file with a valid session.")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="cURL dump from DevTools (recommended).")
@click.option("--task-id", required=True, help="Salesforce Task ID (00T…) to update.")
@click.option("--field", "fields", multiple=True, metavar="KEY=VALUE",
              help="Field to set, e.g. --field Subject='Hello' --field Status=Completed. Repeatable.")
@click.option("--cookie-string", default="", help="Extra cookies if needed.")
def update_task_cmd(har_path: str | None, curl_path: str | None,
                    task_id: str, fields: tuple[str, ...], cookie_string: str) -> None:
    """Patch an existing Task's fields without creating a new one.

    Useful for fixing a record produced by an earlier run, or for any quick
    inline-edit-style update via the same captured session.

    Example:
      activity-tracker update-task --curl session.curl.sh --task-id 00TRg00000u1aPPMAY \\
          --field Subject='Testing AUtomation' --field Status=Completed
    """
    if not har_path and not curl_path:
        raise click.UsageError("Provide either --har or --curl.")
    if not fields:
        raise click.UsageError("Provide at least one --field KEY=VALUE.")

    field_map: dict[str, str] = {}
    for kv in fields:
        if "=" not in kv:
            raise click.BadParameter(f"Expected KEY=VALUE, got {kv!r}", param_hint="--field")
        k, v = kv.split("=", 1)
        field_map[k.strip()] = v.strip()

    auth = load_auth(curl_path or har_path)
    client = AuraFlowClient(auth, cookie_string=cookie_string)

    console.rule("[bold]Update Task")
    console.print(f"  Task ID : {task_id}")
    console.print(f"  Fields  :")
    for k, v in field_map.items():
        console.print(f"    {k} = {v!r}")
    console.print()

    try:
        resp = client.update_task_fields(task_id, field_map)
        console.print(f"[green]✓ saveRecord OK[/green]  response keys: {list(resp)[:8] if isinstance(resp, dict) else type(resp).__name__}")
    except Exception as e:
        console.print(f"[red]✗ failed:[/red] {e}")
        sys.exit(1)


def main() -> None:
    cli(prog_name="non-activity-tracking")


if __name__ == "__main__":
    main()
