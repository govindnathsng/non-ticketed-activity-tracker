#!/usr/bin/env python3
"""Web UI for the Non-Ticketed Activity Tracker.

Run:
    source .venv/bin/activate
    python server.py

Then open http://localhost:5055 in Chrome.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from flask import Flask, Response, jsonify, request, stream_with_context

BASE = Path(__file__).parent
ACTIVITY_TRACKER = BASE / ".venv" / "bin" / "activity-tracker"
EVENTS_JSON = BASE / "events.json"
SESSION_CURL = BASE / "session.curl.sh"
RAW_CALENDAR = BASE / "raw-calendar.json"

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\[[\d;]*[A-Za-z]|\r')


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


app = Flask(__name__)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/events")
def get_events():
    if not EVENTS_JSON.exists():
        return jsonify([])
    return jsonify(json.loads(EVENTS_JSON.read_text(encoding="utf-8")))


@app.route("/api/events/<int:index>", methods=["DELETE"])
def delete_event(index: int):
    if not EVENTS_JSON.exists():
        return jsonify({"error": "events.json not found"}), 404
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    if index < 0 or index >= len(events):
        return jsonify({"error": "index out of range"}), 400
    removed = events.pop(index)
    EVENTS_JSON.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"removed": removed, "remaining": len(events)})


@app.route("/api/import-calendar", methods=["POST"])
def import_calendar():
    data = request.get_json(force=True)
    raw_text = (data or {}).get("json", "").strip()
    if not raw_text:
        return jsonify({"ok": False, "error": "No JSON provided."})
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return jsonify({"ok": False, "error": f"Invalid JSON: {e}"})
    if not isinstance(parsed, list):
        return jsonify({"ok": False, "error": "Expected a JSON array ([ ... ]) at the top level."})
    if len(parsed) == 0:
        return jsonify({"ok": False, "error": "The array is empty — nothing to import."})

    normalized = []
    for item in parsed:
        entry: dict = {}

        # ── Title ──────────────────────────────────────────────────────
        entry["title"] = (
            item.get("title") or item.get("summary") or item.get("subject") or ""
        )

        # ── Start / end / date ─────────────────────────────────────────
        # Accept both Google Calendar shape  (start / end)
        # and legacy shape (start_time / end_time + date).
        start_raw = item.get("start_time") or item.get("start") or ""
        end_raw   = item.get("end_time")   or item.get("end")   or ""
        # Derive the calendar date: prefer an explicit "date" field, fall back
        # to the first 10 chars of the start timestamp (works for both
        # "2026-07-09" and "2026-07-09T15:10:00+05:30").
        date_raw  = item.get("date") or (start_raw[:10] if start_raw else "")
        entry["date"]       = date_raw
        entry["start_time"] = start_raw
        entry["end_time"]   = end_raw

        # ── Participants ───────────────────────────────────────────────
        # Google Calendar exports attendees as a flat list of strings or
        # dicts; the legacy shape uses "participants" (list of strings or
        # {"name": …, "email": …} dicts).  Normalise to a plain list.
        raw_participants = item.get("participants") or item.get("attendees") or []
        entry["participants"] = raw_participants

        # ── Calendar description → Task description ────────────────────
        # If the calendar event carries a description, pass it through so
        # the CLI can append it to the long-form Task.Description field.
        cal_desc = item.get("description")
        if cal_desc and cal_desc != entry["title"]:
            entry["description"] = cal_desc

        # ── Copy any remaining fields the caller may have added ────────
        for k, v in item.items():
            if k not in entry:
                entry[k] = v

        normalized.append(entry)

    RAW_CALENDAR.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    result = subprocess.run(
        [str(ACTIVITY_TRACKER), "convert-calendar",
         "--input", str(RAW_CALENDAR), "--out", str(EVENTS_JSON)],
        capture_output=True, text=True,
    )
    output = strip_ansi(result.stdout + result.stderr)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": output.strip()})

    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    converted = re.search(r"wrote (\d+) events", output)
    skipped   = re.search(r"Skipped (\d+)", output)
    return jsonify({
        "ok": True,
        "total_input": len(normalized),
        "converted":   int(converted.group(1)) if converted else len(events),
        "skipped":     int(skipped.group(1))   if skipped   else 0,
        "output":      output.strip(),
    })


@app.route("/api/session-update", methods=["POST"])
def session_update():
    data = request.get_json(force=True)
    curl_text = (data or {}).get("curl", "").strip()
    if not curl_text:
        return jsonify({"ok": False, "error": "No curl content provided."})
    if "taboola.lightning.force.com" not in curl_text:
        return jsonify({"ok": False, "error": "This doesn't look like a Taboola Salesforce cURL. Make sure you copied from taboola.lightning.force.com/aura?r=\u2026 not a CDN or static URL."})
    if not curl_text.startswith("curl "):
        return jsonify({"ok": False, "error": "Content must start with 'curl '. Use Chrome DevTools \u2192 right-click request \u2192 Copy as cURL (bash)."})
    SESSION_CURL.write_text(curl_text, encoding="utf-8")
    result = subprocess.run(
        [str(ACTIVITY_TRACKER), "extract-auth", "--curl", str(SESSION_CURL), "--no-show-curl"],
        capture_output=True, text=True,
    )
    output = strip_ansi(result.stdout + result.stderr)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": output.strip()})
    host      = re.search(r"host\s+:\s+(\S+)", output)
    record_id = re.search(r"recordId \(parent\)\s+:\s+(\S+)", output)
    cookies   = re.search(r"cookies \((\d+) captured\)", output)
    return jsonify({
        "ok": True,
        "host":      host.group(1)         if host      else "?",
        "record_id": record_id.group(1)    if record_id else "?",
        "cookies":   int(cookies.group(1)) if cookies   else 0,
    })


@app.route("/api/session-check")
def session_check():
    if not SESSION_CURL.exists():
        return jsonify({"ok": False, "error": "session.curl.sh not found"})
    result = subprocess.run(
        [str(ACTIVITY_TRACKER), "extract-auth", "--curl", str(SESSION_CURL), "--no-show-curl"],
        capture_output=True, text=True,
    )
    output = strip_ansi(result.stdout + result.stderr)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": output.strip()})
    host      = re.search(r"host\s+:\s+(\S+)", output)
    record_id = re.search(r"recordId \(parent\)\s+:\s+(\S+)", output)
    cookies   = re.search(r"cookies \((\d+) captured\)", output)
    return jsonify({
        "ok": True,
        "host":      host.group(1)         if host      else "?",
        "record_id": record_id.group(1)    if record_id else "?",
        "cookies":   int(cookies.group(1)) if cookies   else 0,
    })


@app.route("/api/run/<action>")
def run_action(action: str):
    if action not in ("dry-run", "smoke", "push"):
        return jsonify({"error": "unknown action"}), 400

    cmd = [
        str(ACTIVITY_TRACKER), "flow-create",
        "--curl", str(SESSION_CURL),
        "--input", str(EVENTS_JSON),
    ]
    if action == "dry-run":
        cmd.append("--dry-run")
    elif action == "smoke":
        cmd += ["--limit", "1"]

    def generate():
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout
        for line in proc.stdout:
            clean = strip_ansi(line)
            yield f"data: {json.dumps(clean)}\n\n"
        proc.wait()
        yield f"data: {json.dumps('__EXIT__:' + str(proc.returncode))}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Frontend — single HTML page
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Activity Tracker — Taboola</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --blue:          #3C83F6;
  --blue-dark:     #2563EB;
  --blue-dim:      #EFF6FF;
  --blue-border:   #BFDBFE;
  --page-bg:       #F3F4F6;
  --surface:       #FFFFFF;
  --border:        #E5E7EB;
  --border-strong: #D1D5DB;
  --text:          #111827;
  --text-2:        #374151;
  --muted:         #6B7280;
  --muted-light:   #9CA3AF;
  --green:         #059669;
  --green-bg:      #ECFDF5;
  --green-border:  #6EE7B7;
  --red:           #DC2626;
  --red-bg:        #FEF2F2;
  --red-border:    #FECACA;
  --amber:         #D97706;
  --amber-bg:      #FFFBEB;
  --amber-border:  #FDE68A;
  --radius:        8px;
  --radius-lg:     12px;
  --shadow-sm:     0 1px 2px rgba(0,0,0,.06), 0 1px 3px rgba(0,0,0,.08);
  --shadow:        0 4px 6px rgba(0,0,0,.05), 0 2px 4px rgba(0,0,0,.04);
  --shadow-lg:     0 10px 24px rgba(0,0,0,.10), 0 4px 8px rgba(0,0,0,.06);
}

body {
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: var(--text);
  background: var(--page-bg);
  min-height: 100vh;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 3px; }

/* ── Top nav ── */
.topnav {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: 0;
  z-index: 50;
}
.topnav-inner {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.nav-logo {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
}
.nav-logo img { width: 34px; height: 34px; display: block; object-fit: cover; }
.nav-divider {
  width: 1px;
  height: 22px;
  background: var(--border);
}
.nav-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
}
.nav-team {
  font-size: 11px;
  font-weight: 600;
  color: var(--blue);
  background: var(--blue-dim);
  border: 1px solid var(--blue-border);
  border-radius: 20px;
  padding: 2px 9px;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

/* ── Page layout ── */
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 28px 24px 48px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Card ── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.card-body { padding: 20px; }
.card-body-flush { padding: 0; }

/* ── Session card ── */
.session-layout {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px 20px;
}
.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot.green  { background: var(--green); box-shadow: 0 0 0 3px #d1fae5; }
.status-dot.red    { background: var(--red);   box-shadow: 0 0 0 3px #fee2e2; }
.status-dot.amber  { background: var(--amber); box-shadow: 0 0 0 3px #fef3c7; }
.status-label {
  font-size: 13px;
  font-weight: 600;
}
.status-label.green { color: var(--green); }
.status-label.red   { color: var(--red);   }
.status-label.amber { color: var(--amber); }

.session-sep {
  width: 1px;
  height: 20px;
  background: var(--border);
  flex-shrink: 0;
}
.session-meta {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  flex: 1;
}
.meta-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.meta-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted-light);
}
.meta-value {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-2);
  font-family: "SF Mono", "Cascadia Code", monospace;
}
.session-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* ── Stats row ── */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
@media (max-width: 640px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } }
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--blue);
  line-height: 1;
  letter-spacing: -0.02em;
}
.stat-label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
}

/* ── Table ── */
.table-scroll {
  overflow-x: auto;
  max-height: 340px;
  overflow-y: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
thead th {
  background: #F9FAFB;
  padding: 10px 16px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  white-space: nowrap;
}
tbody tr {
  border-bottom: 1px solid var(--border);
  transition: background 0.1s;
}
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: #F9FAFB; }
tbody td {
  padding: 11px 16px;
  vertical-align: middle;
  color: var(--text-2);
}
.td-num { color: var(--muted-light); font-size: 12px; width: 40px; }
.td-date { color: var(--muted); font-size: 12px; white-space: nowrap; font-family: "SF Mono", monospace; }
.td-subject { font-weight: 500; color: var(--text); }
.td-min { text-align: right; font-variant-numeric: tabular-nums; color: var(--text-2); font-size: 12px; }
.td-action { width: 80px; text-align: right; }

/* ── Badges / Pills ── */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.badge-green  { background: var(--green-bg);  color: var(--green);  border: 1px solid var(--green-border); }
.badge-red    { background: var(--red-bg);    color: var(--red);    border: 1px solid var(--red-border);   }
.badge-blue   { background: var(--blue-dim);  color: var(--blue);   border: 1px solid var(--blue-border);  }
.badge-amber  { background: var(--amber-bg);  color: var(--amber);  border: 1px solid var(--amber-border); }
.badge-gray   { background: #F3F4F6;          color: var(--muted);  border: 1px solid var(--border);       }

/* ── Buttons ── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s, opacity 0.15s, transform 0.1s;
  border: 1px solid transparent;
  white-space: nowrap;
  text-decoration: none;
}
.btn:hover:not(:disabled) { transform: translateY(-1px); }
.btn:active:not(:disabled) { transform: none; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; transform: none !important; }

.btn-sm   { padding: 6px 12px; font-size: 12px; }
.btn-md   { padding: 9px 16px; }
.btn-lg   { padding: 11px 22px; font-size: 14px; }

.btn-primary   { background: var(--blue); color: #fff; border-color: var(--blue); }
.btn-primary:hover:not(:disabled) { background: var(--blue-dark); border-color: var(--blue-dark); }

.btn-outline   { background: var(--surface); color: var(--blue); border-color: var(--blue-border); }
.btn-outline:hover:not(:disabled) { background: var(--blue-dim); border-color: var(--blue); }

.btn-ghost     { background: transparent; color: var(--muted); border-color: var(--border); }
.btn-ghost:hover:not(:disabled) { background: #F3F4F6; color: var(--text-2); }

.btn-danger-ghost { background: transparent; color: var(--muted-light); border-color: transparent; }
.btn-danger-ghost:hover:not(:disabled) { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }

.btn-amber-outline { background: var(--surface); color: var(--amber); border-color: var(--amber-border); }
.btn-amber-outline:hover:not(:disabled) { background: var(--amber-bg); border-color: var(--amber); }

/* ── Action row ── */
.action-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
@media (max-width: 600px) { .action-row { grid-template-columns: 1fr; } }
.action-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.action-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.action-card-desc {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
  flex: 1;
}
.action-card .btn { width: 100%; margin-top: 10px; }

/* ── Console ── */
.console-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  background: #F9FAFB;
}
.console-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.console-dots {
  display: flex;
  gap: 5px;
}
.console-dots span {
  width: 11px;
  height: 11px;
  border-radius: 50%;
}
.d1 { background: #FC5F5A; }
.d2 { background: #FDBC40; }
.d3 { background: #34C749; }
.console-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
#console-body {
  font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace;
  font-size: 12px;
  line-height: 1.7;
  color: #94A3B8;
  background: #0F172A;
  padding: 16px 20px;
  min-height: 180px;
  max-height: 360px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
#console-body .lo  { color: #22C55E; }
#console-body .le  { color: #F87171; }
#console-body .lh  { color: #E2E8F0; font-weight: 600; }
#console-body .ld  { color: #475569; }

.result-bar {
  display: none;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  font-size: 13px;
  font-weight: 600;
  border-top: 1px solid var(--border);
}
.result-bar.ok  { display: flex; background: var(--green-bg);  color: var(--green); }
.result-bar.err { display: flex; background: var(--red-bg);    color: var(--red);   }

/* ── Spinner ── */
.spinner {
  width: 13px; height: 13px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.55s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Modal ── */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(17, 24, 39, 0.5);
  backdrop-filter: blur(2px);
  z-index: 200;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  width: min(560px, 100%);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
  overflow: hidden;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  background: #F9FAFB;
  flex-shrink: 0;
}
.modal-head-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
}
.modal-head-icon {
  width: 28px; height: 28px;
  background: var(--blue-dim);
  border: 1px solid var(--blue-border);
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: var(--blue);
  font-size: 13px;
}
.modal-close {
  width: 28px; height: 28px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 15px;
  transition: background 0.1s, color 0.1s;
}
.modal-close:hover { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }
.modal-body {
  padding: 20px;
  overflow-y: auto;
}
.modal-steps {
  background: var(--blue-dim);
  border: 1px solid var(--blue-border);
  border-radius: var(--radius);
  padding: 12px 14px;
  margin-bottom: 14px;
}
.modal-steps ol {
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.modal-steps li {
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.6;
}
.modal-steps li b { color: var(--text); }
.modal-hint {
  background: #FFFBEB;
  border: 1px solid var(--amber-border);
  border-radius: var(--radius);
  padding: 10px 14px;
  margin-bottom: 14px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.65;
}
.modal-hint b  { color: var(--text); }
.modal-hint code {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: "SF Mono", monospace;
  font-size: 11px;
  color: var(--text-2);
}
.modal-textarea {
  width: 100%;
  height: 130px;
  background: #0F172A;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  color: #E2E8F0;
  font-family: "SF Mono", "Cascadia Code", monospace;
  font-size: 11.5px;
  padding: 10px 12px;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
  line-height: 1.6;
}
.modal-textarea:focus { border-color: var(--blue); }
.modal-textarea::placeholder { color: #475569; }
.modal-result {
  margin-top: 12px;
  padding: 11px 14px;
  border-radius: var(--radius);
  font-size: 12.5px;
  font-weight: 500;
  display: none;
}
.modal-result.ok  { background: var(--green-bg); border: 1px solid var(--green-border); color: var(--green); display: block; }
.modal-result.err { background: var(--red-bg);   border: 1px solid var(--red-border);   color: var(--red);   display: block; }
.modal-result .res-meta { font-size: 11px; font-weight: 400; color: var(--muted); margin-top: 5px; }
.modal-result .res-meta b { color: var(--text-2); }
.modal-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
  background: #F9FAFB;
  flex-shrink: 0;
}
</style>
</head>
<body>

<!-- Top navigation -->
<nav class="topnav">
  <div class="topnav-inner">
    <div class="nav-logo">
      <img src="/static/logo.png" alt="Taboola">
    </div>
    <span class="nav-title">Non Ticketed Activity Tracker</span>
    <div class="nav-divider"></div>
    <span class="nav-team">Pub PS</span>
  </div>
</nav>

<div class="page">

  <!-- Session status -->
  <div class="card">
    <div class="card-header">
      <span class="card-title">Salesforce Session</span>
    </div>
    <div class="session-layout">
      <div class="status-indicator">
        <div class="status-dot amber" id="status-dot"></div>
        <span class="status-label amber" id="status-label">Checking…</span>
      </div>
      <div class="session-sep"></div>
      <div class="session-meta" id="session-meta">
        <div class="meta-item"><div class="meta-label">Host</div><div class="meta-value" id="meta-host">—</div></div>
        <div class="meta-item"><div class="meta-label">Parent Record</div><div class="meta-value" id="meta-record">—</div></div>
        <div class="meta-item"><div class="meta-label">Cookies</div><div class="meta-value" id="meta-cookies">—</div></div>
      </div>
      <div class="session-actions">
        <button class="btn btn-sm btn-ghost" onclick="checkSession()">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M10.5 6A4.5 4.5 0 1 1 2.25 3.75M1.5 1.5v2.25H3.75" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Re-check
        </button>
        <button class="btn btn-sm btn-outline" onclick="openSessionModal()">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="2.5" width="9" height="7" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 5h5M3.5 7h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          Update Session
        </button>
      </div>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value" id="stat-total">—</div>
      <div class="stat-label">Total events</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="stat-client">—</div>
      <div class="stat-label">Client-facing</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="stat-mins">—</div>
      <div class="stat-label">Total minutes</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="stat-days">—</div>
      <div class="stat-label">Days covered</div>
    </div>
  </div>

  <!-- Events table -->
  <div class="card">
    <div class="card-header">
      <span class="card-title">Event List</span>
      <button class="btn btn-sm btn-outline" onclick="openCalendarModal()">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><rect x="1.5" y="2" width="9" height="9" rx="1" stroke="currentColor" stroke-width="1.3"/><path d="M4 1v2M8 1v2M1.5 5h9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
        Import Calendar JSON
      </button>
    </div>
    <div class="card-body-flush">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th class="td-num">#</th>
              <th>Date</th>
              <th>Subject</th>
              <th style="text-align:right">Min</th>
              <th>Impl Component</th>
              <th>Client-facing</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="events-body">
            <tr><td colspan="7" style="text-align:center;color:var(--muted);padding:28px 0;font-size:13px">Loading events…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Actions -->
  <div class="action-row">
    <div class="action-card">
      <div class="action-card-title">Preview</div>
      <div class="action-card-desc">Simulate the push and see exactly which tasks would be created — nothing is sent to Salesforce.</div>
      <button class="btn btn-md btn-outline" id="btn-dry" onclick="runAction('dry-run')">Run Dry Run</button>
    </div>
    <div class="action-card">
      <div class="action-card-title">Smoke Test</div>
      <div class="action-card-desc">Send only the first event to Salesforce. Verify it appears correctly before pushing everything.</div>
      <button class="btn btn-md btn-amber-outline" id="btn-smoke" onclick="runAction('smoke')">Send 1 Task</button>
    </div>
    <div class="action-card">
      <div class="action-card-title">Push All</div>
      <div class="action-card-desc">Create all events listed above as PS Tasks in Salesforce, one after another.</div>
      <button class="btn btn-md btn-primary" id="btn-push" onclick="runAction('push')">Push All Tasks</button>
    </div>
  </div>

  <!-- Console output -->
  <div class="console-card">
    <div class="console-header">
      <div class="console-header-left">
        <div class="console-dots"><span class="d1"></span><span class="d2"></span><span class="d3"></span></div>
        <span class="console-label" id="console-title">Output</span>
      </div>
      <button class="btn btn-sm btn-ghost" onclick="clearConsole()">Clear</button>
    </div>
    <div id="console-body"><span class="ld">Click an action above to begin…</span></div>
    <div class="result-bar" id="result-banner"></div>
  </div>

</div><!-- /page -->

<!-- ── Calendar import modal ────────────────────────────────────────────── -->
<div class="modal-overlay" id="calendar-modal" onclick="closeCalendarOnBackdrop(event)">
  <div class="modal">
    <div class="modal-head">
      <div class="modal-head-title">
        <div class="modal-head-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1.5" y="2.5" width="11" height="10" rx="1.5" stroke="currentColor" stroke-width="1.4"/><path d="M4.5 1v3M9.5 1v3M1.5 6h11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </div>
        Import Calendar JSON
      </div>
      <button class="modal-close" onclick="closeCalendarModal()">&#x2715;</button>
    </div>
    <div class="modal-body">
      <div class="modal-hint">
        Paste a JSON array of meetings. Each item needs at minimum a <b>title</b> (or <code>summary</code>), <b>start</b> time, and <b>end</b> time.<br><br>
        Accepted: <code>{"summary": "Call", "start": "2026-07-07T10:00:00+05:30", "end": "..."}</code><br>
        or: <code>{"title": "Call", "start_time": "...", "end_time": "...", "date": "2026-07-07"}</code><br><br>
        <b>This will replace your current event list.</b>
      </div>
      <textarea class="modal-textarea" id="calendar-input" placeholder='[&#10;  {&#10;    "summary": "Client call",&#10;    "start": "2026-07-07T10:00:00+05:30",&#10;    "end":   "2026-07-07T11:00:00+05:30"&#10;  }&#10;]'></textarea>
      <div class="modal-result" id="calendar-result"></div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-md btn-primary" id="btn-import" onclick="importCalendar()">
        <span id="import-icon">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M6.5 1v8M3 6l3.5 3.5L10 6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M1.5 11h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </span>
        Import &amp; Convert
      </button>
      <button class="btn btn-md btn-ghost" onclick="closeCalendarModal()">Cancel</button>
    </div>
  </div>
</div>

<!-- ── Session update modal ──────────────────────────────────────────────── -->
<div class="modal-overlay" id="session-modal" onclick="closeOnBackdrop(event)">
  <div class="modal">
    <div class="modal-head">
      <div class="modal-head-title">
        <div class="modal-head-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.4"/><path d="M7 4.5v3l1.5 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </div>
        Update Salesforce Session
      </div>
      <button class="modal-close" onclick="closeSessionModal()">&#x2715;</button>
    </div>
    <div class="modal-body">
      <div class="modal-steps">
        <ol>
          <li>Open Chrome and go to your <b>Non-Ticketed Activities Delivery Task</b> in Salesforce</li>
          <li>Press <b>F12</b> &rarr; <b>Network</b> tab &rarr; tick <b>Preserve log</b> &rarr; filter by <b>aura</b></li>
          <li>Click anywhere on the Salesforce page to trigger a network request</li>
          <li>Find the request starting with <b>aura?r=</b> under <b>taboola.lightning.force.com</b></li>
          <li>Right-click it &rarr; <b>Copy &rarr; Copy as cURL (bash)</b></li>
          <li>Paste below and click <b>Save &amp; Verify</b></li>
        </ol>
      </div>
      <textarea class="modal-textarea" id="curl-input" placeholder="curl 'https://taboola.lightning.force.com/aura?r=...' \&#10;  -H 'accept: */*' \&#10;  -b 'sid=...' \&#10;  --data-raw '...'"></textarea>
      <div class="modal-result" id="modal-result"></div>
    </div>
    <div class="modal-foot">
      <button class="btn btn-md btn-primary" id="btn-save-session" onclick="saveSession()">
        <span id="save-icon">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M2 7l3.5 3.5L11 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </span>
        Save &amp; Verify
      </button>
      <button class="btn btn-md btn-ghost" onclick="closeSessionModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
let activeSource = null;

// ── Load events ───────────────────────────────────────────────────────────────
async function loadEvents() {
  const res    = await fetch('/api/events');
  const events = await res.json();
  const tbody  = document.getElementById('events-body');

  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:28px 0;font-size:13px">No events — import a calendar or add events to events.json.</td></tr>';
    ['stat-total','stat-client','stat-mins','stat-days'].forEach(id => document.getElementById(id).textContent = '0');
    return;
  }

  const clientCount = events.filter(e => e.is_client_facing === 'Yes').length;
  const totalMins   = events.reduce((s,e) => s + (e.duration_minutes || e.time_spent_in_minutes || 0), 0);
  const days        = new Set(events.map(e => e.activity_date)).size;
  document.getElementById('stat-total').textContent  = events.length;
  document.getElementById('stat-client').textContent = clientCount;
  document.getElementById('stat-mins').textContent   = totalMins;
  document.getElementById('stat-days').textContent   = days;

  tbody.innerHTML = events.map((e, i) => {
    const impl = e.implementation_component || 'None';
    const implBadge = impl !== 'None'
      ? `<span class="badge badge-blue">${impl}</span>`
      : `<span class="badge badge-gray">None</span>`;
    const clientBadge = e.is_client_facing === 'Yes'
      ? `<span class="badge badge-green">Yes</span>`
      : `<span class="badge badge-gray">No</span>`;
    const mins = e.duration_minutes || e.time_spent_in_minutes || '—';
    return `<tr data-index="${i}">
      <td class="td-num">${i + 1}</td>
      <td class="td-date">${e.activity_date || '—'}</td>
      <td class="td-subject">${e.subject || '—'}</td>
      <td class="td-min">${mins}</td>
      <td>${implBadge}</td>
      <td>${clientBadge}</td>
      <td class="td-action">
        <button class="btn btn-sm btn-danger-ghost" onclick="deleteEvent(${i}, this)" title="Remove event">
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M1.5 1.5l8 8M9.5 1.5l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
          Remove
        </button>
      </td>
    </tr>`;
  }).join('');
}

// ── Delete event ──────────────────────────────────────────────────────────────
async function deleteEvent(index, btn) {
  const row = btn.closest('tr');
  row.style.opacity = '0.4';
  row.style.pointerEvents = 'none';
  const res = await fetch(`/api/events/${index}`, { method: 'DELETE' });
  if (res.ok) {
    await loadEvents();
  } else {
    row.style.opacity = '';
    row.style.pointerEvents = '';
    alert('Could not delete event.');
  }
}

// ── Session check ─────────────────────────────────────────────────────────────
async function checkSession() {
  setSessionUI('checking');
  const res  = await fetch('/api/session-check');
  const data = await res.json();
  if (data.ok) {
    setSessionUI('ok', data);
  } else {
    setSessionUI('err');
  }
}

function setSessionUI(state, data) {
  const dot   = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const host  = document.getElementById('meta-host');
  const rec   = document.getElementById('meta-record');
  const cook  = document.getElementById('meta-cookies');

  dot.className   = 'status-dot';
  label.className = 'status-label';

  if (state === 'checking') {
    dot.classList.add('amber'); label.classList.add('amber');
    label.textContent = 'Checking…';
    host.textContent = rec.textContent = cook.textContent = '…';
  } else if (state === 'ok') {
    dot.classList.add('green'); label.classList.add('green');
    label.textContent = 'Connected';
    host.textContent  = data.host;
    rec.textContent   = data.record_id;
    cook.textContent  = data.cookies + ' captured';
  } else {
    dot.classList.add('red'); label.classList.add('red');
    label.textContent = 'Session expired';
    host.textContent = rec.textContent = cook.textContent = '—';
  }
}

// ── Run action (SSE) ──────────────────────────────────────────────────────────
function runAction(action) {
  if (activeSource) { activeSource.close(); activeSource = null; }

  const titles = { 'dry-run': 'Dry Run Preview', 'smoke': 'Smoke Test — 1 Task', 'push': 'Push All Tasks' };
  document.getElementById('console-title').textContent = titles[action] || action;
  document.getElementById('result-banner').className = 'result-bar';

  const body = document.getElementById('console-body');
  body.innerHTML = '';

  const btnIds = { 'dry-run': 'btn-dry', 'smoke': 'btn-smoke', 'push': 'btn-push' };
  ['btn-dry','btn-smoke','btn-push'].forEach(id => {
    const b = document.getElementById(id);
    b.disabled = true;
    if (!b._orig) b._orig = b.innerHTML;
  });
  const ab = document.getElementById(btnIds[action]);
  ab.innerHTML = '<span class="spinner"></span> Running…';

  activeSource = new EventSource(`/api/run/${action}`);
  activeSource.onmessage = e => {
    const raw = JSON.parse(e.data);
    if (typeof raw === 'string' && raw.startsWith('__EXIT__:')) {
      const code = parseInt(raw.split(':')[1]);
      activeSource.close(); activeSource = null;
      showResult(code === 0);
      restoreButtons();
      return;
    }
    appendLine(body, raw);
    body.scrollTop = body.scrollHeight;
  };
  activeSource.onerror = () => {
    activeSource.close(); activeSource = null;
    appendLine(body, '\n[Stream closed]\n');
    restoreButtons();
  };
}

function appendLine(container, text) {
  text.split('\n').forEach(line => {
    const span = document.createElement('span');
    if (/created|DRY RUN|wrote|Converted/i.test(line))    span.className = 'lo';
    else if (/failed|error|401|403|invalid/i.test(line))  span.className = 'le';
    else if (/[─━]{3,}/.test(line))                       span.className = 'lh';
    else if (/^\s*$/.test(line))                           span.className = 'ld';
    span.textContent = line;
    container.appendChild(span);
    container.appendChild(document.createElement('br'));
  });
}

function showResult(success) {
  const bar = document.getElementById('result-banner');
  if (success) {
    bar.className = 'result-bar ok';
    bar.innerHTML = '<svg width="15" height="15" viewBox="0 0 15 15" fill="none"><circle cx="7.5" cy="7.5" r="6.5" fill="#059669"/><path d="M4.5 7.5l2 2 4-4" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg> Completed successfully';
  } else {
    bar.className = 'result-bar err';
    bar.innerHTML = '<svg width="15" height="15" viewBox="0 0 15 15" fill="none"><circle cx="7.5" cy="7.5" r="6.5" fill="#DC2626"/><path d="M5 5l5 5M10 5l-5 5" stroke="white" stroke-width="1.8" stroke-linecap="round"/></svg> Failed — see output above. If you see "invalidSession", update your session.';
  }
}

function restoreButtons() {
  ['btn-dry','btn-smoke','btn-push'].forEach(id => {
    const b = document.getElementById(id);
    b.disabled = false;
    if (b._orig) { b.innerHTML = b._orig; }
  });
}

function clearConsole() {
  document.getElementById('console-body').innerHTML = '<span class="ld">Cleared. Click an action above to begin…</span>';
  document.getElementById('result-banner').className = 'result-bar';
}

// ── Calendar modal ────────────────────────────────────────────────────────────
function openCalendarModal() {
  document.getElementById('calendar-input').value = '';
  document.getElementById('calendar-result').className = 'modal-result';
  document.getElementById('calendar-result').innerHTML = '';
  document.getElementById('calendar-modal').classList.add('open');
  setTimeout(() => document.getElementById('calendar-input').focus(), 60);
}
function closeCalendarModal() { document.getElementById('calendar-modal').classList.remove('open'); }
function closeCalendarOnBackdrop(e) { if (e.target.id === 'calendar-modal') closeCalendarModal(); }

async function importCalendar() {
  const text   = document.getElementById('calendar-input').value.trim();
  const btn    = document.getElementById('btn-import');
  const result = document.getElementById('calendar-result');
  if (!text) { alert('Please paste your calendar JSON first.'); return; }

  btn.disabled = true;
  document.getElementById('import-icon').innerHTML = '<span class="spinner"></span>';
  result.className = 'modal-result'; result.innerHTML = '';

  const res  = await fetch('/api/import-calendar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({json: text}) });
  const data = await res.json();

  document.getElementById('import-icon').innerHTML = '<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M6.5 1v8M3 6l3.5 3.5L10 6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M1.5 11h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  btn.disabled = false;

  if (data.ok) {
    result.className = 'modal-result ok';
    result.innerHTML = `<b>Import successful</b><div class="res-meta"><b>${data.converted}</b> events converted &nbsp;&middot;&nbsp; <b>${data.skipped}</b> skipped (all-day / no time)</div>`;
    await loadEvents();
    setTimeout(closeCalendarModal, 2200);
  } else {
    result.className = 'modal-result err';
    result.innerHTML = data.error;
  }
}

// ── Session modal ─────────────────────────────────────────────────────────────
function openSessionModal() {
  document.getElementById('curl-input').value = '';
  document.getElementById('modal-result').className = 'modal-result';
  document.getElementById('modal-result').innerHTML = '';
  document.getElementById('session-modal').classList.add('open');
  setTimeout(() => document.getElementById('curl-input').focus(), 60);
}
function closeSessionModal() { document.getElementById('session-modal').classList.remove('open'); }
function closeOnBackdrop(e) { if (e.target.id === 'session-modal') closeSessionModal(); }

async function saveSession() {
  const curl   = document.getElementById('curl-input').value.trim();
  const btn    = document.getElementById('btn-save-session');
  const result = document.getElementById('modal-result');
  if (!curl) { alert('Please paste a cURL command first.'); return; }

  btn.disabled = true;
  document.getElementById('save-icon').innerHTML = '<span class="spinner"></span>';
  result.className = 'modal-result'; result.innerHTML = '';

  const res  = await fetch('/api/session-update', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({curl}) });
  const data = await res.json();

  document.getElementById('save-icon').innerHTML = '<svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M2 7l3.5 3.5L11 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  btn.disabled = false;

  if (data.ok) {
    result.className = 'modal-result ok';
    result.innerHTML = `<b>Session saved and verified</b><div class="res-meta">Host: <b>${data.host}</b> &nbsp;&middot;&nbsp; Parent: <b>${data.record_id}</b> &nbsp;&middot;&nbsp; Cookies: <b>${data.cookies}</b></div>`;
    await checkSession();
    setTimeout(closeSessionModal, 2200);
  } else {
    result.className = 'modal-result err';
    result.innerHTML = data.error;
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
checkSession();
loadEvents();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return HTML


if __name__ == "__main__":
    print("\n  Activity Tracker — Taboola Pub PS")
    print("  Open http://localhost:5055 in Chrome\n")
    app.run(host="127.0.0.1", port=5055, debug=False)
