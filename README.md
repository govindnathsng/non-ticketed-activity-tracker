# Non-Ticketed Activity Tracker

**Stop clicking the "New PS Task" button 20 times a week.**
Hand this tool a list of your meetings; it creates the matching Salesforce
PS Tasks for you in about 30 seconds.

- One-time install: **5 minutes**
- Use after that: **3 short commands per week**
- No passwords, no admin permissions, no IT tickets

---

## Table of contents

1. [What is this?](#1-what-is-this)
2. [Who is it for?](#2-who-is-it-for)
3. [How does it work? (in plain English)](#3-how-does-it-work-in-plain-english)
4. [What it does **not** do](#4-what-it-does-not-do)
5. [One-time install (5 minutes)](#5-one-time-install-5-minutes)
6. [Weekly workflow (30 seconds)](#6-weekly-workflow-30-seconds)
   - [Step A — Capture your Salesforce session](#step-a--capture-your-salesforce-session)
   - [Step B — Get a list of your meetings as JSON](#step-b--get-a-list-of-your-meetings-as-json)
   - [Step C — Convert + preview + push](#step-c--convert--preview--push)
7. [Fixing a task you already created](#7-fixing-a-task-you-already-created)
8. [Troubleshooting](#8-troubleshooting)
9. [Security & privacy](#9-security--privacy)
10. [What's inside the repo](#10-whats-inside-the-repo)
11. [Need help?](#11-need-help)

---

## 1. What is this?

Every week, Pub PS people have to log their meetings as **PS Task** records
inside a **Delivery Task** in Salesforce. Doing it by hand means opening a
form, filling 8–10 fields, clicking "Save", and repeating that 20–30 times.
It's painful and easy to forget.

This tool does the same thing for you, in bulk, from a JSON file. Behind
the scenes it pretends to be your own browser — it opens the same
"New PS Task" form, fills it in, and submits it. One after another, for
every meeting you list.

The end result is identical to what you'd get by clicking through the form
yourself: same fields, same validation, same audit trail, same report.

## 2. Who is it for?

Anyone on the Pub PS team (or any team using the **New PS Task** Lightning
quick-action on **Delivery Task** records). You do **not** need to know
Python. You only need to be able to:

- Run 3–4 commands in a terminal (copy/paste from this README)
- Open Chrome DevTools (right-click → Inspect) once per work session
- Edit a small text file occasionally (optional)

That's it.

## 3. How does it work? (in plain English)

Imagine you trained a very fast intern. You give them:

1. **A browser session** — proof you're logged into Salesforce. You hand
   this over by copying a single "cURL" command from Chrome DevTools and
   saving it to a file called `session.curl.sh`. (Takes ~20 seconds.)
2. **A list of meetings** — a small JSON file called `events.json` with
   one entry per meeting (title, duration, date, and a few defaults).

The intern then opens the "New PS Task" form in your name, types in each
meeting, and clicks Save. Because the form is the same one you'd use by
hand, all of Salesforce's validation and automation runs exactly as
usual — there is no separate API integration to break.

Time per meeting: ~4 seconds. 20 meetings ≈ 1.5 minutes.

## 4. What it does **not** do

- It **does not** read your Google Calendar automatically. You give it the
  meeting list as JSON. (The easiest way is to ask an AI assistant — see
  Step B.)
- It **does not** need any Salesforce admin permissions, security token,
  or password.
- It **does not** read, delete, or modify anything in Salesforce other
  than the PS Tasks you explicitly asked it to create or update.
- It **does not** store your session anywhere except your own laptop,
  inside the `session.curl.sh` file (which `.gitignore` blocks from ever
  being committed).

---

## 5. One-time install (5 minutes)

**What you need:** a Mac or Linux machine, Python 3.10+ (Macs already
have it), and access to clone this repo.

```bash
# 1. Get the code
cd ~/Documents
git clone https://github.com/govindnathsng/non-ticketed-activity-tracker.git
cd non-ticketed-activity-tracker

# 2. Create an isolated Python environment and install the tool
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .

# 3. Sanity check
activity-tracker --help
```

You should see a help message listing four commands (`extract-auth`,
`convert-calendar`, `flow-create`, `update-task`). That's everything.

> Why the `pip install --upgrade pip setuptools wheel` line? Old versions
> of `pip` (< 21.3) silently break editable installs. Upgrading first
> avoids a confusing `ModuleNotFoundError` later.

**Every new terminal session,** re-activate the environment before running
the tool:

```bash
cd ~/Documents/non-ticketed-activity-tracker
source .venv/bin/activate
```

---

## 6. Weekly workflow (30 seconds)

Three short steps. Capture session → fetch meetings → push.

### Step A — Capture your Salesforce session

Salesforce session tokens expire every few hours, so you redo this once
per working session (~20 seconds).

1. In Chrome, open **any Delivery Task page** — usually your personal
   "Non-Ticketed Activities" Delivery Task. **Important:** every task
   you create will be attached to whichever Delivery Task is open in
   this tab. Open the right one.
2. Right-click the page → **Inspect** → click the **Network** tab.
3. Tick **Preserve log**, and type `aura` in the filter box.
4. Click anywhere on the page (a related list, a button) so a new
   `aura?...` request appears.
5. Right-click that request → **Copy → Copy as cURL (bash)**.
6. Paste it into `session.curl.sh` in the project folder:
   ```bash
   pbpaste > session.curl.sh        # macOS shortcut
   ```
   (Or open the file in any editor and paste.)
7. Verify it parsed correctly:
   ```bash
   activity-tracker extract-auth --curl session.curl.sh --no-show-curl
   ```
   You should see your `host`, the parent `recordId` (the `a2d…` ID of
   the Delivery Task), and a list of cookies.

### Step B — Get a list of your meetings as JSON

Easiest way: ask your AI assistant (Ada, ChatGPT, Copilot, Reclaim,
Notion AI, anything) with this exact prompt:

> Show me all meetings from my calendar for the last 7 days in a single
> chronological sequence. List every instance of recurring meetings
> separately (do not group them). For each meeting, provide the exact
> date, time (with timezone), full title, and the list of participants.
> Present the final result as a clean JSON array.

Save the answer as `raw-calendar.json` in the project folder.

(If you'd rather write the file by hand, copy `events.example.json` and
edit. Only `subject` and `duration_minutes` are required per entry.)

### Step C — Convert + preview + push

```bash
# 1. Normalize the raw JSON into events.json
activity-tracker convert-calendar --input raw-calendar.json --out events.json

# 2. Preview without sending anything (a nice table appears)
activity-tracker flow-create --curl session.curl.sh --input events.json --dry-run

# 3. (Recommended) Smoke test — send ONE task and verify it in Salesforce
activity-tracker flow-create --curl session.curl.sh --input events.json --limit 1

# 4. Push everything
activity-tracker flow-create --curl session.curl.sh --input events.json
```

Each task takes about 4 seconds. The tool prints the Salesforce Task ID
of every record it creates, so you can click straight through to verify
in the UI.

When it finishes, open your standard PS report — the rows are already
there.

**Wrong parent showing in the preview?** Either re-capture the session
from the correct Delivery Task page (Step A), or override for this run
without recapturing:

```bash
activity-tracker flow-create --curl session.curl.sh --input events.json \
    --parent-id a2dRg000007hIthIAE --dry-run
```

---

## 7. Fixing a task you already created

If a record came out wrong, you don't need to delete and re-create it.
Patch it in place:

```bash
activity-tracker update-task --curl session.curl.sh \
    --task-id 00TRg00000xxxxx \
    --field "Subject=Corrected title" \
    --field "Status=Completed"
```

The most-used field names:

| You type… | …to update this UI field |
|---|---|
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

## 8. Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `markup://aura:invalidSession` | Your captured session expired. | Redo **Step A** — recapture `session.curl.sh`. |
| `HTTP 401` or `HTTP 403` | Same as above — session/cookies invalid. | Recapture. |
| `Flow returned to screen 1…` | Salesforce added a new required field to the form. | Open a GitHub issue or ping Govind — the client needs a small patch to send the new field. |
| `Flow validation errors: …` | A specific field combo was rejected (e.g. wrong Implementation Component for the chosen Activity Type). | The error names the field. Fix it in `events.json` and re-run. |
| `command not found: activity-tracker` | Your Python environment isn't active. | Run `source .venv/bin/activate` from the project folder. |
| `ModuleNotFoundError: No module named 'activity_tracker'` | Old `pip` / `setuptools` did a broken editable install. | `pip uninstall -y activity-tracker && pip install --upgrade pip setuptools wheel && pip install -e .` |
| Tasks created under the wrong Delivery Task | The session was captured from a different Delivery Task tab. | Either recapture from the right tab (Step A) or rerun with `--parent-id a2d…`. |
| Hangs partway through a push | Network blip / VPN dropped. | Ctrl-C, fix Wi-Fi/VPN, re-run. Already-created tasks won't be duplicated as long as you remove them from `events.json` or add `--limit` to skip past them. |

---

## 9. Security & privacy

- **Your session lives only on your laptop.** `session.curl.sh` contains
  your live Salesforce auth token + cookies. `.gitignore` blocks it from
  ever being committed.
- **Tokens expire fast.** Salesforce typically invalidates them within a
  few hours, so even if one leaks, the window of risk is tiny.
- **No shared credentials, no service account.** Everyone runs as
  themselves.
- **The tool only creates / updates the PS Tasks you ask for.** It
  cannot read other records, modify other objects, or delete anything.
- **The tool never touches your calendar.** You provide the meeting list
  manually (typically via your AI assistant in Step B).

---

## 10. What's inside the repo

```
non-ticketed-activity-tracker/
├── README.md                ← this file
├── pyproject.toml           ← Python package config (installs the CLI)
├── requirements.txt         ← raw dependency list (alternative install path)
├── events.example.json      ← starter template for events.json
├── events.schema.json       ← annotated reference of every supported field
└── activity_tracker/        ← the Python source
    ├── cli.py               ← the four CLI commands
    ├── har_auth.py          ← parses your session.curl.sh / .har capture
    └── sf_flow_client.py    ← replays the New PS Task Lightning Flow
```

Files you create locally (all git-ignored, never committed):

| File | What it holds |
|---|---|
| `session.curl.sh` | Captured Salesforce session. Recapture every few hours. |
| `raw-calendar.json` | Whatever JSON your AI / calendar tool produced. |
| `events.json` | Normalized events the tool sends to Salesforce. |

---

## 11. Need help?

Ping **Govind Nath Singh** on Slack. When reporting an issue, include:

1. The exact command you ran.
2. The full error output.
3. The first ~3 lines of `session.curl.sh` (the URL is enough — **never
   paste the full file**, it contains a live auth token).
