"""Thin wrapper around the Google Calendar API."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from dateutil import parser as dateparser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import GoogleCalendarConfig

log = logging.getLogger(__name__)

# Read-only scope — we never modify the user's calendar.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass
class CalendarEvent:
    """A normalized calendar event ready for mapping to a Salesforce Task."""

    id: str
    summary: str
    description: str
    start: datetime
    end: datetime
    is_all_day: bool
    organizer_email: str
    attendees: list[dict[str, Any]]
    html_link: str
    response_status: str  # the current user's response: accepted/declined/...
    location: str

    @property
    def duration_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


class CalendarClient:
    def __init__(self, cfg: GoogleCalendarConfig):
        self.cfg = cfg
        self._service = None

    # ---------- auth ----------
    def _load_credentials(self) -> Credentials:
        token_path = Path(self.cfg.token_file)
        creds: Credentials | None = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Google OAuth token...")
            creds.refresh(Request())
        else:
            cred_path = Path(self.cfg.credentials_file)
            if not cred_path.exists():
                raise FileNotFoundError(
                    f"Google OAuth client file not found: {cred_path}. "
                    f"Download it from Google Cloud Console "
                    f"(APIs & Services → Credentials → OAuth client ID)."
                )
            log.info("Starting Google OAuth flow — a browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        return creds

    def _get_service(self):
        if self._service is None:
            creds = self._load_credentials()
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # ---------- queries ----------
    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Fetch events in [start, end). Both must be timezone-aware."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware datetimes")

        service = self._get_service()
        events: list[CalendarEvent] = []
        page_token: str | None = None

        log.info(
            "Fetching events from calendar=%s between %s and %s",
            self.cfg.calendar_id, start.isoformat(), end.isoformat(),
        )

        while True:
            resp = service.events().list(
                calendarId=self.cfg.calendar_id,
                timeMin=start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                timeMax=end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
                pageToken=page_token,
                showDeleted=False,
            ).execute()

            for raw in resp.get("items", []):
                event = self._parse_event(raw)
                if event is not None:
                    events.append(event)

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        log.info("Fetched %d events.", len(events))
        return events

    # ---------- parsing ----------
    def _parse_event(self, raw: dict[str, Any]) -> CalendarEvent | None:
        if raw.get("status") == "cancelled":
            return None

        start_obj = raw.get("start", {})
        end_obj = raw.get("end", {})
        is_all_day = "date" in start_obj

        try:
            if is_all_day:
                start = dateparser.isoparse(start_obj["date"]).replace(tzinfo=timezone.utc)
                end = dateparser.isoparse(end_obj["date"]).replace(tzinfo=timezone.utc)
            else:
                start = dateparser.isoparse(start_obj["dateTime"])
                end = dateparser.isoparse(end_obj["dateTime"])
        except (KeyError, ValueError) as e:
            log.warning("Skipping event %s: bad date (%s)", raw.get("id"), e)
            return None

        # Find the current user's response status.
        response_status = "accepted"  # default for events you created
        for attendee in raw.get("attendees", []) or []:
            if attendee.get("self"):
                response_status = attendee.get("responseStatus", "accepted")
                break

        return CalendarEvent(
            id=raw.get("id", ""),
            summary=(raw.get("summary") or "(no title)").strip(),
            description=(raw.get("description") or "").strip(),
            start=start,
            end=end,
            is_all_day=is_all_day,
            organizer_email=(raw.get("organizer") or {}).get("email", ""),
            attendees=raw.get("attendees", []) or [],
            html_link=raw.get("htmlLink", ""),
            response_status=response_status,
            location=raw.get("location", "") or "",
        )


def default_window(lookback_days: int, tz_name: str) -> tuple[datetime, datetime]:
    """Return (start, end) covering the last `lookback_days` whole days, in tz_name."""
    try:
        from zoneinfo import ZoneInfo  # py>=3.9
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    end = now
    start = (now - timedelta(days=lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def filter_events(
    events: Iterable[CalendarEvent],
    *,
    skip_all_day: bool,
    skip_declined: bool,
    min_duration_minutes: int,
    exclude_title_keywords: list[str],
    skip_optional_attendee: bool = False,
) -> list[CalendarEvent]:
    keywords = [k.lower() for k in (exclude_title_keywords or [])]
    out: list[CalendarEvent] = []
    for ev in events:
        if skip_all_day and ev.is_all_day:
            log.debug("skip all-day: %s", ev.summary); continue
        if skip_declined and ev.response_status == "declined":
            log.debug("skip declined: %s", ev.summary); continue
        if ev.duration_minutes < min_duration_minutes:
            log.debug("skip short (%dm): %s", ev.duration_minutes, ev.summary); continue
        title_l = ev.summary.lower()
        if any(k in title_l for k in keywords):
            log.debug("skip by keyword: %s", ev.summary); continue
        if skip_optional_attendee:
            for a in ev.attendees:
                if a.get("self") and a.get("optional"):
                    log.debug("skip optional: %s", ev.summary); break
            else:
                out.append(ev)
            continue
        out.append(ev)
    return out
