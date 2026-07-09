"""Provider adapters behind one shared protocol.

`base.py` defines the `LLMProvider` protocol. `claude.py` (Claude, via the `anthropic`
SDK), `ollama.py` (local embeddings and completions, via `httpx`), and `fake.py` — the
deterministic in-memory provider every offline test uses in place of the network —
implement it.
"""

from __future__ import annotations
