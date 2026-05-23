# Hosting on Nexus — Publisher Guide

Goal: Publish `activity-tracker` to your internal Nexus PyPI repo so teammates
can install it with one command:

```bash
pip install activity-tracker --index-url https://nexus.taboola.com/repository/<repo-name>/simple
```

> Replace `https://nexus.taboola.com/...` and `<repo-name>` with your actual
> internal URL — you'll get these from the Nexus admin (Part 1).

---

## Prerequisites — what's already done in this project

✅ `pyproject.toml` configured with package metadata, dependencies, and entry point.
✅ Source folder renamed `src/` → `activity_tracker/` (proper package).
✅ Console script registered: after install, `activity-tracker` is a global command.
✅ Wheel + sdist build verified (`activity_tracker-0.1.0-py3-none-any.whl`).
✅ Optional Google Calendar deps gated behind `pip install 'activity-tracker[gcal]'`.

You only need to:

1. Ask Nexus admin for a hosted PyPI repo (5-min ask, see Part 1).
2. Configure credentials on your laptop (~/.pypirc).
3. Build and upload (`python -m build && twine upload …`).
4. Share the install command in your team email.

---

## Part 1 — Ask the Nexus admin

Send this exact request to whoever runs Taboola's Nexus instance (DevOps /
Platform / Infra team). Two minutes of their time.

> **Subject:** Request: PyPI hosted repository on Nexus for internal Python tool
>
> Hi,
>
> I have a small internal Python CLI (`activity-tracker`, a Salesforce
> automation for our team) that I'd like to publish to Nexus so teammates can
> `pip install` it.
>
> Could you create:
>
> 1. A **hosted PyPI repository** named `internal-pypi-hosted` (or whatever
>    convention you use).
>    - Type: PyPI (hosted)
>    - Layout policy: Strict or Permissive (either fine)
>    - Deployment policy: `Allow redeploy` for `0.x` versions (so I can iterate)
> 2. (Optional) A **PyPI group repository** named `internal-pypi` that
>    combines `internal-pypi-hosted` + a proxy to pypi.org. With this,
>    teammates can use a single `--index-url` and get both internal +
>    public packages.
> 3. A Nexus user / token I can use to upload via `twine`. Username + a
>    password or token works.
>
> The package is internal-only — no external mirroring needed.
>
> Thanks,
> Govind

You'll get back something like:

| | Example |
| --- | --- |
| Upload URL | `https://nexus.taboola.com/repository/internal-pypi-hosted/` |
| Install URL | `https://nexus.taboola.com/repository/internal-pypi/simple` |
| Username | `govind-nath.s` |
| Token / password | `NPm3xxxxxxxxxxxxxxxx` |

Keep these handy for Part 2 and Part 5.

---

## Part 2 — One-time local setup (publisher side)

### 2a. Install build + twine

```bash
cd ~/Documents/Non-activity-tracking
source .venv/bin/activate
pip install --upgrade build twine
```

### 2b. Save Nexus credentials

Create `~/.pypirc` (mode 600):

```bash
cat > ~/.pypirc <<'EOF'
[distutils]
index-servers =
    nexus

[nexus]
repository = https://nexus.taboola.com/repository/internal-pypi-hosted/
username = govind-nath.s
password = NPm3xxxxxxxxxxxxxxxx
EOF
chmod 600 ~/.pypirc
```

> 🔒 `~/.pypirc` holds your Nexus token. Mode 600 means only you can read it.
> Never commit it to Git.

If your Nexus instance issues short-lived tokens, you can also use
environment variables instead and skip `~/.pypirc`:

```bash
export TWINE_USERNAME=govind-nath.s
export TWINE_PASSWORD=NPm3xxxxxxxxxxxxxxxx
export TWINE_REPOSITORY_URL=https://nexus.taboola.com/repository/internal-pypi-hosted/
```

---

## Part 3 — Build and upload

Every time you cut a release:

```bash
cd ~/Documents/Non-activity-tracking
source .venv/bin/activate

# 1. Bump version in pyproject.toml — e.g. 0.1.0 → 0.1.1
#    (Open it in your editor or run: sed -i '' 's/version = "0.1.0"/version = "0.1.1"/' pyproject.toml)

# 2. Clean previous artefacts
rm -rf dist build *.egg-info

# 3. Build wheel + sdist
python -m build

# 4. (Recommended) Validate metadata before upload
twine check dist/*

# 5. Upload
twine upload --repository nexus dist/*
```

Successful output ends with something like:

```
Uploading activity_tracker-0.1.1-py3-none-any.whl
100%  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 35.6 kB
Uploading activity_tracker-0.1.1.tar.gz
100%  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 37.3 kB
```

Verify it landed by hitting the repo URL in a browser — you should see your
package listed.

---

## Part 4 — Versioning rules (so you don't shoot yourself in the foot)

PyPI / Nexus generally **refuses re-uploads of the same version**. Always bump.

Suggested scheme (semver-ish):

| Change type | Bump | Example |
| --- | --- | --- |
| Bug fix, doc tweak | patch | `0.1.0` → `0.1.1` |
| New CLI command, new field support | minor | `0.1.1` → `0.2.0` |
| Breaking change (rename CLI, drop a flag, etc.) | major (or pre-1.0 minor) | `0.9.x` → `1.0.0` |

Tag the same version in Git so you can roll back:

```bash
git tag v0.1.1
git push origin v0.1.1
```

If you really need to overwrite (e.g. broken upload), ask the admin to allow
redeploy for that repo OR delete the bad artefact from Nexus UI, then re-upload.

---

## Part 5 — Teammate install (share in your email)

This is the snippet teammates paste into their terminal:

```bash
# 1. Create a virtual env (one time)
python3 -m venv ~/.virtualenvs/activity-tracker
source ~/.virtualenvs/activity-tracker/bin/activate

# 2. Install from Nexus
pip install activity-tracker \
    --index-url https://nexus.taboola.com/repository/internal-pypi/simple

# (Optional) If you want the Google Calendar integration too:
# pip install 'activity-tracker[gcal]' \
#     --index-url https://nexus.taboola.com/repository/internal-pypi/simple

# 3. Verify install
activity-tracker --help
```

To always use your Nexus index without typing `--index-url` every time, they
can add it to their pip config (one-time):

```bash
pip config set global.index-url https://nexus.taboola.com/repository/internal-pypi/simple
```

After this, `pip install activity-tracker` works without any extra flags.

---

## Part 6 — Updating teammates after a new release

You don't need to email everyone for a patch release. Tell them once:

```bash
# Whenever you've pushed a new version:
pip install --upgrade activity-tracker \
    --index-url https://nexus.taboola.com/repository/internal-pypi/simple
```

Or pin to a specific version for stability:

```bash
pip install 'activity-tracker==0.2.0' \
    --index-url https://nexus.taboola.com/repository/internal-pypi/simple
```

If you ship a breaking change, mention the new version in the team Slack
channel — that's usually enough.

---

## Part 7 — Where to put the source code (Git)

You'll want the source somewhere teammates can read it (Bitbucket / GitHub
Enterprise / GitLab, whatever Taboola uses):

```bash
cd ~/Documents/Non-activity-tracking
git init
git add .
# (Confirm .gitignore is excluding your live session/cookies/HAR files:)
git status --short | grep -E '(session\.curl\.sh|events\.json|auth\.json|.*\.har|raw-calendar\.json)' \
    && echo "❌ STOP — some sensitive files would be committed" \
    || echo "✓ Safe to commit"
git commit -m "Initial release of activity-tracker v0.1.0"
git remote add origin https://git.taboola.com/your-team/activity-tracker.git
git push -u origin main
```

Update `[project.urls]` in `pyproject.toml` with the real URL so the Nexus
package page links to the source.

---

## Troubleshooting (publisher side)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `twine upload` → `403 Forbidden` | Wrong creds / token doesn't have deploy permission | Re-check `~/.pypirc`, ask admin for a token with deploy scope |
| `twine upload` → `400 File already exists` | Trying to upload a version that's already on Nexus | Bump version in `pyproject.toml`, rebuild, re-upload |
| `twine upload` → SSL error | Corporate proxy / cert chain issue | Set `--verbose` and check; may need `REQUESTS_CA_BUNDLE` env var pointing to corp CA |
| `twine check dist/*` warns about long description | README.md has rST/Markdown issues | Usually safe to ignore; or set `long_description_content_type` (already done — uses `readme = "README.md"`) |
| Build fails — `error: package directory 'src' does not exist` | You forgot the rename | Run `mv src activity_tracker` and retry |
| Wheel is built but `activity-tracker` command not found after install | Entry point not picked up | Confirm `pyproject.toml` has `[project.scripts]` section — already present |

---

## Troubleshooting (teammate side)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Could not find a version that satisfies the requirement activity-tracker` | Wrong index URL, or package not yet uploaded | Verify the URL, re-check with the publisher that it's been pushed |
| `pip install` works but `activity-tracker` command not found | venv not activated | `source ~/.virtualenvs/activity-tracker/bin/activate` |
| `ModuleNotFoundError: google.auth…` when running `fetch-calendar` | Installed without `[gcal]` extra | `pip install --upgrade 'activity-tracker[gcal]' --index-url …` |

---

## Quick reference — entire publish flow in one block

```bash
# Whenever you ship a release:
cd ~/Documents/Non-activity-tracking
source .venv/bin/activate

# 1. Bump version in pyproject.toml (e.g. 0.1.0 → 0.1.1)
$EDITOR pyproject.toml

# 2. Clean + build + check + upload
rm -rf dist build *.egg-info
python -m build
twine check dist/*
twine upload --repository nexus dist/*

# 3. Git tag for traceability
NEW_VERSION=$(grep -E '^version =' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')
git add . && git commit -m "Release v${NEW_VERSION}"
git tag "v${NEW_VERSION}" && git push origin main --tags

# 4. (Optional) Announce in Slack
echo "🚀 activity-tracker v${NEW_VERSION} is live on Nexus. Upgrade with:
  pip install --upgrade activity-tracker --index-url https://nexus.taboola.com/repository/internal-pypi/simple"
```
