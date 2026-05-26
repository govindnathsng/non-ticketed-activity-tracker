# Announcement — Non-Ticketed Activity Tracker

Drop-in copy for the rollout. Replace anything in `[brackets]` and tighten
the tone to match your normal style. There are three pieces:

1. [Team email](#1-team-email-pub-ps-impl-apac)
2. [Manager email](#2-manager-email-for-forwarding-up)
3. [Slack message variant](#3-slack-message-channel-announcement)

---

## 1. Team email (Pub PS Impl APAC)

**To:** Pub PS Impl APAC  
**Cc:** [Manager name], Karan R, Deepika A  
**Subject:** New internal tool — bulk-create your weekly PS Tasks in Salesforce (~30 sec/week)

Hi team,

I've put together a small CLI tool that **bulk-creates "New PS Task"
records in Salesforce from a JSON list of your weekly meetings** — so
we stop clicking through the Lightning form 20–30 times a week.

**What it does**

- Takes a JSON list of meetings (date, time, title, participants).
- Logs into Salesforce using your own browser session — *no extra
  passwords, no admin perms, no security tokens*.
- Creates one PS Task per meeting with Subject, Task Type, Sub-Type,
  Activity Currency, Time Spent, Status, Description, and Activity Date
  all filled in correctly.
- Skips placeholders / OOO / lunch / birthdays automatically.
- Prints the Salesforce Task IDs at the end so you can spot-check.

**How it works (one sentence)**

It replays the exact same Lightning Aura Flow your browser uses for the
"New PS Task" quick action, which means every Salesforce validation rule
and field automation still fires — it's the same Flow, just driven from
a script instead of mouse clicks.

**What it does NOT do (yet)**

- 🚧 **It does not pull from Google Calendar automatically.** You give
  it the meeting JSON yourself. The easiest way is to ask Ada (or any
  AI assistant) for *"all my meetings last 7 days as JSON"* — there's a
  ready-made prompt in SETUP.md that produces exactly the right format.
  I'm planning to finish the GCal OAuth path next; happy to take help.
- It never reads, modifies, or deletes anything in Salesforce beyond
  the PS Tasks you ask it to create or update.

**Numbers from my own runs**

- One typical week's meetings → created in **~1.5–2 minutes**.
- First-time setup on a fresh laptop: **~5 minutes**.
- Weekly use after that: **3 commands, ~30 seconds** (capture session →
  convert JSON → push).

**How to try it**

```bash
git clone -b main [REPO_URL]
cd non-ticketed-activity-tracker-v2
# Then follow SETUP.md — 5-min walkthrough.
```

Repo: [REPO_URL]  
Setup guide: see `SETUP.md` in the repo.

Ping me on Slack and I'll do a 10-minute pairing to get your first run
in. Feedback / bugs / PRs very welcome.

Thanks,  
Govind

---

## 2. Manager email (for forwarding up)

**To:** [Manager name]  
**Cc:** Karan R  
**Subject:** Internal automation — non-ticketed PS Task logging (working prototype, ready to share)

Hi [Manager],

Sharing a small internal automation I prototyped that I think the wider
Pub PS Impl team could benefit from. Wanted your steer before I roll it
out beyond myself.

**Problem**

Logging non-ticketed activities (CSD calls, syncs, knowledge sessions,
huddles, etc.) as **New PS Tasks** in Salesforce is manual and
repetitive — roughly 6 clicks + 4 fields per entry, and most of us have
20–30 such activities a week. That's **~30–45 minutes/week per person**
of low-value clicking, and the friction discourages consistent logging —
which then under-reports our actual capacity in management dashboards.

**Solution**

A small command-line tool (Python, no external services, runs on the
user's own laptop) that:

1. Takes a JSON list of the week's meetings — either from any AI
   assistant ("give me last 7 days' meetings as JSON") or written by
   hand.
2. Replays the same Lightning Aura Flow the browser uses for the "New
   PS Task" quick action — so every Salesforce validation rule fires
   exactly as with manual entry.
3. Sets all standard Task fields (Subject, Task Type, Task Sub Type,
   Activity Currency, Time Spent in Minutes, Status, Description,
   Activity Date) correctly in one shot.
4. Prints the Salesforce Task IDs for verification.

**Honest scoping**

- ✅ **Already working end-to-end on my Salesforce account.** Most
  recent run: 5 PS Tasks for one workday created in under a minute, all
  visible in the standard PS report.
- 🚧 **Calendar input is still manual** — users paste a JSON of their
  week's meetings (an AI assistant produces it in ~10 seconds). Direct
  Google Calendar OAuth is in the codebase but not yet production-grade;
  finishing that is the next milestone.
- ✅ Uses each user's **own** browser session — no shared credentials,
  no admin permissions, no security-token exchange, no Connected App.
- ✅ Read-write only on PS Tasks attached to the user's own Delivery
  Task. Cannot touch anything else.

**Rollout proposal**

- Code + setup guide already in our internal git
  (`NEXUS/non-ticketed-activity-tracker-v2`).
- Fresh-laptop setup: ~5 minutes (`git clone` + `pip install -e .`),
  fully documented in `SETUP.md`.
- Suggest a **2-week soft-launch with APAC Pub PS Impl (~6 people)**,
  collect feedback and bug reports, then expand if useful.
- Zero infra / zero cost — runs entirely on the user's laptop.

**Estimated impact (conservative)**

|                       | Per person | Team of 6 |
| --------------------- | ---------- | --------- |
| Time saved / week     | 30–45 min  | 3–4.5 hrs |
| Time saved / year     | ~25–30 hrs | ~150–180 hrs |
| Logging consistency   | ↑          | ↑         |

Happy to demo this in a 15-minute slot whenever convenient. Code + setup
guide are ready to share as soon as you give the go-ahead.

Thanks,  
Govind

---

## 3. Slack message (channel announcement)

> 👋 Built a small tool that **bulk-creates "New PS Task" records in
> Salesforce from a JSON of your weekly meetings**. My last run did a
> full week in ~2 minutes.
>
> No passwords / no admin perms / no Google OAuth — paste your
> Salesforce session once (a 20-sec "Copy as cURL" from Chrome
> DevTools), and the tool replays the same Lightning Flow your browser
> uses. Calendar JSON is provided by you (Ada / ChatGPT prompt in
> SETUP.md generates it in ~10 seconds). Auto-GCal pull is on the
> roadmap.
>
> Setup is ~5 minutes the first time, ~30 seconds/week after that.
>
> Repo + setup guide: [REPO_URL]
>
> DM me if you'd like a 10-min walkthrough for your first run. 🙏

---

## Tips before you send

- ✅ Replace `[REPO_URL]` with the actual internal git link.
- ✅ Lead with the **manual JSON step** — set expectations that GCal
  auto-pull isn't done yet. Underpromising here avoids "I thought it
  read my calendar?" follow-ups.
- ✅ Address security explicitly. Most non-engineers will worry "is this
  bypassing security?" — the answer is **no**: it uses each user's own
  authenticated browser session, the same one Salesforce already trusts.
- ✅ Offer a 1:1 walkthrough. Most people prefer 10 minutes of pairing
  over reading a setup doc.
- ✅ Soft-launch with 5–6 people for ~2 weeks before broadcasting more
  widely.
- ❌ Don't promise an SLA. This is a self-service tool; help is on a
  best-effort basis.
- ❌ Don't share your own `session.curl.sh` to demo. Each user must
  capture their own; that's the entire security model.
