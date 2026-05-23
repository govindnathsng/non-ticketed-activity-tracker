# Setup Guide — Non-Ticketed Activity Tracker

> One-time setup: **~5 minutes.**
> Weekly usage: **~30 seconds** (3 commands).

This tool lets you bulk-create **PS Tasks** in Salesforce from any source of
meeting data (Google Calendar, a JSON file, a tracker like Ada, etc.) instead
of clicking through the **New PS Task** form 20 times a week.

It works by **replaying the same Lightning Flow** your browser uses, so every
validation rule and field automation in Salesforce fires exactly as if you'd
filled out the form by hand.

---

## What you'll need

| Item | How long | Notes |
| --- | --- | --- |
| macOS / Linux machine | — | Windows users — use WSL or Git Bash, same commands work |
| Python 3.10 or newer | 0 min | Already on every Mac (`python3 --version` to check) |
| Salesforce Lightning access | 0 min | The account you'd use to create a PS Task manually |
| Your weekly meetings in JSON | varies | Easiest: have ChatGPT / Ada / Copilot dump them for you |

You **don't** need:
- ❌ Any Salesforce admin permissions
- ❌ A Salesforce security token or password
- ❌ A Google Cloud project / OAuth setup
- ❌ A credit card (everything is free)
- ❌ Anything installed in the browser

---

## Part 1 — One-time install (5 min)

You have two options. **Option A** (Nexus) is the recommended path once the
tool is published — teammates get a one-line install. **Option B** (clone the
repo) is for early access or if Nexus isn't set up in your org yet.

### Option A — Install from Nexus (recommended once published)

```bash
# 1. Create a dedicated virtual env (one time)
python3 -m venv ~/.virtualenvs/activity-tracker
source ~/.virtualenvs/activity-tracker/bin/activate

# 2. Install (replace <NEXUS_PYPI_URL> with the one Govind shares — looks like
#    https://nexus.taboola.com/repository/internal-pypi/simple)
pip install activity-tracker --index-url <NEXUS_PYPI_URL>

# 3. Verify — should print the list of CLI commands
activity-tracker --help
```

That's it — `activity-tracker` is now a global command inside that venv.
Skip to **Part 2** below.

> 🔄 **Updating later:**
> `pip install --upgrade activity-tracker --index-url <NEXUS_PYPI_URL>`

> 📅 **Want the Google Calendar integration too?**
> `pip install --upgrade 'activity-tracker[gcal]' --index-url <NEXUS_PYPI_URL>`

### Option B — Run from source (no install)

Use this if Nexus isn't set up yet, or you want to hack on the code.

```bash
cd ~/Documents
# Copy the project folder from wherever you got it:
cp -r /shared/path/Non-activity-tracking .
cd Non-activity-tracking

# Create a virtual env + install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Verify
.venv/bin/python run.py --help
```

You should see a list of 7 commands. The rest of this guide uses
`activity-tracker …` for brevity — if you went with Option B, substitute
`.venv/bin/python run.py …` everywhere.

---

## Part 2 — Capture your Salesforce session (do this every few hours)

Salesforce's `aura.token` is short-lived (typically a few hours per browser
session). You'll re-capture it whenever you start a fresh logging run. Takes
~20 seconds once you've done it once.

### Step 1. Open Salesforce and go to ANY Delivery Task page

In Chrome, log in to Salesforce and open **any** Delivery Task record — e.g.
your personal "Non-Ticketed Activities" Delivery Task. You can find yours by
navigating to:

> **Delivery Tasks** tab → search/filter for your personal non-ticketed task →
> click it open.

> ⚠️ **Important**: The script attaches every new PS Task to whichever
> Delivery Task you're viewing when you capture the session. Use the
> *right* parent so your records land where the report expects them.

### Step 2. Open DevTools → Network tab

- Right-click anywhere on the page → **Inspect** → click the **Network** tab.
- ✅ Check **Preserve log**
- In the filter box at the top, type `aura`.

### Step 3. Trigger any Aura call

Click anywhere on the page (a related list, a button, even just the page
header) so something new shows up in the Network list. You'll see entries like
`aura?r=…&aura.RecordUi…` etc.

### Step 4. Copy as cURL

- Right-click any of those `aura` rows
- Choose **Copy → Copy as cURL (bash)**

### Step 5. Save it as `session.curl.sh`

```bash
cd ~/Documents/Non-activity-tracking
pbpaste > session.curl.sh        # macOS — pastes whatever's in clipboard
```

Or, if pbpaste doesn't work on your system, just open the file in any editor
and paste:

```bash
nano session.curl.sh
# (paste with ⌘V, then Ctrl-X, Y, Enter)
```

Verify it captured everything:

```bash
.venv/bin/python run.py extract-auth --curl session.curl.sh --no-show-curl
```

You should see:
```
host                  : taboola.lightning.force.com
quickActionApiName    : Delivery_Task__c.New_PS_Task
recordId (parent)     : <your delivery task id, starts with a2d…>
cookies (~10 captured)
```

If any of those are missing, re-do Step 3-5 with a different aura request.

---

## Part 3 — Prepare your events JSON

You have three options. Use whichever you prefer.

### Option A — Use an AI / calendar tool you already have

Whatever you use (Ada, ChatGPT plugin, Copilot, Notion, Reclaim, etc.), ask it:

> "Fetch all my meetings from the last 7 days in JSON format. Exclude OOO,
> birthdays, and office status updates. For each event include `title`,
> `date` (YYYY-MM-DD), `time` (HH:MM - HH:MM IST), and `participants`."

Save the response as `raw-calendar.json`, then:

```bash
.venv/bin/python run.py convert-calendar --input raw-calendar.json --out events.json
```

The converter computes duration from your time strings, drops `[placeholder]`
events automatically, and applies sensible defaults (Meeting / Completed / USD /
Normal). Output preview is a nice table so you can sanity-check.

### Option B — Use Google Calendar directly (one-time OAuth setup)

```bash
.venv/bin/python run.py fetch-calendar --days 7 --out events.json
```

The first time you run this you'll need a `credentials.json` from Google Cloud
Console (free, no billing). See `README.md` → "Setup → Step 2" for the 2-minute
walkthrough. After that, the tool refreshes its own token forever.

### Option C — Write `events.json` by hand

Easiest format — just `subject` + `duration_minutes` is enough:

```json
[
  { "subject": "Monday Team Huddle [Pub PS APAC]", "duration_minutes": 30 },
  { "subject": "CSD India CP Call", "duration_minutes": 30, "activity_date": "2026-05-19" },
  { "subject": "Header bidding walkthrough", "duration_minutes": 60,
    "implementation_component": "Header bidding", "activity_date": "2026-05-19" }
]
```

See `events.schema.json` for the full list of supported fields and allowed
values (Activity Type, Implementation Component choices, Status options, etc.).

---

## Part 4 — Push to Salesforce

Always preview before pushing.

```bash
# 1. Dry-run — table view, no requests sent
.venv/bin/python run.py flow-create --curl session.curl.sh --input events.json --dry-run

# 2. Single-event test — creates ONE task, lets you verify on Salesforce
.venv/bin/python run.py flow-create --curl session.curl.sh --input events.json --limit 1

# 3. Full push — everything in events.json
.venv/bin/python run.py flow-create --curl session.curl.sh --input events.json
```

Each task takes about 4 seconds (2 round-trips: Flow create + field update).
20 events ≈ 1.5 minutes.

When done, open your Salesforce report and you should see the new rows.

---

## Part 5 — Fix an existing Task (optional)

If you push a record and notice a wrong value, you don't need to delete and
re-create. Patch it in place:

```bash
.venv/bin/python run.py update-task --curl session.curl.sh \
  --task-id 00TRg00000xxxxx \
  --field "Subject=Corrected Title" \
  --field "Status=Completed" \
  --field "Time_Spent_in_minutes_integer__c=45"
```

Field names are the Salesforce API names you'd see on the Setup → Object
Manager → Task → Fields page. Common ones:

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

## Troubleshooting

| Symptom | What it means | Fix |
| --- | --- | --- |
| `markup://aura:invalidSession` | Your `aura.token` is dead | Re-do **Part 2** (re-capture `session.curl.sh`). The cookies expired too — you need both refreshed. |
| `HTTP 401` / `HTTP 403` | Either token or cookies invalid | Same — re-capture. |
| `Flow validation errors: …` | Salesforce rejected a field combo (e.g. wrong Implementation Component for the Activity Type) | Look at the error message — it usually says exactly which field failed. Fix it in `events.json` and re-run. |
| Subject shows as "Meeting" not your text | (Should not happen anymore — the script force-sets Subject in phase 2) | If it does, run `update-task` with `--field "Subject=…"`. |
| `bash: python: command not found` | macOS calls it `python3`, not `python` | Use `.venv/bin/python …` (always works after Step 2). |
| Script worked once, now hangs | Your network blocked the Salesforce API mid-call | Cancel (Ctrl-C), check VPN / Wi-Fi, re-run. |

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
- The Google Calendar integration (if you use Option B) uses a read-only OAuth
  scope (`calendar.readonly`).

---

## Reference: file layout

```
Non-activity-tracking/
├── README.md                  ← project overview
├── SETUP.md                   ← you are here
├── requirements.txt           ← Python deps
├── run.py                     ← CLI entrypoint
├── events.schema.json         ← annotated field reference
├── events.example.json        ← starter template
├── session.curl.sh            ← your captured Salesforce session (gitignored)
├── raw-calendar.json          ← whatever your tool gave you (gitignored)
├── events.json                ← converter output (gitignored)
└── src/                       ← Python source
    ├── cli.py                 ← all 7 commands
    ├── har_auth.py            ← HAR + cURL parsing
    ├── sf_flow_client.py      ← Lightning Flow replay + saveRecord
    ├── calendar_client.py     ← Google Calendar wrapper
    └── …
```

---

## Need help?

Ping **Govind Nath Singh** on Slack. Include:
1. The exact command you ran
2. The full error output
3. (If sensitive) only the first few lines, not your `session.curl.sh`
