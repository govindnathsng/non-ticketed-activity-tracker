# Non-Ticketed Activity Tracker

> Bulk-create Salesforce **PS Task** records from your weekly meetings —
> instead of clicking through the "New PS Task" Lightning form 20-30 times
> a week.

**Setup time:** ~5 minutes · **Weekly use:** 3 commands, ~30 seconds.

➡️ **Jump straight to [SETUP.md](./SETUP.md)** for the full install +
walkthrough.

---

## How it works

You hand the tool a JSON of your meetings, plus a **one-time browser
session capture** (a `curl` dump from Chrome DevTools). The CLI then
**replays the exact same Lightning Aura Flow** your browser uses for the
**New PS Task** quick action — so:

- Every Salesforce validation rule fires as if you'd filled the form by hand
- No admin permissions, security tokens, or passwords needed
- Each user uses their own browser session — nothing is shared

For every event in your JSON, the tool runs two calls:

1. `startFlow → navigateFlow(NEXT → FINISH)` — creates the Task via the Flow
2. `saveRecord` — patches Subject / Task Type / Sub Type / Activity Currency /
   Time Spent / Status / Description so all fields land correctly

The created `00T…` Task IDs print on success, and the rows appear in your
standard PS report immediately.

---

## Commands at a glance

After `pip install -e .` (see SETUP.md):

| Command | What it does |
| --- | --- |
| `activity-tracker extract-auth --curl session.curl.sh` | Show the Salesforce session info parsed from your captured cURL (handy for Postman / debugging) |
| `activity-tracker convert-calendar --input raw-calendar.json --out events.json` | Turn a raw JSON dump from any calendar / AI tool into the format `flow-create` expects |
| `activity-tracker fetch-calendar --days 7 --out events.json` | (Optional, needs `[gcal]` extra) Pull events directly from Google Calendar via OAuth |
| `activity-tracker flow-create --curl session.curl.sh --input events.json --dry-run` | Preview the Tasks that *would* be created |
| `activity-tracker flow-create --curl session.curl.sh --input events.json` | Push them all to Salesforce |
| `activity-tracker flow-create … --limit 1` | Push only the first event — useful for a sanity check |
| `activity-tracker update-task --curl session.curl.sh --task-id 00T… --field "Subject=…"` | Patch any field on an already-created Task |

Run `activity-tracker --help` (or any subcommand `--help`) for full
options.

---

## Repo layout

```
non-ticketed-activity-tracker-v2/
├── SETUP.md                   ← full step-by-step install guide
├── README.md                  ← you are here
├── ANNOUNCEMENT_EMAIL.md      ← draft emails / Slack message for the team
├── pyproject.toml             ← package config (gives the `activity-tracker` command)
├── requirements.txt           ← raw deps (alternative to pip install -e .)
├── run.py                     ← fallback entrypoint (`python run.py …`)
├── events.schema.json         ← annotated JSON-schema reference
├── events.example.json        ← starter template
├── config.example.yaml        ← legacy config (only used by the old REST sync path)
├── scripts/                   ← weekly cron / launchd helpers
└── activity_tracker/          ← the Python package
    ├── cli.py                 ← all CLI commands
    ├── har_auth.py            ← HAR + cURL parsing → session info
    ├── sf_flow_client.py      ← Lightning Flow replay + saveRecord
    ├── salesforce_client.py   ← legacy REST client (kept for reference)
    ├── calendar_client.py     ← Google Calendar wrapper (optional)
    ├── sync.py                ← legacy sync engine (kept for reference)
    └── config.py              ← YAML loader (legacy)
```

---

## Files you'll create locally (all gitignored)

| File | What it holds |
| --- | --- |
| `session.curl.sh` | Your captured Salesforce session — recapture every few hours |
| `raw-calendar.json` | Whatever JSON your calendar/AI tool produced |
| `events.json` | Normalized events the tool sends to Salesforce |
| `auth.json` | Optional dump of parsed session info |
| `credentials.json` / `token.json` | Only if you use the Google OAuth fetch path |

None of these ever get committed (see `.gitignore`).

---

## Updating

```bash
cd ~/Documents/non-ticketed-activity-tracker-v2
git pull
```

Editable install means the new code takes effect immediately. If
`pyproject.toml` changed:

```bash
source .venv/bin/activate
pip install -e .
```

---

## Security

- Every user runs against **their own** Salesforce session. No shared
  credentials, no admin access.
- `session.curl.sh` is treated like a password (gitignored, never logged in
  plain text).
- Salesforce session tokens self-expire within a few hours — so the blast
  radius of any leak is small.
- The Google Calendar integration uses a **read-only** OAuth scope
  (`calendar.readonly`) and only runs if you explicitly invoke
  `fetch-calendar`.

---

## Need help?

Read [SETUP.md](./SETUP.md) first — it covers the 95% case. For anything
not in there, ping **Govind Nath Singh** on Slack with:

1. The exact command you ran
2. The full error output
3. (Don't paste your `session.curl.sh` — first few lines are plenty)
