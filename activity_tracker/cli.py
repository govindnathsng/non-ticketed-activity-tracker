"""Command-line interface for the Non-Ticketed Activity Tracker.

Four commands, three of which you'll actually use weekly:

  extract-auth      Sanity-check your captured Salesforce session.
  convert-calendar  Turn a raw calendar JSON dump into events.json.
  flow-create       Push events.json to Salesforce as PS Tasks.
  update-task       Patch a single field on an existing Task.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import click
from dateutil import parser as dateparser
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .har_auth import build_curl, load_auth
from .sf_flow_client import AuraFlowClient, load_events_json

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_time=True, show_path=False)],
    )
    for noisy in ("urllib3",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@click.group()
@click.version_option(package_name="activity-tracker", message="%(version)s")
def cli() -> None:
    """Bulk-create Salesforce PS Tasks from your weekly meetings."""


# ----------------------------------------------------------------------
# extract-auth — verify the captured session
# ----------------------------------------------------------------------

@cli.command(name="extract-auth")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="A 'Copy as cURL (bash)' dump from DevTools (recommended).")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="A HAR export — fallback if you can't capture a cURL.")
@click.option("--out", "out_path", default=None, type=click.Path(dir_okay=False),
              help="Optional path to save the parsed auth bundle as JSON.")
@click.option("--show-curl/--no-show-curl", default=True, show_default=True,
              help="Print a ready-to-use cURL command for Postman.")
@click.option("--parent-id", "parent_id", default=None,
              help="Override the parent Delivery Task ID parsed from the session.")
def extract_auth_cmd(curl_path: str | None, har_path: str | None,
                     out_path: str | None, show_curl: bool,
                     parent_id: str | None) -> None:
    """Verify your captured Salesforce session and show what was parsed."""
    if not curl_path and not har_path:
        raise click.UsageError("Provide either --curl or --har.")
    auth = load_auth(curl_path or har_path)
    if parent_id:
        auth.record_id = parent_id.strip()

    console.rule("[bold]Salesforce session — parsed from your capture")
    console.print(f"[cyan]host[/cyan]                  : {auth.host}")
    console.print(f"[cyan]quickActionApiName[/cyan]    : {auth.quick_action_api_name}")
    console.print(f"[cyan]recordId (parent)[/cyan]     : {auth.record_id}")
    console.print(f"[cyan]aura.pageURI[/cyan]          : "
                  f"{auth.aura_page_uri[:100]}{'…' if len(auth.aura_page_uri) > 100 else ''}")
    console.print()
    console.print("[bold]aura.token[/bold] (auth, keep secret):")
    console.print(f"  {auth.aura_token[:60]}…")
    console.print()
    if auth.cookies:
        console.print(f"[yellow]cookies[/yellow] ({len(auth.cookies)} captured):")
        for k, v in auth.cookies.items():
            preview = (v[:40] + "…") if len(v) > 40 else v
            console.print(f"  {k} = {preview}")
    else:
        console.print("[yellow]cookies[/yellow]: none captured "
                      "[dim](HAR strips them — recapture as cURL if replay 401s)[/dim]")
    console.print()

    if show_curl:
        console.rule("[bold]Postman / cURL snippet")
        console.print(build_curl(auth))
        console.print()

    if out_path:
        Path(out_path).write_text(
            json.dumps(auth.to_public_dict(), indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]saved[/green] auth bundle → {out_path}")


# ----------------------------------------------------------------------
# convert-calendar — raw JSON → events.json
# ----------------------------------------------------------------------

@cli.command(name="convert-calendar")
@click.option("--input", "in_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Raw calendar JSON dump (date/time/title/participants).")
@click.option("--out", "out_path", default="events.json", show_default=True,
              type=click.Path(dir_okay=False))
@click.option("--default-activity-type", default="Meeting", show_default=True)
@click.option("--default-impl-component", default="None", show_default=True)
@click.option("--default-currency", default="USD", show_default=True)
@click.option("--default-status", default="Completed", show_default=True)
@click.option("--default-priority", default="Normal", show_default=True)
@click.option("--default-client-facing", default="No", show_default=True,
              type=click.Choice(["Yes", "No"]),
              help="Is Client facing? Defaults to No (internal meetings).")
@click.option("--exclude", "excludes", multiple=True, default=("[placeholder]",), show_default=True,
              help="Skip events whose title contains this substring. Repeatable.")
@click.option("--include-participants/--no-include-participants", default=True, show_default=True)
def convert_calendar_cmd(in_path: str, out_path: str, default_activity_type: str,
                         default_impl_component: str, default_currency: str,
                         default_status: str, default_priority: str,
                         default_client_facing: str,
                         excludes: tuple[str, ...], include_participants: bool) -> None:
    """Turn a raw calendar JSON dump into the events.json that flow-create consumes.

    Input shapes accepted (per array element):

    1. ISO start/end times (recommended):
       {"date": "2026-06-08",
        "start_time": "2026-06-08T08:45:00+05:30",
        "end_time":   "2026-06-08T09:15:00+05:30",
        "title": "Standup",
        "participants": [{"name": "alice", "email": "a@x.com"}, ...]}

    2. Legacy "HH:MM - HH:MM TZ" range string:
       {"date": "2026-05-19", "time": "08:45 - 09:15 IST",
        "title": "Standup", "participants": ["alice", "bob"]}

    3. Single start time + duration_minutes (calendar export shape):
       {"date": "2026-07-14", "time": "14:00", "duration_minutes": 60,
        "meeting_name": "Sync", "attendees": ["a@x.com"]}
    """
    raw = json.loads(Path(in_path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise click.UsageError("Input must be a JSON array.")

    excludes_lc = [e.lower() for e in excludes]
    out: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []

    table = Table(title="Converted events", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Time", no_wrap=True)
    table.add_column("Min", justify="right")
    table.add_column("Subject")
    table.add_column("People", justify="right")

    time_re = re.compile(
        r"""\s*
        (\d{1,2}):(\d{2})(?::\d{2})?
        \s*[-–]\s*
        (\d{1,2}):(\d{2})(?::\d{2})?
        \s*
        (?:[A-Z]{2,4} | \([A-Za-z0-9+:\-/\s]+\))?
        \s*$""",
        re.VERBOSE,
    )
    single_time_re = re.compile(
        r"""\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$""",
        re.VERBOSE,
    )

    def _participant_label(p: Any) -> str:
        if isinstance(p, dict):
            return str(p.get("name") or p.get("email") or "").strip()
        return str(p).strip()

    for idx, item in enumerate(raw, 1):
        title = (
            item.get("title")
            or item.get("meeting_name")
            or item.get("summary")
            or item.get("subject")
            or ""
        )
        title = str(title).strip()
        date_str = (item.get("date") or "").strip()
        time_str = str(item.get("time") or "").strip()
        start_raw = (item.get("start_time") or "").strip()
        end_raw = (item.get("end_time") or "").strip()
        participants = item.get("participants") or item.get("attendees") or []
        explicit_duration = item.get("duration_minutes")
        try:
            explicit_duration_int = int(explicit_duration) if explicit_duration is not None else None
        except (TypeError, ValueError):
            explicit_duration_int = None

        if not title or not date_str:
            skipped.append((title or "(no title)", "missing title or date"))
            continue

        title_lc = title.lower()
        kw_hit = next((kw for kw in excludes_lc if kw in title_lc), None)
        if kw_hit:
            skipped.append((title, f"excluded by keyword {kw_hit!r}"))
            continue

        display_time = time_str
        duration = 0
        if start_raw or end_raw:
            if "(All Day)" in start_raw or "(All Day)" in end_raw:
                skipped.append((title, "all-day event"))
                continue
            try:
                start_dt = dateparser.isoparse(start_raw)
                end_dt = dateparser.isoparse(end_raw)
            except (ValueError, TypeError) as exc:
                skipped.append((title, f"unparsable start/end_time ({exc})"))
                continue
            display_time = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
            duration = int((end_dt - start_dt).total_seconds() // 60)
        else:
            m = time_re.match(time_str)
            if m:
                sh, sm, eh, em = m.groups()
                start_min = int(sh) * 60 + int(sm)
                end_min = int(eh) * 60 + int(em)
                duration = end_min - start_min
                if duration <= 0:
                    duration += 24 * 60
            else:
                m_single = single_time_re.match(time_str)
                if m_single and explicit_duration_int and explicit_duration_int > 0:
                    sh, sm = m_single.groups()
                    start_min = int(sh) * 60 + int(sm)
                    duration = explicit_duration_int
                    end_min = start_min + duration
                    display_time = (
                        f"{int(sh):02d}:{int(sm):02d} - "
                        f"{(end_min // 60) % 24:02d}:{end_min % 60:02d}"
                    )
                elif not time_str:
                    skipped.append((title, "all-day event / no time"))
                    continue
                else:
                    skipped.append((title, f"unrecognised time format {time_str!r}"))
                    continue

        if duration <= 0:
            skipped.append((title, "non-positive duration"))
            continue

        long_desc_parts = [f"Meeting time: {display_time}"]
        if include_participants and participants:
            others = [
                lbl for lbl in (_participant_label(p) for p in participants)
                if lbl
                and "govind-nath.s" not in lbl.lower()
                and "(room)" not in lbl.lower()
            ]
            if others:
                long_desc_parts.append(f"Participants: {', '.join(others)}")
        long_desc_parts.append(f"Duration: {duration} min")

        out.append({
            "subject": title,
            "duration_minutes": duration,
            "activity_type": default_activity_type,
            "implementation_component": default_impl_component,
            "revenue_generating": "None",
            "is_client_facing": default_client_facing,
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
            display_time.split(" IST")[0].strip(),
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
            console.print(f"  - {t}  [dim]({r})[/dim]")
        console.print()

    Path(out_path).write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    console.print(f"[green]wrote[/green] {len(out)} events → [bold]{out_path}[/bold]")
    console.print()
    console.print("[dim]Next:[/dim]")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path} --dry-run")
    console.print(f"  activity-tracker flow-create --curl session.curl.sh --input {out_path}")


# ----------------------------------------------------------------------
# flow-create — replay the Lightning Flow for every event
# ----------------------------------------------------------------------

@cli.command(name="flow-create")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="A 'Copy as cURL (bash)' dump from DevTools (recommended).")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="HAR fallback if you can't capture cURL.")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="events.json — the list of tasks to create.")
@click.option("--cookie-string", default="",
              help="Override/add raw 'Cookie:' header value (HAR users only).")
@click.option("--dry-run/--no-dry-run", default=False, show_default=True,
              help="Show what would be sent without hitting Salesforce.")
@click.option("--limit", type=int, default=0,
              help="Only process the first N events (handy for a smoke test). 0 = all.")
@click.option("--parent-id", "parent_id", default=None,
              help="Override the parent Delivery Task ID parsed from the session.")
@click.option("-v", "--verbose", is_flag=True)
def flow_create_cmd(curl_path: str | None, har_path: str | None,
                    input_path: str, cookie_string: str,
                    dry_run: bool, limit: int, parent_id: str | None,
                    verbose: bool) -> None:
    """Push events.json to Salesforce by replaying the 'New PS Task' Flow."""
    _setup_logging(verbose)
    if not curl_path and not har_path:
        raise click.UsageError("Provide either --curl or --har.")
    auth = load_auth(curl_path or har_path)
    original_parent = auth.record_id
    if parent_id:
        auth.record_id = parent_id.strip()
    tasks = load_events_json(input_path)
    if limit:
        tasks = tasks[:limit]

    console.rule("[bold]New PS Task — Flow replay")
    console.print(f"[dim]Host[/dim]               : {auth.host}")
    console.print(f"[dim]Quick action[/dim]       : {auth.quick_action_api_name}")
    if parent_id and parent_id.strip() != original_parent:
        console.print(f"[dim]Parent record[/dim]      : [yellow]{auth.record_id}[/yellow] "
                      f"[dim](overrode session value {original_parent})[/dim]")
    else:
        console.print(f"[dim]Parent record[/dim]      : {auth.record_id}")
    console.print(f"[dim]Events to create[/dim]   : {len(tasks)}")
    console.print(f"[dim]Mode[/dim]               : {'DRY RUN' if dry_run else 'LIVE'}")
    console.print()

    preview = Table(title="Tasks queued", show_lines=False)
    preview.add_column("#", justify="right", style="dim")
    preview.add_column("Subject")
    preview.add_column("Min", justify="right")
    preview.add_column("Type", style="magenta")
    preview.add_column("Impl", style="cyan")
    preview.add_column("Client?", style="green")
    for i, t in enumerate(tasks, 1):
        preview.add_row(
            str(i),
            (t.subject[:60] + "…") if len(t.subject) > 60 else t.subject,
            str(t.duration_minutes),
            t.activity_type,
            t.implementation_component,
            t.is_client_facing,
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
        console.print(f"[dim]>[/dim] [{i}/{len(tasks)}] {t.subject[:70]}")
        result = client.create_ps_task(t)
        if result.ok:
            ok += 1
            tail = f" -> {result.task_id}" if result.task_id else ""
            console.print(f"   [green]created[/green]{tail}")
            if result.fields_updated:
                applied = ", ".join(f"{k}={v!r}" for k, v in result.fields_updated.items())
                console.print(f"   [green]updated:[/green] {applied}")
            elif result.update_error:
                console.print(f"   [yellow]update failed:[/yellow] {result.update_error}")
        else:
            fail += 1
            console.print(f"   [red]failed[/red]: {result.error}")

    console.rule("[bold]Summary")
    console.print(f"  [green]Created:[/green] {ok}")
    console.print(f"  [red]Failed:[/red]  {fail}")
    sys.exit(1 if fail else 0)


# ----------------------------------------------------------------------
# update-task — patch a single Task in place
# ----------------------------------------------------------------------

@cli.command(name="update-task")
@click.option("--curl", "curl_path", type=click.Path(exists=True, dir_okay=False),
              help="cURL dump from DevTools (recommended).")
@click.option("--har", "har_path", type=click.Path(exists=True, dir_okay=False),
              help="HAR fallback.")
@click.option("--task-id", required=True, help="Salesforce Task ID (00T…) to update.")
@click.option("--field", "fields", multiple=True, metavar="KEY=VALUE",
              help="Field to set, e.g. --field Subject='Hello' --field Status=Completed. Repeatable.")
@click.option("--cookie-string", default="", help="Extra cookies if needed.")
def update_task_cmd(curl_path: str | None, har_path: str | None,
                    task_id: str, fields: tuple[str, ...], cookie_string: str) -> None:
    """Patch one or more fields on an existing Task without re-creating it.

    Example:
      activity-tracker update-task --curl session.curl.sh --task-id 00TRg00000u1aPPMAY \\
          --field Subject='Corrected Title' --field Status=Completed
    """
    if not curl_path and not har_path:
        raise click.UsageError("Provide either --curl or --har.")
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
        client.update_task_fields(task_id, field_map)
        console.print(f"[green]saveRecord OK[/green]")
    except Exception as e:
        console.print(f"[red]failed:[/red] {e}")
        sys.exit(1)


def main() -> None:
    cli(prog_name="activity-tracker")


if __name__ == "__main__":
    main()
