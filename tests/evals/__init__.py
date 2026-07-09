"""Prompt evaluations against a real LLM.

Excluded from `make check`: these cost money and need the network. Mark every test here
with `@pytest.mark.llm` and run them deliberately with `uv run pytest tests/evals`.
"""

from __future__ import annotations
