# Setup Guide — Non-Ticketed Activity Tracker

> **One-time install:** ~5 minutes
> **Weekly use after that:** 3 commands, ~30 seconds

This tool bulk-creates **PS Tasks** in Salesforce from a JSON list of meetings
— so you stop clicking through the **New PS Task** form 20+ times a week.

It works by **replaying the same Lightning Flow** your browser already uses,
so every validation rule and field automation fires exactly as if you'd
filled out the form by hand.

---

## What you'll need

| Item | Notes |
| --- | --- |
| macOS / Linux machine | Windows: use WSL or Git Bash, same commands work |
| Python 3.10+ | Already on every Mac. Check: `python3 --version` |
| Salesforce Lightning access | Whatever account you'd use to create a PS Task manually |
| Git access to the repo | The GitHub link below |

You **don't** need: any Salesforce admin perms, security token, password,
Google Cloud project, or anything installed in the browser.

---

## Part 1 — One-time install (5 min)

### Step 1. Clone the repo

```bash
cd ~/Documents
git clone -b main https://github.com/govindnathsng/non-ticketed-activity-tracker.git
cd non-ticketed-activity-tracker
```

> The `-b main` flag is harmless (the repo only has `main`) but pins the
> branch explicitly in case more branches are added later.

### Step 2. Create a virtualenv and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
```

> `-e .` is "editable install" — it uses `pyproject.toml` to install the
> package **and** register a global `activity-tracker` command inside this
> venv. If you `git pull` later, the command picks up the latest code
> automatically (no re-install).
>
> ⚠️ The `pip install --upgrade pip setuptools wheel` line is **not optional**.
> Older `pip` (< 21.3) or `setuptools` (< 64) will print
> `Successfully installed activity-tracker-0.1.0` but silently skip wiring
> the source folder onto `sys.path`, so `activity-tracker` then fails with
> `ModuleNotFoundError: No module named 'activity_tracker'`. Upgrading first
> avoids that trap.

### Step 3. Verify

```bash
activity-tracker --help
```

You should see a list of commands (`extract-auth`, `flow-create`,
`update-task`, `convert-calendar`, …). That's it — install done.

> 📅 **What about pulling from Google Calendar automatically?**
> There is a `fetch-calendar` command in the codebase, but it is **not
> production-ready yet** (token refresh + filter rules are still being
> finished). For now, use Option A in Part 3 — give the tool a JSON dump
> from your AI assistant. It takes ~30 seconds and works reliably.

---

## Part 2 — Capture your Salesforce session (re-do every few hours)

Salesforce's `aura.token` is short-lived (typically a few hours per browser
session). You'll re-capture it whenever you start a fresh logging run. Takes
~20 seconds once you've done it once.

### Step 1. Open Salesforce → any Delivery Task page

In Chrome, log in to Salesforce and open **any** Delivery Task record — e.g.
your personal "Non-Ticketed Activities" Delivery Task. Find it via:

> **Delivery Tasks** tab → search/filter for your personal non-ticketed task
> → click it open.

> ⚠️ **Important:** The script attaches every new PS Task to whichever
> Delivery Task you're viewing when you capture the session. Use the
> *right* parent so your records land where the report expects them.
>
> If you've already captured a session from the wrong tab, you don't have
> to recapture — pass `--parent-id <a2d…>` to `extract-auth` /
> `flow-create` to override the parent for the run. The expected ID is
> printed by `extract-auth` as `recordId (parent)` and shows up in your
> Salesforce URL when viewing the Delivery Task.

### Step 2. Open DevTools → Network tab

- Right-click the page → **Inspect** → click the **Network** tab.
- ✅ Check **Preserve log**.
- In the filter box, type `aura`.

### Step 3. Trigger any Aura call

Click anywhere on the page (a related list, a button, even just the page
header) so something new shows up in the Network list. You'll see entries
like `aura?r=…&aura.RecordUi…`.

### Step 4. Copy as cURL

- Right-click any of those `aura` rows
- Choose **Copy → Copy as cURL (bash)**

### Step 5. Save it as `session.curl.sh`

```bash
cd ~/Documents/non-ticketed-activity-tracker
pbpaste > session.curl.sh        # macOS — pastes whatever's in clipboard
```

If `pbpaste` isn't there, open the file in any editor and paste manually:

```bash
nano session.curl.sh
# (paste, then Ctrl-X, Y, Enter)
```

### Step 6. Verify the session

```bash
activity-tracker extract-auth --curl session.curl.sh --no-show-curl
```

You should see:

```
host                  : taboola.lightning.force.com
quickActionApiName    : Delivery_Task__c.New_PS_Task
recordId (parent)     : a2dRg000007hIth   ← your Delivery Task id
cookies (~10 captured)
```

If any of those are missing, repeat Step 3-5 with a different aura request.

---

## Part 3 — Prepare your events JSON

Two supported options. Pick whichever is easiest for you.

### Option A — Use whatever AI / calendar tool you already have (recommended)

Ask **Ada** (or ChatGPT, Copilot, Reclaim, Notion, etc.) with this prompt —
copy it verbatim, it produces exactly the format the converter expects:

> Show me all meetings from my calendar for the last 7 days in a single
> chronological sequence. List every instance of recurring meetings
> separately (do not group them). For each meeting, provide the exact date,
> time (with timezone), full title, and the list of participants. Present
> the final result as a clean JSON array.

Save the response as `raw-calendar.json`, then convert:

```bash
activity-tracker convert-calendar --input raw-calendar.json --out events.json
```

The converter computes duration from your time strings, drops
`[placeholder]` events automatically, and applies sensible defaults
(Meeting / Completed / USD / Normal). Output is a nice table you can
sanity-check.

### Option B — Write `events.json` by hand

Minimum required is just `subject` + `duration_minutes`:

```json
[
  { "subject": "Monday Team Huddle [Pub PS APAC]", "duration_minutes": 30 },
  { "subject": "CSD India CP Call", "duration_minutes": 30, "activity_date": "2026-05-19" },
  { "subject": "Header bidding walkthrough", "duration_minutes": 60,
    "implementation_component": "Header bidding", "activity_date": "2026-05-19" }
]
```

See `events.schema.json` in the repo for every supported field and allowed
values (Activity Type, Implementation Component, Status, etc.).

> 🚧 **Heads-up on Google Calendar auto-pull.** There is a
> `fetch-calendar` command in the code that uses Google OAuth to pull
> meetings directly, but it's **still experimental** — token refresh and
> the filter rules aren't finished. Until that's done, stick with
> Option A (paste JSON from your AI assistant). It takes ~30 seconds and
> avoids the OAuth setup entirely.

---

## Part 4 — Push to Salesforce

Always preview before pushing.

```bash
# 1. Dry-run — table view, no requests sent
activity-tracker flow-create --curl session.curl.sh --input events.json --dry-run

# 2. Single-event smoke test — creates ONE task so you can verify it in Salesforce
activity-tracker flow-create --curl session.curl.sh --input events.json --limit 1

# 3. Full push — everything in events.json
activity-tracker flow-create --curl session.curl.sh --input events.json
```

If the captured session points at the wrong Delivery Task, pass
`--parent-id <a2d…>` to override for this run without re-capturing:

```bash
# Override the parent for any of the three commands above, e.g.:
activity-tracker flow-create --curl session.curl.sh --input events.json \
    --parent-id a2dRg000007hIthIAE --dry-run
```

Each task takes ~4 seconds (2 round-trips: Flow create + field update).
20 events ≈ 1.5 minutes.

When done, open your Salesforce report and the new rows are there.

> 💡 **Wrong parent showing in the preview?** The "Parent record" line
> in the preview is the Delivery Task every new PS Task will be attached
> to. If it's wrong, either recapture the session from the right tab
> (Part 2) *or* pass `--parent-id a2dRg000...` to override for this run:
>
> ```bash
> activity-tracker flow-create --curl session.curl.sh --input events.json \
>     --parent-id a2dRg000007hIthIAE --dry-run
> ```

---

## Part 5 — Fix an existing Task (optional)

If you pushed a record and a value is wrong, you don't need to delete and
re-create. Patch it in place:

```bash
activity-tracker update-task --curl session.curl.sh \
  --task-id 00TRg00000xxxxx \
  --field "Subject=Corrected Title" \
  --field "Status=Completed" \
  --field "Time_Spent_in_minutes_integer__c=45"
```

Field names are the Salesforce API names from **Setup → Object Manager →
Task → Fields**. Most-used ones:

| You'd type… | …to set this UI field |
| --- | --- |
| `Subject` | Subject |
| `Task_Type__c` | Task Type |
| `Task_Sub_Type__c` | Task Sub Type |
| `CurrencyIsoCode` | Activity Currency |
| `Time_Spent_in_minutes_integer__c` | Time Spent in Minutes |
| `Status` | Status |
| `Priority` | Priority |
| `ActivityDate` | Due Date |
| `Description` | Comments |

---

## Updating the tool later

```bash
cd ~/Documents/non-ticketed-activity-tracker
git pull
# `pip install -e .` already linked the source folder — no re-install needed.
```

If `pyproject.toml` changed (new deps), refresh:

```bash
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
```

---

## Troubleshooting

| Symptom | What it means | Fix |
| --- | --- | --- |
| `markup://aura:invalidSession` | Your `aura.token` died | Re-do **Part 2** (recapture `session.curl.sh`) |
| `HTTP 401` / `HTTP 403` | Token or cookies invalid | Same — recapture |
| `Flow validation errors: …` | Salesforce rejected a field combo (e.g. wrong Implementation Component for the Activity Type) | The error names the failing field. Fix it in `events.json` and re-run. |
| Subject shows as "Meeting" not your text | Should not happen — the script force-sets Subject in phase 2 | If it does, run `update-task` with `--field "Subject=…"` |
| `command not found: activity-tracker` | Your venv isn't active | `source .venv/bin/activate` (run from the project folder) |
| `ModuleNotFoundError: No module named 'activity_tracker'` (right after install) | Old `pip` / `setuptools` did a half-broken editable install | `pip uninstall -y activity-tracker && pip install --upgrade pip setuptools wheel && pip install -e .` |
| Tasks created under the wrong Delivery Task | The session was captured while viewing a different Delivery Task | Either recapture from the right tab (Part 2) or re-run with `--parent-id <correct a2d… id>`. The current parent shows up in the `flow-create` preview header. |
| `bash: python: command not found` | macOS calls it `python3` | Use `python3` for the venv create step. After activation, just `python` works. |
| Script worked once, now hangs | Network blocked the Salesforce API mid-call | Ctrl-C, check VPN / Wi-Fi, re-run |

---

## Security notes

- `session.curl.sh` contains your **live Salesforce session token + cookies**.
  Treat it like a password. It's in `.gitignore` so you won't accidentally
  commit it.
- Tokens self-expire within a few hours of inactivity, so the blast radius if
  one leaks is small — but still don't share the file.
- The script only creates PS Tasks attached to the parent Delivery Task that
  was active in your browser when you captured the session. It cannot read,
  delete, or modify anything else.
- The experimental Google Calendar path (`fetch-calendar`) uses a
  read-only OAuth scope (`calendar.readonly`) — it never writes back
  to your calendar. It also runs only when you invoke it explicitly.

---

## Reference: file layout

```
non-ticketed-activity-tracker/
├── README.md                  ← project overview
├── SETUP.md                   ← you are here
├── pyproject.toml             ← package config (installs `activity-tracker` command)
├── requirements.txt           ← raw deps (alternative to pip install -e .)
├── run.py                     ← `python run.py …` fallback if you skip pip install
├── events.schema.json         ← annotated field reference
├── events.example.json        ← starter template
├── session.curl.sh            ← your captured Salesforce session (gitignored)
├── raw-calendar.json          ← whatever your tool gave you (gitignored)
├── events.json                ← converter output (gitignored)
└── activity_tracker/          ← Python source
    ├── cli.py                 ← all commands
    ├── har_auth.py            ← HAR + cURL parsing
    ├── sf_flow_client.py      ← Lightning Flow replay + saveRecord
    ├── calendar_client.py     ← Google Calendar wrapper (optional)
    └── …
```

---

## Need help?

Ping **Govind Nath Singh** on Slack. Include:

1. The exact command you ran
2. The full error output
3. (If sensitive) only the first few lines — never paste your `session.curl.sh`
