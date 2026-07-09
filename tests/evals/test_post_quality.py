"""Eyeball evaluation of `post_v1` against the real Claude API.

═══════════════════════════════════════════════════════════════════════════════
 THIS TEST SPENDS REAL MONEY. It calls the Anthropic API once per golden topic.
 At Haiku-class prices and eight topics, one run is a fraction of a cent — but it
 is not zero, and it needs a live `ANTHROPIC_API_KEY`.

 It is excluded from `make check` twice over: `-m "not llm"` skips the marker, and
 `--ignore=tests/evals` skips the directory. Nothing in CI will ever run it.

 Run it deliberately, and read the output:

     uv run pytest tests/evals -m llm -s

 The `-s` matters. This test asserts almost nothing. Its product is the printed
 drafts, which a human reads to decide whether the prompt still writes well. The
 assertions below only catch collapse — an empty response, a wall of text, a
 leaked instruction — not mediocrity. Mediocrity is your job to notice.
═══════════════════════════════════════════════════════════════════════════════

Embeddings still go through the fake provider here: this eval is about prose quality, and
there is no reason to require a running Ollama daemon to look at it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.pricing import Tier
from linkedos.ai.prompts import registry
from linkedos.ai.providers.base import Message
from linkedos.ai.providers.claude import ClaudeProvider
from linkedos.core.config import get_settings
from linkedos.services.content import MAX_TOKENS, PROMPT_VERSION

GOLDENS = Path(__file__).parent / "post_goldens.md"

#: Phrases that mean the prompt's anti-cliché section stopped working.
BANNED = [
    "i'm thrilled",
    "i am thrilled",
    "excited to announce",
    "game-changer",
    "game changer",
    "let that sink in",
    "deep dive",
    "in today's fast-paced",
    "here's the thing",
    "the harsh truth",
    "what's your take",
]

VOICE_GUIDELINES = """\
Write plainly and concretely. Short sentences. No emoji, no hashtags, no hooks.
Take a position. Never open with a question. Never end with a call to engage.
"""

VOICE_EXAMPLES = """\
Shipped the migration today. Four hours, no downtime, and nobody noticed — which is
the only review a migration ever gets.

---

We deleted 3,000 lines this quarter. That is the work I am proudest of. The feature
nobody uses still costs you every time someone reads the file it lives in.
"""


def load_goldens() -> list[str]:
    """Top-level `-` bullets from the golden set. Indented bullets are human notes."""
    text = GOLDENS.read_text(encoding="utf-8")
    return [match.group(1).strip() for match in re.finditer(r"^- (.+)$", text, re.MULTILINE)]


@pytest.fixture(scope="module")
def live_client() -> AIClient:
    settings = get_settings()
    if not settings.anthropic_api_key.get_secret_value():
        pytest.skip("ANTHROPIC_API_KEY is not set; skipping the paid eval")
    return AIClient(ClaudeProvider.from_settings())


def test_goldens_file_parses() -> None:
    """Cheap, offline, unmarked: the eval's own input must be well-formed."""
    topics = load_goldens()

    assert len(topics) == 8
    assert all(topic and not topic.startswith("-") for topic in topics)


@pytest.mark.llm
@pytest.mark.parametrize("topic", load_goldens())
def test_draft_a_golden_topic(topic: str, live_client: AIClient, temp_db: Path) -> None:
    """Generate one real draft per golden topic and print it for a human to judge."""
    prompt = registry.render(
        PROMPT_VERSION,
        topic=topic,
        voice_guidelines=VOICE_GUIDELINES,
        voice_examples=VOICE_EXAMPLES,
        similar_posts="(nothing yet — this is a new subject for the author)",
        variant_index="1",
        variant_count="1",
    )
    result = live_client.complete(
        [Message(role="user", content=prompt.user)],
        tier=Tier.DRAFT,
        purpose="eval_draft",
        system=prompt.system,
        max_tokens=MAX_TOKENS,
        prompt_version=prompt.version,
    )
    draft = result.text

    print(f"\n{'═' * 78}\nTOPIC: {topic}\n{'═' * 78}\n{draft}\n")
    print(
        f"[{result.response.input_tokens} in / {result.response.output_tokens} out "
        f"· ${result.cost_usd:.6f} · {result.latency_ms}ms]"
    )

    # Collapse detection only. Quality is judged by the human reading the output above.
    assert draft.strip(), "model returned an empty draft"
    assert len(draft.split()) < 400, "draft is far longer than the prompt's 200-word ceiling"
    assert "<topic>" not in draft, "instruction tags leaked into the output"
    assert "voice_examples" not in draft, "prompt internals leaked into the output"

    lowered = draft.lower()
    found = [phrase for phrase in BANNED if phrase in lowered]
    assert not found, f"banned phrase(s) survived the anti-cliché constraints: {found}"
