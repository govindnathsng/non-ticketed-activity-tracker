"""Config loading + validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GoogleCalendarConfig:
    credentials_file: str
    token_file: str
    calendar_id: str = "primary"
    timezone: str = "UTC"


@dataclass
class TypeRule:
    match: list[str]
    type: str


@dataclass
class SalesforceConfig:
    auth_method: str
    username: str
    password: str
    security_token: str
    domain: str = "login"
    task_defaults: dict[str, Any] = field(default_factory=dict)
    type_rules: list[TypeRule] = field(default_factory=list)
    external_id_field: str = ""
    hours_field: str = ""


@dataclass
class SyncConfig:
    lookback_days: int = 7
    skip_all_day: bool = True
    skip_declined: bool = True
    min_duration_minutes: int = 5
    exclude_title_keywords: list[str] = field(default_factory=list)
    skip_optional_attendee: bool = False
    state_file: str = "./.state/synced_events.json"


@dataclass
class Config:
    google_calendar: GoogleCalendarConfig
    salesforce: SalesforceConfig
    sync: SyncConfig

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}. "
                f"Copy config.example.yaml to {path} and fill in values."
            )
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        gc = raw.get("google_calendar", {})
        sf = raw.get("salesforce", {})
        sy = raw.get("sync", {})

        type_rules = [TypeRule(**r) for r in sf.get("type_rules", [])]

        return cls(
            google_calendar=GoogleCalendarConfig(
                credentials_file=gc.get("credentials_file", "./credentials.json"),
                token_file=gc.get("token_file", "./token.json"),
                calendar_id=gc.get("calendar_id", "primary"),
                timezone=gc.get("timezone", "UTC"),
            ),
            salesforce=SalesforceConfig(
                auth_method=sf.get("auth_method", "password"),
                username=sf.get("username", ""),
                password=sf.get("password", ""),
                security_token=sf.get("security_token", ""),
                domain=sf.get("domain", "login"),
                task_defaults=sf.get("task_defaults", {}) or {},
                type_rules=type_rules,
                external_id_field=sf.get("external_id_field", "") or "",
                hours_field=sf.get("hours_field", "") or "",
            ),
            sync=SyncConfig(
                lookback_days=int(sy.get("lookback_days", 7)),
                skip_all_day=bool(sy.get("skip_all_day", True)),
                skip_declined=bool(sy.get("skip_declined", True)),
                min_duration_minutes=int(sy.get("min_duration_minutes", 5)),
                exclude_title_keywords=list(sy.get("exclude_title_keywords", []) or []),
                skip_optional_attendee=bool(sy.get("skip_optional_attendee", False)),
                state_file=sy.get("state_file", "./.state/synced_events.json"),
            ),
        )
