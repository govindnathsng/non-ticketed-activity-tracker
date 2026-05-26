# Non-Ticketed Activity Tracker

> Bulk-create Salesforce **PS Task** records from your weekly meetings —
> instead of clicking through the "New PS Task" Lightning form 20–30 times
> a week.

**One-time setup:** ~5 minutes &nbsp;·&nbsp; **Weekly use:** 3 commands, ~30 seconds.

➡️ Full step-by-step install + walkthrough is in **[SETUP.md](./SETUP.md)**.

---

## What it does

You hand the tool a list of your meetings (as JSON). It logs into
Salesforce using *your own* browser session and creates one PS Task per
meeting, with every field (Subject, Task Type, Sub-Type, Activity
Currency, Time Spent, Status, Description, Activity Date) pre-filled
correctly. Placeholder / OOO / lunch entries are skipped automatically.

A run that used to take 15–20 minutes of clicking now takes about
**30 seconds**.

## What it does **not** do

- ❌ **It does not pull from Google Calendar automatically yet.**
  You provide the meeting list as a JSON file (see [How you provide the
  data](#how-you-provide-the-data) below). The Google Calendar OAuth
  path exists in the code but is **not finished** — treat it as
  experimental.
- ❌ No admin permissions, security tokens, or service accounts are
  needed.
- ❌ Nothing is read or modified in Salesforce beyond creating the PS
  Tasks you asked for.

---

## How it works

1. You capture your Salesforce session **once per working session**
   (a `Copy as cURL` from Chrome DevTools — ~20 seconds, no passwords).
2. You give the tool a JSON list of meetings.
3. For every meeting, the CLI **replays the exact Lightning Aura Flow**
   that the "New PS Task" quick action uses in your browser:
   - `startFlow → navigateFlow(NEXT → FINISH)` creates the Task
   - `saveRecord` patches Subject / Task Type / Sub Type / Currency /
     Time Spent / Status / Description / Activity Date
4. The Salesforce Task IDs are printed for verification, and the rows
   appear in your standard PS report instantly.

Because it replays the same Flow your browser uses, **every Salesforce
validation rule and field automation fires identically** to manual
entry. There is no separate API integration to maintain.

---

## How you provide the data

Pick whichever is easiest — all three produce the same `events.json`
that the tool consumes:

| Option | How | Effort |
| --- | --- | --- |
| **A. AI assistant (recommended)** | Ask Ada / ChatGPT / Copilot / Reclaim for "all meetings last 7 days as JSON" — see the prompt in SETUP.md. Save as `raw-calendar.json`, then run `activity-tracker convert-calendar`. | ~30 sec / week |
| **B. Write `events.json` by hand** | Just `subject` + `duration_minutes` are required. See `events.example.json`. | ~5 min / week |
| **C. Google Calendar OAuth (experimental)** | `activity-tracker fetch-calendar --days 7 --out events.json`. Needs `pip install -e '.[gcal]'` and a Google OAuth client. **Not production-ready** — token refresh + filter rules still being polished. Use Option A for now. | varies |

---

## Commands at a glance

After `pip install -e .` (see SETUP.md):

| Command | What it does |
| --- | --- |
| `activity-tracker extract-auth --curl session.curl.sh` | Verify the parsed Salesforce session (host, parent record, token, cookies). |
| `activity-tracker convert-calendar --input raw-calendar.json --out events.json` | Normalize a raw calendar/AI JSON dump into the format `flow-create` expects. |
| `activity-tracker flow-create --curl session.curl.sh --input events.json --dry-run` | Preview the Tasks that *would* be created. |
| `activity-tracker flow-create --curl session.curl.sh --input events.json --limit 1` | Smoke test — push only the first event. |
| `activity-tracker flow-create --curl session.curl.sh --input events.json` | Push everything. |
| `activity-tracker update-task --curl session.curl.sh --task-id 00T… --field "Subject=…"` | Patch a single field on an already-created Task. |
| `activity-tracker fetch-calendar --days 7 --out events.json` | *Experimental* — pull events from Google Calendar via OAuth. Not recommended for daily use yet. |

Run `activity-tracker --help` (or any subcommand `--help`) for full options.

---

## Repo layout

```
non-ticketed-activity-tracker/
├── README.md                  ← you are here
├── SETUP.md                   ← full step-by-step install guide
├── ANNOUNCEMENT_EMAIL.md      ← draft emails / Slack copy for rollout
├── pyproject.toml             ← package config (installs the `activity-tracker` command)
├── requirements.txt           ← raw deps (alternative to `pip install -e .`)
├── run.py                     ← fallback entrypoint (`python run.py …`)
├── events.schema.json         ← annotated JSON-schema reference
├── events.example.json        ← starter template
├── config.example.yaml        ← config skeleton (only needed for the experimental GCal path)
├── scripts/                   ← cron / launchd helpers (optional)
└── activity_tracker/          ← the Python package
    ├── cli.py                 ← all CLI commands
    ├── har_auth.py            ← cURL / HAR → session-info parser
    ├── sf_flow_client.py      ← Lightning Flow replay + saveRecord (the core)
    ├── calendar_client.py     ← Google Calendar wrapper (experimental, used only by fetch-calendar)
    ├── salesforce_client.py   ← legacy REST client (unused, kept for reference)
    ├── sync.py                ← legacy sync engine (unused, kept for reference)
    └── config.py              ← YAML loader (used only by the experimental GCal path)
```

> `salesforce_client.py` and `sync.py` are an earlier REST-based prototype
> kept for reference. The active path is `sf_flow_client.py` (Lightning
> Flow replay).

---

## Files you create locally (all git-ignored)

| File | What it holds |
| --- | --- |
| `session.curl.sh` | Captured Salesforce session — recapture every few hours. |
| `raw-calendar.json` | Whatever JSON your calendar/AI tool produced. |
| `events.json` | Normalized events the tool sends to Salesforce. |
| `auth.json` | Optional dump of the parsed session info. |
| `credentials.json` / `token.json` | Only if you experiment with the Google OAuth path. |

None of these ever get committed (see `.gitignore`).

---

## Updating

```bash
cd ~/Documents/non-ticketed-activity-tracker
git pull
```

Editable install means new code takes effect immediately. If
`pyproject.toml` changed (new deps):

```bash
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
```

---

## Security

- Every user runs against **their own** Salesforce session. No shared
  credentials, no admin access, no security-token exchange.
- `session.curl.sh` is treated like a password — `.gitignore` blocks
  accidental commits, and Salesforce tokens self-expire within a few
  hours, so the blast radius of any leak is small.
- The (experimental) Google Calendar path uses a **read-only** OAuth
  scope (`calendar.readonly`) and only runs if you explicitly invoke
  `fetch-calendar`.
- The tool never deletes or modifies anything in Salesforce beyond the
  PS Tasks you ask it to create or update.

---

## Roadmap

- [ ] Finish Google Calendar OAuth path so `fetch-calendar` becomes a
      first-class option (token refresh, filter rules, dedupe).
- [ ] Slack helper to grab "yesterday's meetings" from a paste.
- [ ] One-shot end-to-end command (`activity-tracker week`) that runs
      convert → preview → push behind a single confirmation.

PRs and suggestions welcome.

---

## Need help?

Read **[SETUP.md](./SETUP.md)** first — it covers ~95% of issues
(including the most common one: re-capturing the session when the token
expires). For anything else, ping **Govind Nath Singh** on Slack with:

1. The exact command you ran.
2. The full error output.
3. *(Please don't paste your `session.curl.sh`. The first few lines are
   plenty.)*
