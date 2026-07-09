# linkedos

A local-first AI copilot for LinkedIn personal branding. Python only, single user, runs
on your own machine. A Streamlit dashboard and a scheduler daemon share one SQLite
database.

**linkedos drafts; you publish.** It never scrapes, never automates comments, likes,
connections, or messages, and never drives a browser at LinkedIn. Publishing your own
posts through LinkedIn's official API is the only outbound action it will ever take.

This repository is currently a skeleton: the foundation is real, the features are not.

## Requirements

- Python 3.12+ (pinned to 3.12 in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- `make`

## Setup

```bash
uv sync --all-groups          # create .venv and install runtime + dev dependencies
cp .env.example .env          # then open .env and fill in the keys you have
uv run alembic upgrade head   # create data/linkedos.db
```

None of the keys in `.env` are needed to boot the skeleton — every field has a default
and the credentials start empty. They become required as each integration lands.

## Running

Three processes, one database. Each is independent; run any subset.

```bash
uv run linkedos status                            # CLI: version + database health
make run-ui                                       # Streamlit dashboard on :8501
make run-scheduler                                # blocking scheduler daemon
```

The underlying commands, if you prefer them raw:

```bash
uv run streamlit run src/linkedos/ui/app.py
uv run python -m linkedos.scheduler
```

Paths in `.env` are relative to the working directory, so run everything from the repo
root.

## Development

`make check` is the gate. Nothing merges without it.

```bash
make check          # ruff check + ruff format --check + mypy --strict src + pytest
make fmt            # ruff check --fix + ruff format
make test           # pytest, excluding tests/evals and anything marked `llm`
make migrate        # alembic upgrade head
```

`just` is not installed on this machine, so the task runner is `make`. The targets match
the names a `justfile` would have used.

The whole suite runs offline: no network, no real API calls, a temp SQLite database per
test. Prompt evaluations that need a live model live in `tests/evals/`, are marked
`@pytest.mark.llm`, and are excluded from `make check`. Run them deliberately:

```bash
uv run pytest tests/evals
```

## Layout

```
src/linkedos/
  core/           settings, logging, errors — depends on nothing internal
  db/             engine, session, models, repositories
  ai/             LLM providers and prompts; one chokepoint per provider
  integrations/   LinkedIn official API and other external systems
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
else, which is why the scheduler can do everything the UI can. See `CLAUDE.md`.

## Database

One SQLite file at `data/linkedos.db`, in WAL mode so the dashboard can read while the
daemon writes. Schema changes go through Alembic:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

APScheduler keeps its own `apscheduler_jobs` table in the same file. It manages that
table itself; Alembic does not touch it.

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

- The plist sets `WorkingDirectory` to the repo, which is how the process finds `.env`
  and `data/`. Secrets stay in `.env`; never put a key in the plist, which is
  world-readable.
- `KeepAlive` restarts the daemon if it exits. Combined with `ThrottleInterval`, a crash
  loop backs off instead of spinning.
- launchd's own stdout/stderr go to `data/logs/launchd.{out,err}.log`. Application logs
  go to `data/logs/linkedos.log`, which rotates at 5 MB.
- After editing the plist, `bootout` then `bootstrap` again — launchd caches it.

## Secrets

Every secret lives in `.env`, which is git-ignored. `.env.example` lists every key with
no values and is the file to update when a new setting appears. Secrets are typed as
`SecretStr`, so they cannot be printed or logged by accident.

## Configuration

| Environment variable     | Default                              | Meaning                            |
| ------------------------ | ------------------------------------ | ---------------------------------- |
| `ANTHROPIC_API_KEY`      | *(empty)*                            | Claude API key                     |
| `LINKEDIN_CLIENT_ID`     | *(empty)*                            | LinkedIn OAuth app client ID       |
| `LINKEDIN_CLIENT_SECRET` | *(empty)*                            | LinkedIn OAuth app client secret   |
| `LINKEDIN_REDIRECT_URI`  | `http://localhost:8501/oauth/callback` | OAuth redirect target            |
| `OLLAMA_BASE_URL`        | `http://localhost:11434`             | Local Ollama server                |
| `DB_PATH`                | `data/linkedos.db`                   | SQLite file; anchors logs, backups |
| `LOG_LEVEL`              | `INFO`                               | Root log level for `linkedos.*`    |
| `MONTHLY_BUDGET_USD`     | `30`                                 | Soft cap on LLM spend per month    |
