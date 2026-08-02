# linkedos

**Your AI ghostwriter for LinkedIn — in your own voice.** It writes the posts; you decide
what goes out. Everything runs on your own computer.

> linkedos **drafts; you approve.** It never scrapes, never touches anyone else's account,
> and never connects or messages on your behalf. The only things it sends to LinkedIn are
> posts — and, later, comments and likes — that you personally approved first.

---

# For You

## What it does

Think of it as a writing assistant that already knows how you sound.

1. **It learns your voice.** You give it a handful of your real past posts and a few notes
   on your style. Everything it writes from then on sounds like you — not like a robot.

2. **It writes drafts.** Give it a topic in your own words ("lessons from my first year
   managing a team") and it hands you a few different versions to pick from. Don't have a
   topic? Ask it to suggest some, based on what you write about.

3. **You stay in control.** Nothing is ever posted without you. You read each draft, tweak
   the wording, and either approve it or throw it out. It even warns you if a topic is too
   close to something you already posted.

4. **It plans your week.** Approve a batch of posts, choose a rhythm ("weekday mornings",
   "Mon / Wed / Fri at 9am"), and it lays out when each one should go live.

5. **It posts for you — at the right time.** Once you've approved and scheduled a post, it
   publishes on its own at the chosen moment, through LinkedIn's official tools. That's the
   *only* thing it ever does to your account, automatically.

## What it will never do

Peace of mind matters more than features here:

- ❌ It never scrolls, scrapes, or reads other people's LinkedIn.
- ❌ It never follows, connects, or sends messages.
- ❌ It never logs in as you through a browser or clicks around your account.
- ❌ It never touches anyone's account but your own.
- ✅ Everything it posts on your behalf — posts, and later comments and likes — **you
  approved first.** Nothing goes out unreviewed, and nothing is mass or indiscriminate.

Your drafts stay on your machine. Nothing is shared with anyone.

## The three things in front of you

- **A dashboard** — a simple web page where you review drafts, edit them, approve or
  reject, and set your schedule.
- **A quiet helper running in the background** — this is what publishes your approved posts
  at their scheduled time. You never have to think about it.
- **A cost tracker** — every draft costs a tiny amount (fractions of a cent) because it
  uses an AI service. You can see your running total any time, and set a monthly limit.

## What you'll need

- A LinkedIn account.
- An **Anthropic (Claude) account** for the AI writing — this is the one paid service.
  You add a key once and forget about it.
- Someone technical to do the one-time setup below (about 15 minutes). After that, it's
  just the dashboard.

> **Current status:** the writing, reviewing, and scheduling all work today. The final
> step — publishing automatically to LinkedIn — is still being built. For now you approve
> and schedule; the last hop to LinkedIn is finished by hand until that piece lands.

---

# For Developers

Local-first, single-user, Python only. A Streamlit dashboard and an APScheduler daemon
share one SQLite database (WAL mode, so the UI reads while the daemon writes). Layered
architecture — see `CLAUDE.md` for the binding rules.

## Requirements

- Python 3.12+ (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- `make`
- A local [Ollama](https://ollama.com/) server for embeddings (`ollama pull nomic-embed-text`)

## Setup

```bash
uv sync --all-groups          # create .venv and install runtime + dev dependencies
cp .env.example .env          # then open .env and fill in the keys you have
uv run alembic upgrade head   # create data/linkedos.db
```

Every field in `.env` has a default and the credentials start empty; keys become required
as each integration is exercised. Paths in `.env` are relative to the working directory —
run everything from the repo root.

## Running

Three independent processes, one database. Run any subset.

```bash
uv run linkedos status        # CLI: version + database health
uv run linkedos draft "topic" # generate draft variants (-n N for count, default 3)
uv run linkedos costs         # month-to-date model spend
make run-ui                   # Streamlit dashboard on :8501
make run-scheduler            # blocking scheduler daemon
```

Raw equivalents:

```bash
uv run streamlit run src/linkedos/ui/app.py
uv run python -m linkedos.scheduler
```

## Implementation status

**Done:**

- `core/` — settings (pydantic-settings), logging, errors
- `db/` — SQLAlchemy 2.0 models, session, repos, Alembic + schema-revision guard
- `ai/` — Claude and Ollama provider chokepoints, fake provider for tests, metered
  `AIClient` (routing + cost ledger), versioned prompt registry, embedding memory with
  NumPy cosine dedup
- `services/content` — `create_drafts`, `generate_batch`, `propose_topics`, `regenerate`
- `services/workflow` — the post state machine (sole writer of `posts.status`), single and
  batch approve/reject/revert/edit, all audited atomically
- `services/scheduling` — cadence grammar + pure `propose_schedule`
- CLI (`status`, `draft`, `costs`), Streamlit pages (content, approvals, logs), scheduler
  daemon with a heartbeat job

**Milestone 3 — publishing (pipeline done, live transport pending):**

- `integrations/linkedin.py` — the `LinkedInPublisher` protocol, a deterministic
  `FakeLinkedInPublisher` (offline, used by every test), and a `LiveLinkedInPublisher` stub
  that refuses until the real transport ships.
- `services/publishing` — `publish_post` and `publish_due`: take a `scheduled` post, hand
  its text to the publisher, and drive `workflow.mark_published` / `mark_failed`. LinkedIn
  accepts first, the row flips second; one failure never stops the sweep.
- `scheduler` — `publish_due_posts_job` registered on `PUBLISH_POLL_SECONDS`, gated on
  `PUBLISH_ENABLED` (off by default so a fresh install never fail-loops).
- Full offline test coverage of the pipeline against the fake publisher.

**Still to do for M3:** the live transport itself — OAuth 2.0 flow, token storage/refresh,
the author Person URN, and the `POST /rest/posts` call inside `LiveLinkedInPublisher`. Until
then `Post.linkedin_urn` is only ever written by the fake. Media (image/video/article) and a
UI "connect account" flow come after text publish works.

No engagement (comment/like) drafting yet — now unblocked by the policy change in
`CLAUDE.md`, and a natural next milestone. No metrics tracking, DB-backup, or weekly-digest
jobs (all noted as planned in `scheduler/jobs.py`).

## Development

`make check` is the gate — nothing merges without it.

```bash
make check     # ruff check + ruff format --check + mypy --strict src + pytest
make fmt       # ruff check --fix + ruff format
make test      # pytest, excluding tests/evals and anything marked `llm`
make migrate   # alembic upgrade head
```

The whole suite runs offline: no network, no real API calls, a temp SQLite DB per test, a
fake LLM provider. Prompt evaluations that need a live model live in `tests/evals/`, are
marked `@pytest.mark.llm`, and are excluded from `make check`:

```bash
uv run pytest tests/evals
```

`just` is not installed here; `make` is the runner and the target names mirror what a
`justfile` would use.

## Layout

```
src/linkedos/
  core/           settings, logging, errors — depends on nothing internal
  db/             engine, session, models, repositories
  ai/             LLM providers and prompts; one chokepoint per provider
  integrations/   LinkedIn official API and other external systems (M3)
  services/       orchestration — the only entry point for ui/ and scheduler/
  scheduler/      APScheduler daemon and job functions
  ui/             Streamlit dashboard and pages
  cli.py          `linkedos` console script
alembic/          migrations, wired to db.models.Base and settings.db_path
deploy/           launchd plist example (not installed)
data/             database, logs, backups — git-ignored
tests/            unit, integration, evals
```

Dependencies point downward only. `ui/` and `scheduler/` call `services/` and nothing
else, which is why the scheduler can do everything the UI can.

## Database

One SQLite file at `data/linkedos.db`, WAL mode. Schema changes go through Alembic:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

APScheduler keeps its own `apscheduler_jobs` table in the same file and manages it itself;
Alembic does not touch it.

## Secrets

Every secret lives in `.env` (git-ignored). `.env.example` lists every key with no values
and is the file to update when a new setting appears. Secrets are typed as `SecretStr`, so
they cannot be printed or logged by accident.

### Configuration

| Environment variable     | Default                                | Meaning                            |
| ------------------------ | -------------------------------------- | ---------------------------------- |
| `ANTHROPIC_API_KEY`      | *(empty)*                              | Claude API key                     |
| `LINKEDIN_CLIENT_ID`     | *(empty)*                              | LinkedIn OAuth app client ID       |
| `LINKEDIN_CLIENT_SECRET` | *(empty)*                              | LinkedIn OAuth app client secret   |
| `LINKEDIN_REDIRECT_URI`  | `http://localhost:8501/oauth/callback` | OAuth redirect target              |
| `OLLAMA_BASE_URL`        | `http://localhost:11434`               | Local Ollama server                |
| `EMBED_MODEL`            | `nomic-embed-text`                     | Embedding model; part of vector id |
| `LLM_TIMEOUT_S`          | `60`                                   | Per-request Claude timeout         |
| `OLLAMA_TIMEOUT_S`       | `60`                                   | Per-request Ollama timeout         |
| `DB_PATH`                | `data/linkedos.db`                     | SQLite file; anchors logs, backups |
| `PUBLISH_ENABLED`        | `false`                                | Daemon publishes scheduled posts   |
| `PUBLISH_POLL_SECONDS`   | `60`                                   | How often it checks for due posts  |
| `LOG_LEVEL`              | `INFO`                                 | Root log level for `linkedos.*`    |
| `MONTHLY_BUDGET_USD`     | `30`                                   | Soft cap on LLM spend per month    |

## Running the daemon under launchd (macOS)

The scheduler is a foreground process. To keep it alive across reboots, register it as a
launchd user agent. `deploy/com.linkedos.scheduler.plist.example` is a template, not an
installed file.

1. Copy it into place and fill in the placeholders:

   ```bash
   mkdir -p ~/Library/LaunchAgents
   sed -e "s|__PROJECT_DIR__|$(pwd)|g" \
       -e "s|__UV_PATH__|$(which uv)|g" \
       deploy/com.linkedos.scheduler.plist.example \
       > ~/Library/LaunchAgents/com.linkedos.scheduler.plist
   ```

2. Check it parses:

   ```bash
   plutil -lint ~/Library/LaunchAgents/com.linkedos.scheduler.plist
   ```

3. Load it. It starts immediately and again at every login:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.linkedos.scheduler.plist
   launchctl kickstart -k gui/$(id -u)/com.linkedos.scheduler
   ```

4. Confirm it is running, then watch it beat:

   ```bash
   launchctl print gui/$(id -u)/com.linkedos.scheduler | head -20
   tail -f data/logs/linkedos.log
   uv run linkedos status
   ```

To stop or remove it:

```bash
launchctl bootout gui/$(id -u)/com.linkedos.scheduler
rm ~/Library/LaunchAgents/com.linkedos.scheduler.plist
```

Notes:

- The plist sets `WorkingDirectory` to the repo, which is how the process finds `.env` and
  `data/`. Secrets stay in `.env`; never put a key in the plist, which is world-readable.
- `KeepAlive` restarts the daemon if it exits. Combined with `ThrottleInterval`, a crash
  loop backs off instead of spinning.
- launchd's own stdout/stderr go to `data/logs/launchd.{out,err}.log`. Application logs go
  to `data/logs/linkedos.log`, which rotates at 5 MB.
- After editing the plist, `bootout` then `bootstrap` again — launchd caches it.
