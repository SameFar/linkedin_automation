.DEFAULT_GOAL := check
.PHONY: check fmt lint types test run-ui run-scheduler migrate

# `just` is not installed on this machine, so the task runner is make.
# Same targets, same commands: `make check` is the gate everything must pass.

check: lint types test

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy --strict src

test:
	uv run pytest -q -m "not llm" --ignore=tests/evals

fmt:
	uv run ruff check --fix .
	uv run ruff format .

run-ui:
	uv run streamlit run src/linkedos/ui/app.py

run-scheduler:
	uv run python -m linkedos.scheduler

migrate:
	uv run alembic upgrade head
