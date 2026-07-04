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

from flask import Flask, Response, jsonify, stream_with_context

BASE = Path(__file__).parent
ACTIVITY_TRACKER = BASE / ".venv" / "bin" / "activity-tracker"
EVENTS_JSON = BASE / "events.json"
SESSION_CURL = BASE / "session.curl.sh"

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
    host = re.search(r"host\s+:\s+(\S+)", output)
    record_id = re.search(r"recordId \(parent\)\s+:\s+(\S+)", output)
    cookies = re.search(r"cookies \((\d+) captured\)", output)
    return jsonify({
        "ok": True,
        "host": host.group(1) if host else "?",
        "record_id": record_id.group(1) if record_id else "?",
        "cookies": int(cookies.group(1)) if cookies else 0,
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
<title>Activity Tracker</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:       #0f172a;
    --surface:  #1e293b;
    --border:   #334155;
    --text:     #e2e8f0;
    --muted:    #94a3b8;
    --green:    #22c55e;
    --red:      #ef4444;
    --yellow:   #eab308;
    --blue:     #3b82f6;
    --purple:   #a855f7;
    --cyan:     #06b6d4;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 28px;
  }
  .logo {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, var(--blue), var(--purple));
    border-radius: 12px;
    display: grid; place-items: center;
    font-size: 22px;
  }
  header h1 { font-size: 1.4rem; font-weight: 700; }
  header p  { font-size: 0.82rem; color: var(--muted); margin-top: 2px; }

  /* ── Cards ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 18px;
  }
  .card-title {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
  }

  /* ── Session status ── */
  .session-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
  }
  .badge.green  { background: #14532d44; color: var(--green);  border: 1px solid #166534; }
  .badge.red    { background: #7f1d1d44; color: var(--red);    border: 1px solid #991b1b; }
  .badge.yellow { background: #713f1244; color: var(--yellow); border: 1px solid #92400e; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }

  .session-meta {
    font-size: 0.8rem;
    color: var(--muted);
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
  }
  .session-meta span b { color: var(--text); }

  /* ── Summary stats ── */
  .stats-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }
  .stat {
    flex: 1;
    min-width: 100px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 16px;
    text-align: center;
  }
  .stat-num  { font-size: 1.8rem; font-weight: 700; color: var(--cyan); line-height: 1; }
  .stat-label { font-size: 0.72rem; color: var(--muted); margin-top: 4px; }

  /* ── Events table ── */
  .table-wrap {
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid var(--border);
    max-height: 320px;
    overflow-y: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  thead th {
    background: #0f172a;
    padding: 10px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 0.7rem;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--muted);
    position: sticky;
    top: 0;
    z-index: 1;
  }
  tbody tr { border-top: 1px solid var(--border); transition: background .1s; }
  tbody tr:hover { background: #ffffff08; }
  tbody td { padding: 9px 14px; vertical-align: middle; }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 500;
  }
  .pill-yes  { background: #14532d44; color: var(--green);  border: 1px solid #166534; }
  .pill-no   { background: #1e293b;   color: var(--muted);  border: 1px solid var(--border); }
  .pill-cfg  { background: #1e3a5f44; color: var(--cyan);   border: 1px solid #0e4f7a; }
  .pill-none { background: #1e293b;   color: var(--muted);  border: 1px solid var(--border); }
  .date-cell { color: var(--muted); white-space: nowrap; }
  .min-cell  { text-align: right; color: var(--cyan); font-variant-numeric: tabular-nums; }

  /* ── Action buttons ── */
  .actions {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .btn {
    flex: 1;
    min-width: 160px;
    padding: 13px 20px;
    border: none;
    border-radius: 10px;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: opacity .15s, transform .1s;
  }
  .btn:hover:not(:disabled) { opacity: .88; transform: translateY(-1px); }
  .btn:active:not(:disabled){ transform: translateY(0); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-preview { background: #1e3a5f; color: var(--cyan);   border: 1px solid #0e4f7a; }
  .btn-smoke   { background: #3f2d63; color: var(--purple); border: 1px solid #5b3d91; }
  .btn-push    { background: #14532d; color: var(--green);  border: 1px solid #166534; }
  .btn-push.danger { background: #7f1d1d; color: var(--red); border: 1px solid #991b1b; }

  .spinner {
    width: 14px; height: 14px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin .6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Console output ── */
  .console {
    background: #020617;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0;
    overflow: hidden;
  }
  .console-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  .console-title {
    font-size: 0.75rem;
    color: var(--muted);
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
  }
  .console-dots { display: flex; gap: 6px; }
  .console-dots span {
    width: 10px; height: 10px; border-radius: 50%;
  }
  .d1 { background: #ef4444; } .d2 { background: #eab308; } .d3 { background: #22c55e; }
  #console-body {
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.78rem;
    line-height: 1.6;
    color: #94a3b8;
    padding: 14px 18px;
    min-height: 200px;
    max-height: 380px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  #console-body .line-ok     { color: #22c55e; }
  #console-body .line-fail   { color: #ef4444; }
  #console-body .line-header { color: #e2e8f0; font-weight: 600; }
  #console-body .line-dim    { color: #475569; }

  .result-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 18px;
    border-top: 1px solid var(--border);
    font-weight: 600;
    font-size: 0.88rem;
  }
  .result-banner.success { background: #14532d22; color: var(--green); }
  .result-banner.failure { background: #7f1d1d22; color: var(--red);   }

  #refresh-btn {
    background: none;
    border: 1px solid var(--border);
    color: var(--muted);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.72rem;
    cursor: pointer;
    transition: color .15s;
  }
  #refresh-btn:hover { color: var(--text); }
</style>
</head>
<body>

<header>
  <div class="logo">⚡</div>
  <div>
    <h1>Non-Ticketed Activity Tracker</h1>
    <p>Bulk-create Salesforce PS Tasks from your meetings</p>
  </div>
</header>

<!-- Session status -->
<div class="card">
  <div class="card-title">Salesforce Session</div>
  <div class="session-row">
    <span id="session-badge" class="badge yellow"><span class="dot"></span>Checking…</span>
    <div class="session-meta" id="session-meta"></div>
    <button id="refresh-btn" onclick="checkSession()">↻ Re-check</button>
  </div>
</div>

<!-- Summary stats -->
<div class="card">
  <div class="card-title">Events ready to push</div>
  <div class="stats-row" id="stats-row">
    <div class="stat"><div class="stat-num" id="stat-total">—</div><div class="stat-label">Total events</div></div>
    <div class="stat"><div class="stat-num" id="stat-client">—</div><div class="stat-label">Client-facing</div></div>
    <div class="stat"><div class="stat-num" id="stat-mins">—</div><div class="stat-label">Total minutes</div></div>
    <div class="stat"><div class="stat-num" id="stat-days">—</div><div class="stat-label">Days covered</div></div>
  </div>
</div>

<!-- Events table -->
<div class="card">
  <div class="card-title">Event list</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Date</th>
          <th>Subject</th>
          <th>Min</th>
          <th>Impl component</th>
          <th>Client?</th>
        </tr>
      </thead>
      <tbody id="events-body">
        <tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">Loading…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- Action buttons -->
<div class="actions">
  <button class="btn btn-preview" id="btn-dry" onclick="runAction('dry-run')">
    <span>👁</span> Preview (Dry Run)
  </button>
  <button class="btn btn-smoke" id="btn-smoke" onclick="runAction('smoke')">
    <span>🧪</span> Smoke Test (1 task)
  </button>
  <button class="btn btn-push" id="btn-push" onclick="runAction('push')">
    <span>🚀</span> Push All Tasks
  </button>
</div>

<!-- Console -->
<div class="console">
  <div class="console-header">
    <div class="console-dots"><span class="d1"></span><span class="d2"></span><span class="d3"></span></div>
    <div class="console-title" id="console-title">Output</div>
    <button id="refresh-btn" onclick="clearConsole()" style="background:none;border:1px solid var(--border);color:var(--muted);border-radius:6px;padding:4px 10px;font-size:.72rem;cursor:pointer;">Clear</button>
  </div>
  <div id="console-body"><span style="color:var(--muted)">Click a button above to start…</span></div>
  <div id="result-banner" style="display:none"></div>
</div>

<script>
let activeSource = null;

// ── Load events ──────────────────────────────────────────────────────────────
async function loadEvents() {
  const res = await fetch('/api/events');
  const events = await res.json();
  const tbody = document.getElementById('events-body');

  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">No events found in events.json</td></tr>';
    return;
  }

  // Stats
  const clientFacing = events.filter(e => e.is_client_facing === 'Yes').length;
  const totalMins = events.reduce((s, e) => s + (e.duration_minutes || e.time_spent_in_minutes || 0), 0);
  const days = new Set(events.map(e => e.activity_date)).size;
  document.getElementById('stat-total').textContent  = events.length;
  document.getElementById('stat-client').textContent = clientFacing;
  document.getElementById('stat-mins').textContent   = totalMins;
  document.getElementById('stat-days').textContent   = days;

  // Table rows
  tbody.innerHTML = events.map((e, i) => {
    const impl = e.implementation_component || 'None';
    const implPill = impl !== 'None'
      ? `<span class="pill pill-cfg">${impl}</span>`
      : `<span class="pill pill-none">None</span>`;
    const clientPill = e.is_client_facing === 'Yes'
      ? `<span class="pill pill-yes">Yes</span>`
      : `<span class="pill pill-no">No</span>`;
    const mins = e.duration_minutes || e.time_spent_in_minutes || '?';
    return `<tr>
      <td style="color:var(--muted)">${i + 1}</td>
      <td class="date-cell">${e.activity_date || ''}</td>
      <td>${e.subject || ''}</td>
      <td class="min-cell">${mins}</td>
      <td>${implPill}</td>
      <td>${clientPill}</td>
    </tr>`;
  }).join('');
}

// ── Session check ─────────────────────────────────────────────────────────────
async function checkSession() {
  const badge = document.getElementById('session-badge');
  const meta  = document.getElementById('session-meta');
  badge.className = 'badge yellow';
  badge.innerHTML = '<span class="dot"></span>Checking…';
  meta.innerHTML  = '';

  const res  = await fetch('/api/session-check');
  const data = await res.json();

  if (data.ok) {
    badge.className = 'badge green';
    badge.innerHTML = '<span class="dot"></span>Session valid';
    meta.innerHTML  = `
      <span><b>${data.host}</b></span>
      <span>Parent: <b>${data.record_id}</b></span>
      <span>Cookies: <b>${data.cookies}</b></span>`;
  } else {
    badge.className = 'badge red';
    badge.innerHTML = '<span class="dot"></span>Session expired / missing';
    meta.innerHTML  = `<span style="color:var(--red);font-size:.75rem">Re-capture session.curl.sh from Chrome DevTools</span>`;
  }
}

// ── Run action (SSE) ──────────────────────────────────────────────────────────
function runAction(action) {
  if (activeSource) { activeSource.close(); activeSource = null; }

  const labels = { 'dry-run': 'Preview (Dry Run)', 'smoke': 'Smoke Test', 'push': 'Push All' };
  document.getElementById('console-title').textContent = labels[action] || action;
  document.getElementById('result-banner').style.display = 'none';

  const body = document.getElementById('console-body');
  body.innerHTML = '';

  // Disable all buttons while running
  ['btn-dry', 'btn-smoke', 'btn-push'].forEach(id => {
    const b = document.getElementById(id);
    b.disabled = true;
    b._origHTML = b.innerHTML;
    if (b.id === 'btn-' + action.replace('-run','dry').replace('dry-run','dry')) {
      b.innerHTML = '<span class="spinner"></span> Running…';
    }
  });
  // mark the running button with spinner
  const activeId = { 'dry-run': 'btn-dry', 'smoke': 'btn-smoke', 'push': 'btn-push' }[action];
  document.getElementById(activeId).innerHTML = '<span class="spinner"></span> Running…';

  activeSource = new EventSource(`/api/run/${action}`);
  activeSource.onmessage = (e) => {
    const raw = JSON.parse(e.data);

    if (typeof raw === 'string' && raw.startsWith('__EXIT__:')) {
      const code = parseInt(raw.split(':')[1]);
      activeSource.close();
      activeSource = null;
      showResult(code === 0);
      restoreButtons();
      return;
    }

    appendLine(body, raw);
    body.scrollTop = body.scrollHeight;
  };
  activeSource.onerror = () => {
    activeSource.close();
    activeSource = null;
    appendLine(body, '\n[Connection closed]\n');
    restoreButtons();
  };
}

function appendLine(container, text) {
  const lines = text.split('\n');
  lines.forEach(line => {
    if (!line && lines.length > 1) {
      container.appendChild(document.createElement('br'));
      return;
    }
    const span = document.createElement('span');
    if (/created|DRY RUN|wrote|Converted/.test(line)) span.className = 'line-ok';
    else if (/failed|error|Error|401|403/.test(line))  span.className = 'line-fail';
    else if (/─{3,}|━{3,}/.test(line))                span.className = 'line-header';
    else if (/^\s*$/.test(line))                       span.className = 'line-dim';
    span.textContent = line;
    container.appendChild(span);
    container.appendChild(document.createElement('br'));
  });
}

function showResult(success) {
  const banner = document.getElementById('result-banner');
  banner.style.display = 'flex';
  if (success) {
    banner.className = 'result-banner success';
    banner.innerHTML = '✅ Completed successfully';
  } else {
    banner.className = 'result-banner failure';
    banner.innerHTML = '❌ Failed — check output above. If you see "invalidSession", re-capture session.curl.sh.';
  }
}

function restoreButtons() {
  ['btn-dry', 'btn-smoke', 'btn-push'].forEach(id => {
    const b = document.getElementById(id);
    b.disabled = false;
    if (b._origHTML) { b.innerHTML = b._origHTML; delete b._origHTML; }
  });
}

function clearConsole() {
  document.getElementById('console-body').innerHTML = '<span style="color:var(--muted)">Cleared. Click a button above to start…</span>';
  document.getElementById('result-banner').style.display = 'none';
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
    print("\n  ⚡  Activity Tracker UI")
    print("  Open http://localhost:5055 in Chrome\n")
    app.run(host="127.0.0.1", port=5055, debug=False)
