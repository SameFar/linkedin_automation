"""Provider adapters behind one shared protocol.

Planned: `anthropic.py` (Claude, via the `anthropic` SDK), `ollama.py` (local models,
via `httpx`), and `fake.py` — the deterministic in-memory provider every offline test
uses in place of the network.
"""

from __future__ import annotations
