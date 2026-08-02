# linkedos — standing rules for every session

Read this before touching anything. These rules bind every session, human or agent.

## Stack

Language: Python 3.12+. Stack is fixed: sqlite + sqlalchemy 2.0 + alembic +
pydantic (+pydantic-settings) + httpx + anthropic + apscheduler + tenacity + streamlit +
pandas. Dev tools: uv, ruff, mypy, pytest. Do not add any dependency not on this list
without stopping and asking me first, with a one-line justification. No LangChain, no
LiteLLM, no vector DB, no Celery/Redis, no Postgres.

## Architecture

Architecture is layered: `ui/` and `scheduler/` may only call `services/`; `services/`
orchestrate `ai/`, `integrations/`, `db/`; `core/` depends on nothing internal.
Dependencies point downward only. UI holds no business logic. The scheduler must be able
to do everything the UI can, via the same service layer.

## Quality

Everything is typed and passes `mypy --strict`. Everything is formatted and linted with
ruff. Every feature ships with tests that run offline (no network, no real API calls) via
a fake LLM provider and a temp SQLite DB.

## External calls

Every external call (Claude API, LinkedIn API, Ollama) goes through one chokepoint per
integration, wrapped with tenacity retry (transient errors only), an explicit timeout,
structured logging, and — for LLM calls — token/cost accounting.

## Forbidden features

Never build, even if asked elsewhere: scraping LinkedIn or any site; automated connecting
or messaging on LinkedIn; automated job applications; any browser automation pointed at
LinkedIn. The system drafts; a human approves.

Permitted automated outbound actions — all through LinkedIn's official API, all on the
user's own account only: publishing the user's own posts, and posting comments and
reactions (likes) the user has approved. Engagement is on the user's behalf and never mass
or indiscriminate: comments and likes run through the same draft-then-approve gate every
post does, and each is recorded in the audit log. No scraping, no browser automation, no
acting on anyone else's account.

## Secrets

Secrets live in `.env` (git-ignored) loaded via pydantic-settings. Never hardcode, print,
log, or commit a key or token. Maintain `.env.example`.

## Process

Work in small, reviewable commits with meaningful messages. Stop and ask before any
destructive operation or schema-breaking change.

---

## Local notes

- `just` is not installed on this machine; the task runner is `make`. `make check` is the
  gate: ruff check, ruff format --check, mypy --strict src, pytest (excluding `tests/evals`
  and anything marked `llm`).
- The launchd plist in `deploy/` is an example only. It is not installed.
