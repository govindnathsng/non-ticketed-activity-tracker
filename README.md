# Non-Ticketed Activity Tracker

> Automatically log non-ticketed work (meetings, calls, consultations, etc.)
> from **Google Calendar** into **Salesforce Tasks**, so your existing
> Salesforce report ([report `00O3o000006KYq3EAG`](https://taboola.lightning.force.com/lightning/r/Report/00O3o000006KYq3EAG/view?queryScope=userFolders))
> shows them automatically — no more manual weekly entry.

---

## What it does

Every run (manual or scheduled):

1. Connects to your **Google Calendar** (read-only).
2. Pulls events for the last *N* days (default 7).
3. Filters out noise — declined events, all-day blocks, lunches, ticketed
   work (anything matching keywords like `JIRA-`, `TICKET-`), and anything
   shorter than 5 minutes.
4. For each remaining event, creates a **Salesforce Task** with:
   - `Subject`  ← event title
   - `ActivityDate`  ← event date
   - `Type`  ← derived (`Call` / `Meeting` / `Other`) from event keywords
   - `Status`  ← `Completed`
   - `CallDurationInSeconds`  ← exact duration
   - `Description`  ← original notes + attendees + duration + Calendar link +
     a hidden dedup marker `[GCAL:<event_id>]`
   - Optional: writes duration in hours to a custom field, and the event
     ID to a custom external-ID field, if you have them.
5. **Skips events it already pushed** — safe to re-run any time.

Your existing report doesn't need any changes; it just starts showing more
rows.

---

## Project layout

```
Non-activity-tracking/
├── README.md                        ← you are here
├── requirements.txt                 ← Python deps
├── config.example.yaml              ← copy to config.yaml and edit
├── run.py                           ← entrypoint (python run.py …)
├── src/
│   ├── cli.py                       ← Click CLI
│   ├── config.py                    ← YAML loader
│   ├── calendar_client.py           ← Google Calendar wrapper
│   ├── salesforce_client.py         ← Salesforce wrapper
│   └── sync.py                      ← mapping + dedup engine
└── scripts/
    ├── run_weekly.sh                ← shell wrapper for the scheduler
    └── com.taboola.calsync.plist.example  ← launchd template (macOS)
```

---

## Setup (10 minutes, one time)

### 1. Install Python deps

```bash
cd ~/Documents/Non-activity-tracking
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create Google Calendar OAuth credentials

You need a **Desktop App** OAuth client to let the script read your
calendar. This takes 2 minutes.

1. Go to <https://console.cloud.google.com/>.
2. Create (or pick) a project.
3. **APIs & Services → Library** → search for **Google Calendar API** → **Enable**.
4. **APIs & Services → OAuth consent screen**:
   - User type: **Internal** (if Taboola Workspace allows) or **External**.
   - Fill the minimum (app name, your email) and save.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app**.
   - Name: `non-activity-tracker`.
   - Click **Create**, then **Download JSON**.
6. Save that file as `credentials.json` in this project root.

> First time you run the tool, a browser window opens asking you to grant
> read-only Calendar access. The resulting token is cached in `token.json`
> — no more prompts.

### 3. Get your Salesforce security token

You need:

- Your Salesforce **username** (e.g. `you@taboola.com`)
- Your Salesforce **password**
- A Salesforce **security token** (different from your password):
  1. Log in to Salesforce.
  2. Click your avatar → **Settings**.
  3. **My Personal Information → Reset My Security Token**.
  4. A new token is emailed to you. Keep it private.

> If your org enforces SSO or blocks password+token logins, ping me and
> I'll add the JWT/OAuth-connected-app variant — the code already
> declares an `auth_method: oauth_jwt` slot.

### 4. Create your config

```bash
cp config.example.yaml config.yaml
```

Then open `config.yaml` and fill in:

- `salesforce.username`, `salesforce.password`, `salesforce.security_token`
- (Optional) Tweak `sync.exclude_title_keywords` for things you do **not**
  want logged (ticketed work, OOO, focus time…).
- (Optional) If your report filters on a specific Type or custom field
  value, add it under `salesforce.task_defaults` so every synced Task
  matches. E.g.:

  ```yaml
  task_defaults:
    Status: Completed
    Type: Other
    Activity_Category__c: "Non-Ticketed"
  ```

`config.yaml` is **gitignored** — secrets stay on your machine.

### 5. Smoke-test both connections

```bash
python run.py test-google
python run.py test-salesforce
```

You should see today's events and your Salesforce user name.

### 6. Dry-run the sync

This prints **exactly what Tasks would be created** without touching
Salesforce:

```bash
python run.py run --dry-run
```

Inspect the table. If it looks right, do a real run:

```bash
python run.py run
```

Open the Salesforce report — your week is logged.

---

## Alternative path: replay the Lightning Flow from a HAR

If your org logs activities through a **Lightning Quick-Action Flow**
(e.g. `Delivery_Task__c.New_PS_Task`) and standard REST writes don't
satisfy the validation rules, you can drive the same Flow programmatically
by replaying what the browser does.

You need a HAR exported once from Chrome while you (manually) click
**New PS Task → fill the form → Save**. That HAR carries the short-lived
`aura.token` plus the `aura.context` envelope the Aura endpoint expects.

### 1. Capture the HAR (one time, takes 60 seconds)

1. In Salesforce Lightning, open Chrome DevTools → **Network** tab.
2. Check **Preserve log** and (optionally) **Disable cache**.
3. Click your **New PS Task** quick action, fill it out, click **Next**.
4. Right-click anywhere in the network list → **Save all as HAR with content**.
5. Save it somewhere safe — the file contains a live session token, treat
   it like a password. (It's gitignored by default.)

### 2. Option 1 — just extract the auth (for Postman / inspection)

```bash
python run.py extract-auth --har /path/to/your.har --out auth.json
```

This prints `aura.token`, `aura.context`, `aura.pageURI`, the parent
record ID, and a ready-to-paste cURL command you can import into Postman
to verify the session works.

### 3. Option 2 — automate the flow end-to-end

Drop your events into a JSON file (see `events.example.json`):

```json
[
  {"summary": "Monday Team Huddle [Pub PS APAC]", "duration_minutes": 30},
  {"summary": "CSD India CP Call", "duration_minutes": 60,
   "activity_type": "Meeting", "implementation_component": "None"}
]
```

Then preview, then create:

```bash
python run.py flow-create --har your.har --input events.json --dry-run
python run.py flow-create --har your.har --input events.json
```

The script replays `startFlow → navigateFlow(NEXT) → navigateFlow(FINISH)`
for each entry and prints the created Task ID (`00T…`) on success.

**Token lifetime:** the `aura.token` expires when your browser session
does. If you see `Salesforce exception: ...`, just re-export a fresh HAR.

**If you get 401/403:** copy the `Cookie:` header from the same DevTools
request and pass `--cookie-string "sid=...; BrowserId=...; ..."`.

---

## Day-to-day usage

```bash
# Sync the last week (default):
python run.py run

# Sync an explicit window (inclusive start, exclusive end):
python run.py run --since 2026-05-16 --until 2026-05-23

# See what would happen without writing anything:
python run.py run --dry-run -v
```

Re-running is **safe** — already-synced events are skipped.

---

## Automate it (weekly, macOS)

1. Edit `scripts/com.taboola.calsync.plist.example`, replace every
   `REPLACE_ME` with your macOS short username (`whoami`).
2. Save (without the `.example` suffix) to
   `~/Library/LaunchAgents/com.taboola.calsync.plist`.
3. Load and test:

   ```bash
   launchctl load ~/Library/LaunchAgents/com.taboola.calsync.plist
   launchctl start com.taboola.calsync          # fire it once now
   tail -f logs/sync_*.log                      # watch it run
   ```

That's it — every Friday at 5 PM it'll sync the week.

(Linux folks: use a `cron` line instead, e.g. `0 17 * * 5 /path/to/scripts/run_weekly.sh`.)

---

## Recommended (but optional) Salesforce schema tweaks

These make the system even more robust. You can do them later.

### A. Bulletproof dedup via an external-ID field

Without this, dedup uses a marker string in the Description (works fine,
but a SOQL `LIKE` per event is slower at scale).

1. **Setup → Object Manager → Task → Fields & Relationships → New**:
   - Data Type: **Text**
   - Field Label: `Google Event ID`
   - Length: **255**
   - ✅ **External ID**
   - ✅ **Unique** (treat blank as not-unique = ON)
   - API name auto-becomes `Google_Event_Id__c`.
2. In `config.yaml`:
   ```yaml
   salesforce:
     external_id_field: Google_Event_Id__c
   ```

### B. Hours field (nicer than seconds for reporting)

1. New **Number(5,2)** field on Task called `Time Spent (Hours)` →
   `Time_Spent_Hours__c`.
2. In `config.yaml`:
   ```yaml
   salesforce:
     hours_field: Time_Spent_Hours__c
   ```

The report can then sum hours per person per week directly.

### C. Make the report filter on synced tasks

In the report editor for `00O3o000006KYq3EAG`, add a filter like:

- `Status equals Completed` AND
- `Type equals Meeting, Call, Other` AND
- (optional) `Description contains "[GCAL:"`  ← if you want to show
  *only* auto-synced rows.

---

## Troubleshooting

| Symptom                                              | Fix                                                                                              |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `FileNotFoundError: credentials.json`                | You haven't downloaded the OAuth client JSON yet — see step 2.                                   |
| `INVALID_LOGIN: Invalid username, password, …`       | Re-reset your Salesforce security token (any password change invalidates it) and update YAML.    |
| `INVALID_LOGIN: API is disabled for this user`       | Your Salesforce profile is missing **API Enabled**. Ask your admin.                              |
| Browser doesn't open during first Google auth        | You're probably on a headless machine — run once locally to mint `token.json`, then copy it over.|
| Same event imported twice                            | Configure `external_id_field` (see option A above) for the strongest dedup.                      |
| Want to re-sync everything from scratch              | Delete `.state/synced_events.json` (and previously-created Tasks in Salesforce if needed).       |
| Tasks aren't appearing in the report                 | Open the report, ensure its filters match what's in `task_defaults` (Type, Status, etc.).        |

---

## Security notes

- `config.yaml`, `credentials.json`, `token.json`, and `.state/` are
  **gitignored**. Never commit them.
- Google scope used: `calendar.readonly` — the tool **cannot** modify
  your calendar.
- Salesforce credentials live only in `config.yaml` on your machine. For
  team-wide rollout, switch `auth_method` to `oauth_jwt` and use a
  Salesforce Connected App.

---

## Roadmap / nice-to-haves

- [ ] JWT-bearer Salesforce auth (no security token, no password).
- [ ] Slack notification with the weekly summary.
- [ ] Per-teammate calendars in a single config (multi-tenant mode).
- [ ] Map specific event titles → Salesforce `WhatId`/`WhoId` automatically.
- [ ] Optional GitHub Action runner (no local cron).
