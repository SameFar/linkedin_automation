"""Adapters for external non-LLM systems — currently the LinkedIn official API.

One chokepoint per integration: explicit timeout, tenacity retry on transient errors,
structured logging, no secrets in the log record.

Hard boundary (see CLAUDE.md): this package never scrapes, never drives a browser, and
never comments, likes, connects, or messages. Publishing the user's own posts through
LinkedIn's official API is the only automated outbound action that will ever live here.
"""

from __future__ import annotations
