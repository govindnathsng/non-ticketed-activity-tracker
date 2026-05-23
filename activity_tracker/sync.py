"""Core sync engine: map calendar events → Salesforce Tasks with dedup."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .calendar_client import CalendarEvent
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    fetched: int = 0
    filtered_out: int = 0
    already_synced: int = 0
    created: int = 0
    failed: int = 0
    dry_run_would_create: int = 0
    created_ids: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.created_ids is None:
            self.created_ids = []


# --------------------------- mapping ---------------------------
def _resolve_type(event: CalendarEvent, cfg: Config) -> str:
    title_l = event.summary.lower()
    for rule in cfg.salesforce.type_rules:
        for kw in rule.match:
            if kw.lower() in title_l:
                return rule.type
    return cfg.salesforce.task_defaults.get("Type", "Meeting")


def _format_description(event: CalendarEvent, marker: str | None) -> str:
    parts: list[str] = []

    if event.description:
        parts.append(event.description.strip())

    meta: list[str] = []
    meta.append(f"When: {event.start.strftime('%Y-%m-%d %H:%M')} → {event.end.strftime('%H:%M')} "
                f"({event.duration_minutes} min)")
    if event.location:
        meta.append(f"Where: {event.location}")
    if event.organizer_email:
        meta.append(f"Organizer: {event.organizer_email}")

    attendee_emails = [a.get("email", "") for a in event.attendees if a.get("email")]
    if attendee_emails:
        shown = ", ".join(attendee_emails[:10])
        more = f" (+{len(attendee_emails) - 10} more)" if len(attendee_emails) > 10 else ""
        meta.append(f"Attendees: {shown}{more}")

    if event.html_link:
        meta.append(f"Calendar link: {event.html_link}")

    if meta:
        parts.append("\n".join(meta))

    if marker:
        parts.append(marker)  # dedup marker, intentionally last

    return "\n\n".join(parts)


def event_to_task_payload(event: CalendarEvent, cfg: Config) -> dict[str, Any]:
    """Build the dict that will be POSTed to Salesforce Task.create."""
    sfc = cfg.salesforce
    data: dict[str, Any] = dict(sfc.task_defaults)  # start with defaults

    data["Subject"] = event.summary[:255]
    data["ActivityDate"] = event.start.date().isoformat()
    data["Type"] = _resolve_type(event, cfg)
    data["CallDurationInSeconds"] = event.duration_seconds

    if sfc.hours_field:
        data[sfc.hours_field] = round(event.duration_seconds / 3600.0, 2)

    marker: str | None
    if sfc.external_id_field:
        data[sfc.external_id_field] = event.id
        marker = None  # we don't need an in-description marker
    else:
        marker = f"[GCAL:{event.id}]"

    data["Description"] = _format_description(event, marker)[:32000]  # SF limit
    return data


# --------------------------- state file ---------------------------
class StateStore:
    """Tiny JSON file remembering which event IDs we already pushed.

    Only used as a fast-path / fallback when no Salesforce external-id field
    is configured. The authoritative check is always the SOQL lookup.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self._ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._ids = set(json.loads(self.path.read_text()))
        except Exception as e:
            log.warning("Could not read state file %s: %s", self.path, e)
            self._ids = set()

    def has(self, event_id: str) -> bool:
        return event_id in self._ids

    def add(self, event_id: str) -> None:
        self._ids.add(event_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._ids), indent=2))


# --------------------------- sync engine ---------------------------
def sync_events(
    events: list[CalendarEvent],
    cfg: Config,
    sf_client,             # SalesforceClient (forward import to avoid cycle)
    *,
    dry_run: bool,
    window_start: datetime,
) -> SyncResult:
    result = SyncResult(fetched=len(events))
    sfc = cfg.salesforce
    state = StateStore(cfg.sync.state_file) if not sfc.external_id_field else None
    since_date = window_start.date().isoformat()

    for ev in events:
        payload = event_to_task_payload(ev, cfg)

        # --- dedup check ---
        already = False
        if sfc.external_id_field:
            if not dry_run:
                existing = sf_client.find_task_by_external_id(sfc.external_id_field, ev.id)
                already = existing is not None
        else:
            if state and state.has(ev.id):
                already = True
            elif not dry_run:
                existing = sf_client.find_task_by_marker(f"[GCAL:{ev.id}]", since_date=since_date)
                if existing is not None:
                    already = True
                    if state:
                        state.add(ev.id)

        if already:
            result.already_synced += 1
            log.info("• already synced: %s (%s)", ev.summary, ev.id)
            continue

        # --- create ---
        if dry_run:
            result.dry_run_would_create += 1
            log.info("[DRY-RUN] would create Task: %s | date=%s | dur=%dm",
                     payload["Subject"], payload["ActivityDate"], ev.duration_minutes)
            continue

        try:
            new_id = sf_client.create_task(payload)
            result.created += 1
            result.created_ids.append(new_id)
            if state:
                state.add(ev.id)
            log.info("✓ created Task %s for event '%s'", new_id, ev.summary)
        except Exception as e:
            result.failed += 1
            log.exception("✗ failed to create Task for event '%s': %s", ev.summary, e)

    if state and not dry_run:
        state.save()

    return result
