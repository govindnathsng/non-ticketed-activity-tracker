"""Parse a Salesforce Lightning HAR file and extract everything needed to
replay the "New PS Task" flow programmatically.

This module powers Option 1 (print auth so the user can paste it into Postman)
and feeds the auth bundle to Option 2 (the replay client).

Why HAR? The Lightning Aura endpoint authenticates calls primarily via the
`aura.token` form field plus the `aura.context` envelope; both are short-lived
and bound to the user's session. The cleanest way for a user to "give" the
script their session is to export a HAR of a single successful action and
hand it over.

Note: Chrome's HAR export strips cookies and Set-Cookie headers. The Aura
endpoint generally accepts the form-field auth alone, but if your org enforces
a session cookie we also support pasting a raw `Cookie:` header string
alongside (see CLI `--cookie-string`).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# The aura endpoints we care about — the ones the Flow runtime uses to start
# and step through an interview.
_FLOW_ENDPOINT_MARKERS = (
    "FlowRuntimeConnect.navigateFlow",
    "FlowRuntimeConnect.startFlow",
    "FlowQuickActionRuntimeController",
)


@dataclass
class FlowAuth:
    """Everything required to replay a Lightning Flow as the captured user."""

    host: str                       # e.g. "taboola.lightning.force.com"
    aura_token: str                 # the `aura.token` form value (signed JWT)
    aura_context: str               # the `aura.context` form value (JSON blob)
    aura_page_uri: str              # the `aura.pageURI` form value
    quick_action_api_name: str      # e.g. "Delivery_Task__c.New_PS_Task"
    record_id: str                  # parent record id (e.g. "a2dRg000007hIthIAE")
    user_agent: str = ""            # echoed back so we look like the same browser
    referer: str = ""               # echoed back; some orgs check it
    cookies: dict[str, str] = field(default_factory=dict)  # usually empty (Chrome strips)

    # --- presentation helpers ---------------------------------------------

    def to_public_dict(self) -> dict[str, Any]:
        """Plain dict for printing / saving to JSON."""
        return asdict(self)

    def aura_url(self, action_query: str) -> str:
        """Build a full POST URL for a given action, e.g.
        'aura.FlowRuntimeConnect.startFlow=1'."""
        return f"https://{self.host}/aura?{action_query}"


def _decode_form_param(entry: dict[str, Any], name: str) -> str | None:
    """Return the URL-decoded value of a form param from a HAR entry, or None."""
    pd = entry.get("request", {}).get("postData", {}) or {}
    for prm in pd.get("params", []) or []:
        if prm.get("name") == name:
            return urllib.parse.unquote(prm.get("value", ""))
    # If params aren't itemised, parse the raw text body.
    text = pd.get("text") or ""
    if text:
        for part in text.split("&"):
            if part.startswith(name + "="):
                return urllib.parse.unquote(part[len(name) + 1:])
    return None


def _find_header(headers: list[dict[str, str]], name: str) -> str:
    name_lc = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == name_lc:
            return h.get("value", "")
    return ""


def _pick_flow_entry(har: dict[str, Any]) -> dict[str, Any]:
    """Pick the most informative flow-related POST in the HAR.

    Preference order:
      1. A `startFlow` call (gives us cleanest, freshest token).
      2. A `navigateFlow` call.
      3. A `getFlowInfo` call.
    """
    entries = har.get("log", {}).get("entries", []) or []

    def _matches(entry: dict[str, Any], needle: str) -> bool:
        return (
            entry.get("request", {}).get("method") == "POST"
            and needle in entry.get("request", {}).get("url", "")
        )

    for needle in ("startFlow", "navigateFlow", "FlowQuickActionRuntimeController"):
        for e in entries:
            if _matches(e, needle):
                return e

    raise ValueError(
        "Could not find a Salesforce Aura Flow POST in this HAR. "
        "Re-export the HAR while clicking 'New PS Task' (and submitting it)."
    )


def _extract_quick_action_and_record_id(entry: dict[str, Any]) -> tuple[str, str]:
    """Pull the quickActionApiName and recordId out of the action payload.

    Falls back to scraping them from the referer URL if the action body
    doesn't carry them (e.g. on `startFlow` calls).
    """
    msg_raw = _decode_form_param(entry, "message")
    qa, rec = "", ""
    if msg_raw:
        try:
            msg = json.loads(msg_raw)
            for act in msg.get("actions", []) or []:
                params = act.get("params") or {}
                qa = qa or params.get("quickActionApiName", "") or ""
                rec = rec or params.get("quickActionSubjectId", "") or ""
                # `startFlow` packs the recordId into `arguments`
                args_raw = params.get("arguments")
                if args_raw and not rec:
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        for a in args or []:
                            if a.get("name") == "recordId":
                                rec = rec or (a.get("value") or "")
                    except Exception:
                        pass
                # `navigateFlow` requests don't carry recordId; nothing to do.
                if qa and rec:
                    break
        except Exception:
            pass

    # Fall back to the referer / pageURI which both encode the parent.
    if not rec or not qa:
        referer = _find_header(entry["request"].get("headers", []), "referer")
        page_uri = _decode_form_param(entry, "aura.pageURI") or ""
        for src in (referer, page_uri):
            if not src:
                continue
            if not rec:
                m = re.search(r"recordId=([A-Za-z0-9]{15,18})", src)
                if m:
                    rec = m.group(1)
            if not qa:
                m = re.search(r"/action/quick/([A-Za-z0-9_\.]+)", src)
                if m:
                    qa = m.group(1)

    if not rec:
        raise ValueError("Could not determine recordId from HAR (missing in payload and referer).")
    if not qa:
        raise ValueError("Could not determine quickActionApiName from HAR.")
    return qa, rec


def extract_auth_from_curl(curl_path: str | Path) -> FlowAuth:
    """Parse a raw "Copy as cURL (bash)" dump and return a FlowAuth bundle.

    Easier than HAR exports because Chrome's "Copy as cURL" *includes* cookies
    and uses the most recent token. The user just right-clicks any aura POST
    in DevTools → Copy → Copy as cURL (bash) → saves to a .txt/.sh file.
    """
    path = Path(curl_path)
    raw = path.read_text(encoding="utf-8")

    # Decode bash $'...' escapes (only the ones cURL actually emits).
    text = raw.replace(r"\u0021", "!").replace("\\n", "\n").replace("\\t", "\t")

    # --- URL ----------------------------------------------------------
    m = re.search(r"curl\s+(?:--[a-zA-Z-]+\s+\S+\s+)*[\"']([^\"']+)[\"']", text)
    if not m:
        m = re.search(r"curl\s+(\S+)", text)
    if not m:
        raise ValueError(f"{path}: could not find request URL in cURL dump.")
    url = m.group(1)
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if not host:
        raise ValueError(f"{path}: cURL URL has no host: {url!r}")

    # --- headers (-H 'key: value') -----------------------------------
    headers: dict[str, str] = {}
    for hm in re.finditer(r"-H\s+\$?[\"']([^:]+):\s*([^\"']*)[\"']", text):
        headers[hm.group(1).strip().lower()] = hm.group(2).strip()

    user_agent = headers.get("user-agent", "")
    referer = headers.get("referer", "")

    # --- cookies (-b '...' or --cookie '...') ------------------------
    cookies: dict[str, str] = {}
    cookie_match = re.search(r"(?:-b|--cookie)\s+\$?[\"']([^\"']+)[\"']", text)
    if cookie_match:
        for piece in cookie_match.group(1).split(";"):
            piece = piece.strip()
            if "=" in piece:
                k, v = piece.split("=", 1)
                cookies[k.strip()] = v.strip()

    # --- body (--data-raw / --data / -d) ------------------------------
    body_match = re.search(
        r"(?:--data-raw|--data|--data-binary|-d)\s+\$?[\"']([^\"']*)[\"']", text
    )
    if not body_match:
        raise ValueError(f"{path}: no request body found in cURL dump.")
    body = body_match.group(1)

    params: dict[str, str] = {}
    for part in body.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = urllib.parse.unquote_plus(v)

    aura_token = params.get("aura.token")
    aura_context = params.get("aura.context")
    aura_page_uri = params.get("aura.pageURI", "")
    if not aura_token or not aura_context:
        raise ValueError(
            f"{path}: cURL body missing aura.token / aura.context. "
            "Make sure you copied an /aura POST from DevTools."
        )

    # Derive recordId + quickActionApiName from referer/pageURI/message body.
    record_id = ""
    quick_action = ""
    for src in (params.get("message", ""), aura_page_uri, referer):
        if not src:
            continue
        if not record_id:
            rm = re.search(r"recordId(?:%3D|=)([A-Za-z0-9]{15,18})", src)
            if rm:
                record_id = rm.group(1)
            else:
                rm = re.search(r"/r/([A-Za-z0-9_]+)/([A-Za-z0-9]{15,18})/view", src)
                if rm:
                    record_id = rm.group(2)
        if not quick_action:
            qm = re.search(r"/action/quick/([A-Za-z0-9_\.]+)", src)
            if qm:
                quick_action = qm.group(1)
            else:
                qm = re.search(r"/r/([A-Za-z0-9_]+)/[A-Za-z0-9]{15,18}/view", src)
                if qm and not quick_action:
                    quick_action = f"{qm.group(1)}.New_PS_Task"

    if not record_id:
        raise ValueError(
            f"{path}: could not infer parent recordId. Make sure the cURL came "
            "from a request issued while viewing the Delivery Task page."
        )
    if not quick_action:
        quick_action = "Delivery_Task__c.New_PS_Task"

    return FlowAuth(
        host=host,
        aura_token=aura_token,
        aura_context=aura_context,
        aura_page_uri=aura_page_uri,
        quick_action_api_name=quick_action,
        record_id=record_id,
        user_agent=user_agent,
        referer=referer,
        cookies=cookies,
    )


def load_auth(source_path: str | Path) -> FlowAuth:
    """Auto-detect HAR vs cURL based on file extension or contents."""
    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix == ".har":
        return extract_auth(path)
    if suffix in (".sh", ".curl", ".txt"):
        return extract_auth_from_curl(path)
    # Fallback: sniff the first 200 bytes.
    head = path.read_text(encoding="utf-8", errors="ignore")[:200].lstrip()
    if head.startswith("{"):
        return extract_auth(path)
    if head.startswith("curl"):
        return extract_auth_from_curl(path)
    raise ValueError(
        f"Cannot tell whether {path} is a HAR or a cURL dump. "
        "Use .har / .sh / .curl extension to disambiguate."
    )


def extract_auth(har_path: str | Path) -> FlowAuth:
    """Load a HAR file and return a FlowAuth bundle ready for replay."""
    har_path = Path(har_path)
    with har_path.open("r", encoding="utf-8") as f:
        har = json.load(f)

    entry = _pick_flow_entry(har)

    token = _decode_form_param(entry, "aura.token")
    context = _decode_form_param(entry, "aura.context")
    page_uri = _decode_form_param(entry, "aura.pageURI") or ""
    if not token or not context:
        raise ValueError(
            "HAR entry is missing `aura.token` or `aura.context` form fields. "
            "Make sure you exported a HAR of a Lightning Flow action."
        )

    headers = entry["request"].get("headers", []) or []
    authority = _find_header(headers, ":authority") or _find_header(headers, "host")
    if not authority:
        # Fall back to the URL host.
        from urllib.parse import urlparse
        authority = urlparse(entry["request"]["url"]).hostname or ""

    quick_action, record_id = _extract_quick_action_and_record_id(entry)

    cookies = {c.get("name", ""): c.get("value", "") for c in entry["request"].get("cookies", []) or []}
    cookies = {k: v for k, v in cookies.items() if k}

    return FlowAuth(
        host=authority,
        aura_token=token,
        aura_context=context,
        aura_page_uri=page_uri,
        quick_action_api_name=quick_action,
        record_id=record_id,
        user_agent=_find_header(headers, "user-agent"),
        referer=_find_header(headers, "referer"),
        cookies=cookies,
    )


# ----------------------------------------------------------------------
# Postman / cURL helpers (Option 1 output)
# ----------------------------------------------------------------------

def build_curl(auth: FlowAuth, sample_message: str | None = None) -> str:
    """Return a copy-pasteable cURL hitting `startFlow` with this auth.

    Handy for the user to verify their token works in Postman before running
    Option 2 (the full automation).
    """
    url = auth.aura_url("r=99&aura.FlowRuntimeConnect.startFlow=1")
    message = sample_message or json.dumps({
        "actions": [{
            "id": "1;a",
            "descriptor": "aura://FlowRuntimeConnectController/ACTION$startFlow",
            "callingDescriptor": "UNKNOWN",
            "params": {
                "flowDevName": auth.quick_action_api_name.split(".")[-1],
                "arguments": json.dumps([
                    {"name": "recordId", "type": "String", "value": auth.record_id}
                ]),
                "enableTrace": False,
                "enableRollbackMode": False,
                "debugAsUserId": "",
                "useLatestSubflow": False,
                "isBuilderDebug": False,
            },
        }]
    })

    def _q(s: str) -> str:
        return urllib.parse.quote(s, safe="")

    body = (
        f"message={_q(message)}"
        f"&aura.context={_q(auth.aura_context)}"
        f"&aura.pageURI={_q(auth.aura_page_uri)}"
        f"&aura.token={_q(auth.aura_token)}"
    )
    cookie_header = ""
    if auth.cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookies.items())
        cookie_header = f"  -H 'Cookie: {cookie_str}' \\\n"

    return (
        f"curl '{url}' \\\n"
        f"  -X POST \\\n"
        f"  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \\\n"
        f"  -H 'Accept: */*' \\\n"
        f"  -H 'Origin: https://{auth.host}' \\\n"
        f"  -H 'Referer: {auth.referer or auth.aura_url('')}' \\\n"
        f"  -H 'User-Agent: {auth.user_agent or 'Mozilla/5.0'}' \\\n"
        f"{cookie_header}"
        f"  --data-raw '{body}'"
    )
