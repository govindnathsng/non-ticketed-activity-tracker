"""Thin wrapper around the Salesforce REST API using simple-salesforce."""
from __future__ import annotations

import logging
from typing import Any

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError

from .config import SalesforceConfig

log = logging.getLogger(__name__)


class SalesforceClient:
    def __init__(self, cfg: SalesforceConfig):
        self.cfg = cfg
        self._sf: Salesforce | None = None

    def connect(self) -> Salesforce:
        if self._sf is not None:
            return self._sf

        if self.cfg.auth_method != "password":
            raise NotImplementedError(
                f"auth_method={self.cfg.auth_method!r} not implemented. "
                "Use 'password' for now."
            )

        log.info("Authenticating to Salesforce as %s (domain=%s)", self.cfg.username, self.cfg.domain)
        self._sf = Salesforce(
            username=self.cfg.username,
            password=self.cfg.password,
            security_token=self.cfg.security_token,
            domain=self.cfg.domain,
        )
        return self._sf

    # ---------- queries ----------
    def find_task_by_external_id(self, ext_field: str, ext_id: str) -> dict[str, Any] | None:
        """Find an existing Task by a custom external-id field. Returns None if not found."""
        sf = self.connect()
        # Escape single quotes in the external ID value.
        safe_id = ext_id.replace("'", "\\'")
        q = f"SELECT Id, Subject FROM Task WHERE {ext_field} = '{safe_id}' LIMIT 1"
        result = sf.query(q)
        records = result.get("records", [])
        return records[0] if records else None

    def find_task_by_marker(self, marker: str, since_date: str | None = None) -> dict[str, Any] | None:
        """Find an existing Task whose Description contains a unique marker like '[GCAL:...]'.

        `since_date` is an optional ISO date (YYYY-MM-DD) to limit the LIKE scan for speed.
        """
        sf = self.connect()
        safe = marker.replace("'", "\\'")
        date_clause = f" AND CreatedDate >= {since_date}T00:00:00Z" if since_date else ""
        q = (
            f"SELECT Id, Subject FROM Task "
            f"WHERE Description LIKE '%{safe}%'{date_clause} LIMIT 1"
        )
        result = sf.query(q)
        records = result.get("records", [])
        return records[0] if records else None

    # ---------- writes ----------
    def create_task(self, data: dict[str, Any]) -> str:
        """Create a Task and return its new Id."""
        sf = self.connect()
        try:
            result = sf.Task.create(data)
        except SalesforceError as e:
            log.error("Salesforce rejected Task create: %s | payload=%s", e, data)
            raise
        if not result.get("success"):
            raise RuntimeError(f"Failed to create Task: {result}")
        return result["id"]

    def whoami(self) -> dict[str, Any]:
        """Return the current user's Id, Name and Username (handy for diagnostics)."""
        sf = self.connect()
        result = sf.query("SELECT Id, Name, Username FROM User WHERE Id = UserInfo.getUserId()")
        if result.get("records"):
            return result["records"][0]
        # Fallback: pull from session info
        return {"Id": sf.session_id[:18], "Name": "?", "Username": self.cfg.username}
