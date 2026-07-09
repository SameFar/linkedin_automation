"""LLM access: provider adapters, prompt templates, token and cost accounting.

Every model call leaves the process through exactly one chokepoint per provider in
`ai/providers/`, wrapped with an explicit timeout, tenacity retry on transient errors
only, structured logging, and cost accounting. Services call `ai/`; `ai/` calls out.
"""

from __future__ import annotations
