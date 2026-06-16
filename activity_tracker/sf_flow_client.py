"""Replay the Salesforce Lightning "New PS Task" Flow via the internal /aura
endpoint, using the auth bundle extracted from a HAR.

This is intentionally a *thin* wrapper: it mirrors exactly what the browser
does (startFlow → navigateFlow NEXT → navigateFlow FINISH), so the org's
validation rules and side-effects all fire the same way.

If Salesforce ever changes the Flow definition (renamed field, new screen,
extra required choice), the replay will fail with a clear error and you'll
need to re-record a fresh HAR.
"""
from __future__ import annotations

import itertools
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

from .har_auth import FlowAuth

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------

@dataclass
class TaskInput:
    """A single record we want to create via the Flow and then enrich.

    Two phases:
      1. Flow inputs (`subject`, `activity_type`, etc.) — what the manual form asks for.
      2. Post-create overrides (`task_type`, `task_sub_type`, `activity_currency`,
         `status`, etc.) — standard Task fields written via saveRecord after the
         Flow has produced the Task. These map 1-to-1 to the Salesforce Task page
         layout (Subject, Task Type, Task Sub Type, Activity Currency, …).

    Only `subject` is required. Everything else has sensible defaults.
    """

    # --- Flow inputs (drive the form the user normally fills) ----------
    subject: str                              # → Flow Description → Task.Subject
    duration_minutes: int = 30                # → Flow Time_Spent_in_Minutes
    activity_type: str = "Meeting"            # Meeting / Account Exploration and Maintenance / Internal PS work
    implementation_component: str = "None"    # one of 31 product choices
    revenue_generating: str = "None"          # Positive / Negative / None
    is_client_facing: str = "No"              # Yes / No (required since Jun 2026 Flow update)
    internal_ps_work_type: str | None = None  # required only when activity_type='Internal PS work'

    # Optional "Related to" lookup (pick at most one)
    related_to: str | None = None             # Account / Delivery Task / Delivery Project / Backstage Account
    account_id: str | None = None
    delivery_task_id: str | None = None
    delivery_project_id: str | None = None
    backstage_account_id: str | None = None

    # --- Post-create overrides (written via saveRecord) ----------------
    # All of these are OPTIONAL. If set, they overwrite whatever the Flow put on
    # the Task. If left None, the Flow's defaults stand.
    task_type: str | None = None              # → Task.Task_Type__c
    task_sub_type: str | None = None          # → Task.Task_Sub_Type__c
    activity_currency: str | None = None      # → Task.CurrencyIsoCode (e.g. "USD")
    time_spent_in_minutes: int | None = None  # → Task.Time_Spent_in_minutes_integer__c
    description: str | None = None            # → Task.Description (long-form notes, separate from Subject)
    status: str | None = None                 # → Task.Status (e.g. "Completed")
    priority: str | None = None               # → Task.Priority
    activity_date: str | None = None          # → Task.ActivityDate (YYYY-MM-DD)
    extra_fields: dict[str, Any] = field(default_factory=dict)  # any other Task API field

    # Parent override
    record_id: str | None = None              # parent Delivery_Task__c; defaults to session

    @classmethod
    def from_event_json(cls, obj: dict[str, Any]) -> "TaskInput":
        """Build a TaskInput from a JSON dict. Many keys are aliased for ease of use."""
        if obj.get("_label") and "summary" not in obj and "subject" not in obj:
            raise _SkipEvent(obj["_label"])

        # Subject can come from any of: subject / summary / description
        subject = obj.get("subject") or obj.get("summary") or obj.get("description")
        if not subject:
            raise ValueError(
                f"Event missing 'subject' (aliases: 'summary'): {obj!r}"
            )

        # duration: accept either duration_minutes or time_spent_in_minutes
        dur = obj.get("duration_minutes")
        if dur is None:
            dur = obj.get("time_spent_in_minutes", 30)

        # `description` is ambiguous: if `summary`/`subject` was used,
        # `description` becomes the long-form Task.Description field.
        long_description = None
        if "description" in obj and obj.get("description") != subject:
            long_description = obj["description"]

        return cls(
            subject=subject,
            duration_minutes=int(dur),
            activity_type=obj.get("activity_type", "Meeting"),
            implementation_component=obj.get("implementation_component", "None"),
            revenue_generating=obj.get("revenue_generating", "None"),
            is_client_facing=obj.get("is_client_facing", "No"),
            internal_ps_work_type=obj.get("internal_ps_work_type"),
            related_to=obj.get("related_to"),
            account_id=obj.get("account_id"),
            delivery_task_id=obj.get("delivery_task_id"),
            delivery_project_id=obj.get("delivery_project_id"),
            backstage_account_id=obj.get("backstage_account_id"),

            task_type=obj.get("task_type"),
            task_sub_type=obj.get("task_sub_type"),
            activity_currency=obj.get("activity_currency") or obj.get("currency"),
            time_spent_in_minutes=(
                int(obj["time_spent_in_minutes"]) if obj.get("time_spent_in_minutes") is not None else None
            ),
            description=long_description,
            status=obj.get("status"),
            priority=obj.get("priority"),
            activity_date=obj.get("activity_date") or obj.get("date"),
            extra_fields=obj.get("extra_fields", {}) or {},

            record_id=obj.get("recordId") or obj.get("record_id"),
        )

    # ------------------------------------------------------------------
    def post_create_field_map(self) -> dict[str, Any]:
        """Return the field map written via saveRecord after the Flow finishes.

        We ALWAYS include `Subject` here — the Flow's "Description" input does
        NOT propagate to Task.Subject (it gets overwritten by the activity_type
        value). Sending Subject in Phase 2 is the only reliable way to get the
        text the user actually wants as the headline.

        Other fields are only sent if the user explicitly populated them, so
        whatever the Flow's defaults are survive for the rest.
        """
        out: dict[str, Any] = {
            "Subject": self.subject,  # always force-set
        }
        if self.task_type is not None:               out["Task_Type__c"] = self.task_type
        if self.task_sub_type is not None:           out["Task_Sub_Type__c"] = self.task_sub_type
        if self.activity_currency is not None:       out["CurrencyIsoCode"] = self.activity_currency
        if self.time_spent_in_minutes is not None:   out["Time_Spent_in_minutes_integer__c"] = self.time_spent_in_minutes
        if self.description is not None:             out["Description"] = self.description
        if self.status is not None:                  out["Status"] = self.status
        if self.priority is not None:                out["Priority"] = self.priority
        if self.activity_date is not None:           out["ActivityDate"] = self.activity_date
        # extra_fields take precedence — last write wins
        out.update(self.extra_fields)
        return out


class _SkipEvent(Exception):
    """Raised when a JSON entry is a documentation row, not an actual event."""


@dataclass
class FlowResult:
    """Outcome of a single Flow replay (and any post-create field update)."""
    ok: bool
    task_id: str | None = None     # Salesforce 18-char Task ID (00T…) if extractable
    message: str = ""              # human-readable success/error text
    error: str = ""                # populated when ok=False
    fields_updated: dict[str, Any] = field(default_factory=dict)   # post-create overrides applied
    update_error: str = ""         # populated if the post-create update failed
    raw_responses: list[dict[str, Any]] = field(default_factory=list)


# -----------------------------------------------------------------------
# Client
# -----------------------------------------------------------------------

class AuraFlowClient:
    """Replays a Salesforce Lightning Quick-Action Flow via the /aura endpoint.

    Usage:
        auth = extract_auth("new-task.har")
        client = AuraFlowClient(auth)
        result = client.create_ps_task(TaskInput(description="Monday Huddle", time_spent_minutes=30))
    """

    def __init__(self, auth: FlowAuth, *, cookie_string: str = "", session: requests.Session | None = None):
        self.auth = auth
        self.session = session or requests.Session()

        # Mirror the browser as closely as we can.
        self.session.headers.update({
            "Accept": "*/*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": f"https://{auth.host}",
            "Referer": auth.referer or f"https://{auth.host}/",
            "User-Agent": auth.user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "X-SFDC-Request-Id": "0" * 18,
        })

        # Apply cookies — from the HAR (usually none) then any pasted ones.
        for k, v in auth.cookies.items():
            self.session.cookies.set(k, v, domain=auth.host)
        if cookie_string:
            for piece in cookie_string.split(";"):
                piece = piece.strip()
                if "=" in piece:
                    k, v = piece.split("=", 1)
                    self.session.cookies.set(k.strip(), v.strip(), domain=auth.host)

        # Rolling action id (`<n>;a`) — Salesforce only requires uniqueness per request.
        self._action_counter = itertools.count(1000)
        # Rolling request counter for the `?r=` query param (cosmetic but matches browser).
        self._req_counter = itertools.count(1)

    # ------------------------------------------------------------------
    # Low-level: post a single Aura action and return the parsed reply
    # ------------------------------------------------------------------

    def _post_action(self, descriptor: str, params: dict[str, Any], *, query_action: str) -> dict[str, Any]:
        """Send one Aura action and return its parsed `returnValue.response` block.

        Raises FlowReplayError on transport / auth / state failures.
        """
        action_id = f"{next(self._action_counter)};a"
        message = {
            "actions": [{
                "id": action_id,
                "descriptor": descriptor,
                "callingDescriptor": "UNKNOWN",
                "params": params,
            }]
        }
        body = {
            "message": json.dumps(message, separators=(",", ":")),
            "aura.context": self.auth.aura_context,
            "aura.pageURI": self.auth.aura_page_uri,
            "aura.token": self.auth.aura_token,
        }
        r_num = next(self._req_counter)
        url = self.auth.aura_url(f"r={r_num}&{query_action}")
        log.debug("POST %s  (descriptor=%s)", url, descriptor)
        resp = self.session.post(url, data=body, timeout=30)

        if resp.status_code != 200:
            raise FlowReplayError(
                f"HTTP {resp.status_code} from Salesforce: {resp.text[:400]}"
            )
        try:
            parsed = resp.json()
        except ValueError as exc:
            raise FlowReplayError(f"Non-JSON response from Salesforce: {resp.text[:400]}") from exc

        # Salesforce signals session/auth problems with a top-level `exceptionMessage`.
        if isinstance(parsed, dict) and parsed.get("exceptionMessage"):
            raise FlowReplayError(
                f"Salesforce exception: {parsed.get('exceptionMessage')!r}. "
                "Your aura.token has likely expired — re-export the HAR while logged in."
            )

        actions = parsed.get("actions") or []
        if not actions:
            raise FlowReplayError(f"Empty `actions` array in response: {resp.text[:400]}")
        action = actions[0]
        state = action.get("state")
        if state != "SUCCESS":
            errs = action.get("error") or []
            err_msg = "; ".join(e.get("message", str(e)) for e in errs) or str(errs)
            raise FlowReplayError(f"Aura action state={state}: {err_msg}")

        return_value = action.get("returnValue")
        # Flow runtime nests the real payload under `response`. Other endpoints
        # (e.g. RecordGvpController.saveRecord) return the value directly,
        # which can be a dict OR a primitive (string / None). Just hand back
        # whatever shape we got — let callers decide what to do with it.
        if isinstance(return_value, dict) and "response" in return_value:
            return return_value["response"]
        return return_value if return_value is not None else {}

    # ------------------------------------------------------------------
    # Flow steps
    # ------------------------------------------------------------------

    def get_flow_info(self, record_id: str | None = None) -> dict[str, Any]:
        """First call the browser makes — sanity-check the flow exists and is reachable."""
        return self._post_action(
            descriptor=(
                "serviceComponent://ui.interaction.runtime.components.controllers."
                "FlowQuickActionRuntimeController/ACTION$getFlowInfo"
            ),
            params={
                "quickActionApiName": self.auth.quick_action_api_name,
                "quickActionSubjectId": record_id or self.auth.record_id,
            },
            query_action=(
                "ui-interaction-runtime-components-controllers."
                "FlowQuickActionRuntimeController.getFlowInfo=1"
            ),
        )

    def start_flow(self, flow_dev_name: str, record_id: str) -> dict[str, Any]:
        """Begin a fresh Flow interview; returns the initial serializedEncodedState."""
        return self._post_action(
            descriptor="aura://FlowRuntimeConnectController/ACTION$startFlow",
            params={
                "flowDevName": flow_dev_name,
                "arguments": json.dumps([
                    {"name": "recordId", "type": "String", "value": record_id}
                ]),
                "enableTrace": False,
                "enableRollbackMode": False,
                "debugAsUserId": "",
                "useLatestSubflow": False,
                "isBuilderDebug": False,
            },
            query_action="aura.FlowRuntimeConnect.startFlow=1",
        )

    def navigate_flow(self, serialized_state: str, *, action: str = "NEXT",
                      fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Advance the Flow one step (NEXT / FINISH / BACK)."""
        return self._post_action(
            descriptor="aura://FlowRuntimeConnectController/ACTION$navigateFlow",
            params={
                "request": {
                    "action": action,
                    "serializedState": serialized_state,
                    "fields": fields or [],
                    "uiElementVisited": True,
                    "enableTrace": False,
                    "lcErrors": {},
                    "isBuilderDebug": False,
                }
            },
            query_action="aura.FlowRuntimeConnect.navigateFlow=1",
        )

    # ------------------------------------------------------------------
    # saveRecord — post-create field updates on the Task
    # ------------------------------------------------------------------

    def update_task_fields(self, task_id: str, fields: dict[str, Any],
                           api_name: str = "Task") -> dict[str, Any]:
        """Patch a record by calling RecordGvpController.saveRecord.

        Mirrors what Lightning does on inline edit (captured in data-feeder.har).
        Only the keys in `fields` are sent — the existing values for other
        columns are preserved by Salesforce.
        """
        if not fields:
            return {}

        record_fields = {"Id": {"value": task_id}}
        for fname, fval in fields.items():
            record_fields[fname] = {"value": fval}

        return self._post_action(
            descriptor=(
                "serviceComponent://ui.force.components.controllers."
                "recordGlobalValueProvider.RecordGvpController/ACTION$saveRecord"
            ),
            params={
                "recordRep": {
                    "id": task_id,
                    "apiName": api_name,
                    "fields": record_fields,
                },
                "useCellBasedActions": False,
            },
            query_action=(
                "ui-force-components-controllers-recordGlobalValueProvider."
                "RecordGvp.saveRecord=1"
            ),
        )

    # ------------------------------------------------------------------
    # High-level: create one PS Task end-to-end
    # ------------------------------------------------------------------

    def create_ps_task(self, task: TaskInput) -> FlowResult:
        """Drive the New_PS_Task flow to completion for a single TaskInput.

        Returns a FlowResult with the created Task ID (parsed from the
        success-screen markup) when possible.
        """
        record_id = task.record_id or self.auth.record_id
        flow_dev_name = self.auth.quick_action_api_name.split(".")[-1]  # 'New_PS_Task'
        raw: list[dict[str, Any]] = []

        try:
            # 1. Sanity-check the Flow (cheap; mirrors the browser).
            info = self.get_flow_info(record_id=record_id)
            raw.append({"step": "getFlowInfo", "response": info})

            # 2. Start the interview.
            started = self.start_flow(flow_dev_name=flow_dev_name, record_id=record_id)
            raw.append({"step": "startFlow", "response": started})
            state = started.get("serializedEncodedState")
            if not state:
                return FlowResult(False, error="startFlow returned no serializedEncodedState",
                                  raw_responses=raw)

            # 3. Submit screen 1 (the actual input fields).
            fields = _build_screen1_fields(task)
            stepped = self.navigate_flow(state, action="NEXT", fields=fields)
            raw.append({"step": "navigateFlow:NEXT", "response": stepped})

            errors = stepped.get("errors")
            if errors:
                return FlowResult(False, error=f"Flow validation errors: {errors}",
                                  raw_responses=raw)

            task_id, success_msg = _extract_task_id(stepped)

            # If we got a success-screen markup (task_id extracted), the
            # interview is already done — but the browser still sends a final
            # navigateFlow to dismiss the success screen. If we didn't get a
            # task_id and the interview is still STARTED, the Flow added a new
            # required field on screen 1 and silently looped back — surface that
            # as a hard failure instead of pretending it succeeded.
            if not task_id and stepped.get("interviewStatus") == "STARTED":
                missing = [
                    f.get("name") or f.get("label")
                    for f in (stepped.get("fields") or [])
                    if f.get("isRequired") and f.get("value") in (None, "")
                    and f.get("fieldType") in ("INPUT", "DROPDOWN", "CHECKBOXES")
                ]
                hint = f" (likely missing required field(s): {missing})" if missing else ""
                return FlowResult(
                    False,
                    error=("Flow returned to screen 1 without creating the Task — "
                           "Salesforce probably added a new required field." + hint),
                    raw_responses=raw,
                )

            # 4. FINISH (or NEXT, depending on what the success screen allows)
            #    so the org closes the interview cleanly. Errors here are
            #    cosmetic — the Task already exists — so we log and move on.
            state2 = stepped.get("serializedEncodedState") or ""
            if state2:
                try:
                    finished = self.navigate_flow(state2, action="FINISH", fields=[])
                    raw.append({"step": "navigateFlow:FINISH", "response": finished})
                except FlowReplayError as e:
                    log.debug("FINISH not accepted (Task already created): %s", e)
                    raw.append({"step": "navigateFlow:FINISH", "error": str(e)})

            # 5. Optional post-create field updates (saveRecord) —
            #    standard Task fields the user wants to override.
            overrides = task.post_create_field_map()
            update_err = ""
            fields_written: dict[str, Any] = {}
            if task_id and overrides:
                try:
                    updated = self.update_task_fields(task_id, overrides)
                    raw.append({"step": "saveRecord", "response": updated})
                    fields_written = overrides
                except FlowReplayError as e:
                    update_err = str(e)

            return FlowResult(
                ok=True,
                task_id=task_id,
                message=success_msg or "Flow completed",
                fields_updated=fields_written,
                update_error=update_err,
                raw_responses=raw,
            )
        except FlowReplayError as e:
            return FlowResult(False, error=str(e), raw_responses=raw)


# -----------------------------------------------------------------------
# Exceptions and helpers
# -----------------------------------------------------------------------

class FlowReplayError(RuntimeError):
    """Raised when the Aura endpoint rejects our replay."""


# Screen-1 field ordering & names are copied verbatim from the recorded HAR.
# Salesforce expects every field — including hidden ones — present, even if
# they're null. Dropping any of them causes a 500.
_HIDDEN_LOOKUPS = (
    "Account_look_up",
    "Delivey_Project",      # note: Salesforce-side typo, kept as-is
    "DeliveryTask",
    "BS_account",
)
_LOOKUP_TO_FORM = {
    "Account":           "Account_look_up",
    "Delivery Project":  "Delivey_Project",       # Salesforce-side typo kept verbatim
    "Delivery Task":     "DeliveryTask",
    "Backstage Account": "BS_account",
}
_LOOKUP_TO_INPUT_ATTR = {
    "Account":           "account_id",
    "Delivery Project":  "delivery_project_id",
    "Delivery Task":     "delivery_task_id",
    "Backstage Account": "backstage_account_id",
}


def _build_screen1_fields(task: TaskInput) -> list[dict[str, Any]]:
    """Translate a TaskInput into the exact field list the Flow expects.

    The order and the presence of every (even hidden) field is significant —
    dropping any of them causes the Aura endpoint to 500.
    """
    is_internal = task.activity_type == "Internal PS work"
    impl_visible = not is_internal

    fields: list[dict[str, Any]] = [
        {"field": f"actvty.activity_type.{task.activity_type}.selected",
         "value": True, "isVisible": True},
        {"field": f"Implementation_Cmp.implemenetation_cmp_choice.{task.implementation_component}.selected",
         "value": True, "isVisible": impl_visible},
        # Custom_Non_Custom — legacy; visibilityRule fires only on a now-removed
        # activity_type value, so we always send it null/hidden.
        {"field": "Custom_Non_Custom", "value": None, "isVisible": False},
    ]

    if is_internal:
        fields.append({
            "field": "Internal_PS_Work_Type",
            "value": task.internal_ps_work_type or "Personal project",
            "isVisible": True,
        })
    else:
        fields.append({"field": "Internal_PS_Work_Type", "value": None, "isVisible": False})

    # NEW (Salesforce flow update ~May 2026): a separate required `Subject` field
    # was added on screen 1. Previously the Flow used `Description` as the
    # headline; now `Subject` is the headline and `Description` is the long text.
    # We populate both with the subject by default; the long description (if the
    # caller passed one) is force-set in phase 2 via saveRecord.
    fields.append({"field": "Subject", "value": task.subject, "isVisible": True})
    fields.append({"field": "Description", "value": task.subject, "isVisible": True})
    fields.append({
        "field": f"Revenue_Generating_Activity.revenue.{task.revenue_generating}.selected",
        "value": True, "isVisible": True,
    })
    # NEW (Jun 2026): required dropdown — Yes/No
    fields.append({
        "field": f"Is_Client_facing.isClientFacingPCKL.{task.is_client_facing}.selected",
        "value": True, "isVisible": True,
    })

    # Related-to picklist + the 4 associated lookup widgets.
    related_visible = bool(task.related_to)
    fields.append({
        "field": "Related_to_picklist",
        "value": task.related_to,
        "isVisible": related_visible,
    })
    for picklist_label, form_name in _LOOKUP_TO_FORM.items():
        is_active = task.related_to == picklist_label
        rec_id = None
        if is_active:
            attr = _LOOKUP_TO_INPUT_ATTR[picklist_label]
            rec_id = getattr(task, attr, None)
            if not rec_id:
                raise ValueError(
                    f"related_to={picklist_label!r} requires '{attr}' to be set."
                )
        fields.append({"field": f"{form_name}.recordId", "value": rec_id, "isVisible": is_active})
        fields.append({"field": f"{form_name}.recordIds",
                       "value": [rec_id] if rec_id else None, "isVisible": is_active})
        fields.append({"field": f"{form_name}.recordName", "value": None, "isVisible": is_active})

    fields.append({"field": "Time_Spent_in_Minutes",
                   "value": int(task.duration_minutes), "isVisible": True})
    return fields


_TASK_ID_RE = re.compile(r"/(00T[A-Za-z0-9]{12,15})")


def _extract_task_id(nav_response: dict[str, Any]) -> tuple[str | None, str]:
    """Pull the newly-created Task ID out of the success-screen markup.

    The Flow's success screen looks like::
        <p>The <a href="/00TRg00000u1LBuMAM" target="_blank">Task </a>
           was successfully created. </p>
    """
    for fld in nav_response.get("fields") or []:
        label = fld.get("label") or ""
        m = _TASK_ID_RE.search(label)
        if m:
            # Strip HTML tags from the message for readability.
            clean = re.sub(r"<[^>]+>", "", label).strip()
            return m.group(1), clean
    return None, ""


# -----------------------------------------------------------------------
# events.json helpers
# -----------------------------------------------------------------------

def load_events_json(path: str) -> list[TaskInput]:
    """Read a list of events from JSON and return TaskInput objects.

    Supported file shapes::
        # Either a top-level list:
        [{"summary": "...", "duration_minutes": 30}, ...]
        # Or wrapped in an object:
        {"events": [...]}
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "events" in raw:
        raw = raw["events"]
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON list (or {{'events': [...]}}).")
    tasks: list[TaskInput] = []
    for item in raw:
        try:
            tasks.append(TaskInput.from_event_json(item))
        except _SkipEvent as e:
            log.info("Skipping documentation row %r", str(e))
    return tasks
