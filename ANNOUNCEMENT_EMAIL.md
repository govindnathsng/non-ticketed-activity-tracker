# Announcement Email Drafts

Two versions — pick whichever fits your tone. Both are short. Replace anything
in `[brackets]`.

---

## Version A — Team-focused, casual (recommended for first send)

**To:** Pub PS Impl IN team  
**Cc:** Karan R, Deepika A  
**Subject:** Tool to auto-create your weekly PS Tasks (saves ~30 mins/week)

Hi team,

Over the weekend I put together a small CLI tool that **auto-creates "New PS
Task" records in Salesforce from a JSON of your weekly meetings** — so you
don't have to click through the form 20-30 times a week.

**What it does**
- Reads a list of meetings (from Google Calendar, an AI assistant, or a hand-written JSON)
- Logs into Salesforce using your own browser session (no extra password setup)
- Creates one PS Task per meeting, with Subject, Task Type, Sub-Type, Activity
  Currency, Time Spent, Status, and Description all auto-filled
- Skips placeholders, OOO, birthdays, lunch, etc. automatically

**What it doesn't do**
- It never reads or modifies anything beyond creating those PS Tasks
- No admin permissions / security tokens / passwords are needed
- Every Salesforce validation rule still fires the same way — because it
  replays the same Lightning Flow your browser uses

**Numbers from my own first run**
- 23 tasks for May 11-22 → created in **~2 minutes**
- Setup on a fresh machine: **~5 minutes** (one-time)
- Weekly use after that: **3 commands, ~30 seconds**

**How to try it**

```bash
git clone -b main https://git.taboolasyndication.com/scm/nexus/non-ticketed-activity-tracker-v2.git
cd non-ticketed-activity-tracker-v2
# then follow SETUP.md (5-minute walkthrough)
```

Repo browser:
<https://git.taboolasyndication.com/projects/NEXUS/repos/non-ticketed-activity-tracker-v2>

Ping me on Slack and I'll help you get the first run going.

Happy to walk anyone through it 1:1 — should take 10 minutes start-to-finish.

Thanks,  
Govind

---

## Version B — Manager-focused, structured (for forwarding up)

**To:** [Manager name]  
**Cc:** Karan R  
**Subject:** Internal automation — non-ticketed activity logging (proposal + working prototype)

Hi [Manager],

Sharing a small internal automation I prototyped this weekend that I think
the wider Pub PS Impl team could benefit from.

**Problem**

Logging non-ticketed activities (CSD calls, syncs, knowledge sessions, etc.)
as **New PS Tasks** in Salesforce is a manual, repetitive task. Each entry is
~6 clicks + 4 fields, and most of us have 20-30 such activities a week. That's
roughly **30-45 minutes/week per person** of low-value clicking, and it
discourages people from logging consistently — which then under-reports our
actual capacity in management dashboards.

**Solution**

A small command-line tool (Python, no external services) that:

1. Takes a JSON of the week's meetings (from Google Calendar, an AI assistant,
   or a manual list)
2. Replays the exact same Lightning Flow the browser uses to create a PS Task
3. Sets all the standard Task fields (Subject, Task Type, Task Sub Type,
   Activity Currency, Time Spent in Minutes, Status, Description) correctly
4. Reports back the Salesforce Task IDs for verification

**Validation**

- End-to-end tested on my own Salesforce account: **23 real PS Tasks** for
  May 11-22 created in ~2 minutes, all visible in the standard PS report.
- Uses each user's own browser session — no shared credentials, no admin
  permissions, no security-token exchange.
- All Salesforce validation rules and field automations execute exactly as
  with manual entry (it's the same Aura Flow).

**Rollout proposal**

- Project lives in our internal Bitbucket (`NEXUS/non-ticketed-activity-tracker-v2`).
  Setup on a fresh laptop is ~5 minutes (`git clone` + `pip install -e .`);
  documented in `SETUP.md`.
- Suggest sharing with **APAC Pub PS Impl** first as a soft launch (~6 people),
  collect feedback for 1-2 weeks, then expand if useful.
- Zero infra/cost — runs entirely on the user's laptop.

**Estimated impact**

| | Per person | Team of 6 |
| --- | --- | --- |
| Time saved / week | 30-45 min | 3-4.5 hrs |
| Time saved / year | ~30 hrs | ~180 hrs |

Happy to demo this in a 15-min slot whenever convenient. Codebase + setup
guide are ready to share.

Thanks,  
Govind

---

## Slack message variant (for #pub-ps-impl-in or similar)

> 👋 Built a small tool over the weekend that auto-creates "New PS Task"
> records in Salesforce from a JSON of your weekly meetings. **23 tasks in
> ~2 mins** on my own run earlier today.
>
> No passwords / no admin perms / no Google OAuth needed — just paste your
> Salesforce session once a day. Full setup is ~5 mins, weekly use is 3
> commands.
>
> Repo + setup guide:
> <https://git.taboolasyndication.com/projects/NEXUS/repos/non-ticketed-activity-tracker-v2>
> Drop me a DM if you want me to walk you through the first run. 🙏

---

## Tips before you send

- ✅ Share the **Bitbucket repo link** (already internal-only). No one's
  `session.curl.sh` should ever leave their own machine — `.gitignore`
  already prevents commits, but reinforce the message verbally too.
- ✅ Mention security explicitly. Most non-engineers will worry "is this
  bypassing security?" — the answer is **no**: it uses each user's own
  authenticated browser session, the same one Salesforce already trusts.
- ✅ Offer a 1:1 walkthrough. Most people prefer 10 minutes of pairing over
  reading a setup doc.
- ✅ Suggest a 2-week soft-launch with a small group before announcing to a
  broader audience.
- ❌ Don't promise SLA / support. This is a self-service tool; help is on a
  best-effort basis.
