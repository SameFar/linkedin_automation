"""The content engine: turn a topic into N on-brand drafts.

`create_drafts` is the whole of Milestone 1's business logic, and it is deliberately
boring: retrieve, build, generate, save. Every piece of I/O sits behind a repository or
a provider, so the whole thing runs offline against a fake provider and a temp database.

Transactions are split around the network calls on purpose. Reading the voice profile
and the similar posts commits and closes before any model call starts; the drafts and
their embeddings are written afterwards in a second transaction. SQLite allows one
writer, and holding a write transaction open across three sequential LLM calls would
block the scheduler for the length of the run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from linkedos.ai import memory
from linkedos.ai.client import AIClient, get_client
from linkedos.ai.pricing import Tier
from linkedos.ai.prompts import registry
from linkedos.ai.providers.base import Message
from linkedos.core.errors import WorkflowError
from linkedos.core.logging import get_logger
from linkedos.db.models import Actor, AuditAction, EmbeddingKind, Post, PostStatus
from linkedos.db.repo import PostRepo
from linkedos.db.session import get_session
from linkedos.services.voice import Voice, get_or_seed
from linkedos.services.workflow import PostView, record_audit, to_view

logger = get_logger(__name__)

# Re-exported on purpose. `ui/` may import `services/` and nothing below it, so the
# types it needs to annotate its own helpers must be reachable from here.
__all__ = [
    "AIClient",
    "BatchResult",
    "BatchSummary",
    "DraftBatch",
    "PostStatus",
    "PostView",
    "Queue",
    "create_drafts",
    "generate_batch",
    "get_batch",
    "get_default_client",
    "get_pending_drafts",
    "get_post",
    "get_queue",
    "recent_batches",
    "regenerate",
]

PROMPT_VERSION = "post_v1"
TOPICS_PROMPT_VERSION = "topics_v1"
PURPOSE_DRAFT = "draft_post"
PURPOSE_EMBED = "embed_post"
PURPOSE_QUERY = "dedup_query"
PURPOSE_PROPOSE = "propose_topics"

#: How many past posts to feed the prompt as "don't repeat these".
CONTEXT_POSTS = 3
#: How many recent topics to show the topic proposer so it does not repeat them.
RECENT_TOPIC_CONTEXT = 20
#: Cosine above which two posts are "about the same thing" and the user is warned.
DEDUP_THRESHOLD = 0.82
MAX_TOKENS = 1024
MAX_VARIANTS = 10
#: A batch of more than a month of distinct topics is almost certainly a mistake.
MAX_BATCH_TOPICS = 31
MAX_PROPOSE_TOKENS = 512


@dataclass(frozen=True, slots=True)
class DraftBatch:
    """The result of one `create_drafts` run.

    Carries the run's cost so the CLI can report it without guessing which ledger rows
    belonged to this invocation.
    """

    posts: list[PostView]
    variant_group_id: str
    prompt_version: str
    cost_usd: float
    similar: list[memory.SimilarPost]

    @property
    def duplicate_warning(self) -> memory.SimilarPost | None:
        """The closest past post, if it is close enough to be worth mentioning."""
        if self.similar and self.similar[0].score >= DEDUP_THRESHOLD:
            return self.similar[0]
        return None


@dataclass(frozen=True, slots=True)
class Queue:
    """Everything the dashboard needs to describe the state of the pipeline."""

    pending: list[PostView]
    approved: list[PostView]
    scheduled: list[PostView]
    counts: dict[PostStatus, int]

    def count_of(self, status: PostStatus) -> int:
        return self.counts.get(status, 0)

    @property
    def pending_count(self) -> int:
        """Drafts awaiting a human decision — the number the Home page leads with."""
        return self.count_of(PostStatus.DRAFT)


def get_default_client() -> AIClient:
    """The production `AIClient`. Exposed so the UI can cache the handle without
    importing `ai/` — a layering rule the UI must not break for convenience."""
    return get_client()


def _format_similar(similar: list[memory.SimilarPost]) -> str:
    """Render past posts for the prompt's `previously_written` block."""
    if not similar:
        return "(nothing yet — this is a new subject for the author)"
    return "\n\n".join(
        f'<post similarity="{item.score:.2f}">\n{item.post.content.strip()}\n</post>'
        for item in similar
    )


def create_drafts(
    topic: str,
    n: int = 3,
    *,
    client: AIClient | None = None,
    batch_id: str | None = None,
) -> DraftBatch:
    """Generate `n` draft variants on `topic` and save them as `status=draft`.

    Each variant is its own model call. One call emitting N variants would be cheaper,
    but it makes the variants condition on each other and collapse toward a single
    voice; separate calls with an explicit variant index keep them genuinely distinct,
    and a partial failure costs one variant rather than the batch.

    Args:
        topic: What to write about, in the user's own words.
        n: How many variants. Each is a billed model call.
        client: Injected for tests. Defaults to the real Claude + Ollama client.
        batch_id: Tags every draft as part of one `generate_batch` run. `None` for a
            standalone single-topic run, which belongs to no batch.

    Returns:
        The saved drafts, the run's total cost, and any similar past posts found.
    """
    topic = topic.strip()
    if not topic:
        raise WorkflowError("topic must not be empty")
    if not 1 <= n <= MAX_VARIANTS:
        raise WorkflowError(f"n must be between 1 and {MAX_VARIANTS}, got {n}")

    resolved = client or get_client()
    voice = get_or_seed()

    # Read transaction: voice profile and prior art. Closed before any network call.
    with get_session() as session:
        similar = memory.similar_posts(
            session, topic, CONTEXT_POSTS, client=resolved, purpose=PURPOSE_QUERY
        )

    if similar and similar[0].score >= DEDUP_THRESHOLD:
        logger.warning(
            "topic %r resembles post id=%d (score=%.2f)",
            topic,
            similar[0].post.id,
            similar[0].score,
        )

    variants, cost = _generate(topic, n, voice=voice, similar=similar, client=resolved)

    variant_group_id = uuid.uuid4().hex
    posts = _persist(topic, variants, variant_group_id, client=resolved, batch_id=batch_id)

    logger.info(
        "drafted %d variant(s) topic=%r group=%s cost=$%.6f",
        len(posts),
        topic,
        variant_group_id,
        cost,
    )
    return DraftBatch(
        posts=posts,
        variant_group_id=variant_group_id,
        prompt_version=PROMPT_VERSION,
        cost_usd=cost,
        similar=similar,
    )


def _generate(
    topic: str,
    n: int,
    *,
    voice: Voice,
    similar: list[memory.SimilarPost],
    client: AIClient,
) -> tuple[list[str], float]:
    """Run the N model calls. Returns the texts and the summed ledger cost."""
    context = _format_similar(similar)
    texts: list[str] = []
    cost = 0.0

    for index in range(1, n + 1):
        prompt = registry.render(
            PROMPT_VERSION,
            topic=topic,
            voice_guidelines=voice.guidelines,
            voice_examples=voice.examples,
            similar_posts=context,
            variant_index=str(index),
            variant_count=str(n),
        )
        result = client.complete(
            [Message(role="user", content=prompt.user)],
            tier=Tier.DRAFT,
            purpose=PURPOSE_DRAFT,
            system=prompt.system,
            max_tokens=MAX_TOKENS,
            prompt_version=prompt.version,
        )
        texts.append(result.text.strip())
        cost += result.cost_usd

    return texts, cost


def _persist(
    topic: str,
    texts: list[str],
    variant_group_id: str,
    *,
    client: AIClient,
    batch_id: str | None = None,
) -> list[PostView]:
    """Save the drafts, then embed each one into memory.

    Three phases, and the order is forced by SQLite's single-writer rule. Embedding
    bills the ledger on its own connection, so it cannot run inside the transaction that
    inserts the posts — that transaction already holds the write lock, and the ledger
    insert would wait on it until the busy timeout fired.

    A draft without a vector is invisible to future dedup checks, so a failure to embed
    fails the run rather than silently degrading retrieval.

    Creating a draft is not a state *transition* — a post is born in `draft` — so this
    does not go through `workflow`. It still earns an audit row: the log should be able
    to answer "where did this text come from" as well as "who approved it".
    """
    with get_session() as session:
        posts = list(
            PostRepo(session).add_all(
                [
                    Post(
                        content=text,
                        status=PostStatus.DRAFT,
                        variant_group_id=variant_group_id,
                        batch_id=batch_id,
                        topic=topic,
                        prompt_version=PROMPT_VERSION,
                    )
                    for text in texts
                ]
            )
        )
        for post in posts:
            record_audit(
                session,
                actor=Actor.SYSTEM,
                action=AuditAction.CREATED,
                entity_id=post.id,
                detail=f"topic={topic!r} prompt={PROMPT_VERSION} group={variant_group_id}",
            )
        views = [to_view(post) for post in posts]

    # No transaction held here: each embed writes its own ledger row.
    vectors = [
        (view.id, memory.embed_text(view.content, client=client, purpose=PURPOSE_EMBED))
        for view in views
    ]

    with get_session() as session:
        for post_id, vector in vectors:
            memory.save_vector(
                session,
                kind=EmbeddingKind.POST,
                ref_id=post_id,
                vector=vector,
                model_name=client.embed_model,
            )
    return views


def get_pending_drafts(limit: int = 50) -> list[PostView]:
    """Drafts awaiting a human decision, newest first. The approval queue."""
    with get_session() as session:
        return [to_view(post) for post in PostRepo(session).list_by_status(PostStatus.DRAFT, limit)]


def get_queue(limit: int = 50) -> Queue:
    """A snapshot of the whole pipeline: what is pending, approved, and scheduled."""
    with get_session() as session:
        repo = PostRepo(session)
        return Queue(
            pending=[to_view(p) for p in repo.list_by_status(PostStatus.DRAFT, limit)],
            approved=[to_view(p) for p in repo.list_by_status(PostStatus.APPROVED, limit)],
            scheduled=[to_view(p) for p in repo.list_by_status(PostStatus.SCHEDULED, limit)],
            counts=repo.count_by_status(),
        )


def get_post(post_id: int) -> PostView:
    """One post, detached. Raises `WorkflowError` if it does not exist."""
    with get_session() as session:
        post = PostRepo(session).get(post_id)
        if post is None:
            raise WorkflowError(f"no post with id {post_id}")
        return to_view(post)


def regenerate(post_id: int, n: int = 1, *, client: AIClient | None = None) -> DraftBatch:
    """Draft `n` fresh variants on the same topic as an existing post.

    The original is left exactly as it was. Regenerating is not rejecting: a reviewer
    who wants another angle usually wants to compare it against the one they have, and
    deciding the old draft's fate is a separate, audited act.
    """
    original = get_post(post_id)
    batch = create_drafts(original.topic, n, client=client)

    with get_session() as session:
        record_audit(
            session,
            actor=Actor.HUMAN,
            action=AuditAction.REGENERATED,
            entity_id=post_id,
            detail=f"{n} new variant(s) in group {batch.variant_group_id}",
        )

    logger.info("regenerated %d variant(s) from post %d", n, post_id)
    return batch


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Everything one `generate_batch` run produced.

    A batch is a set of drafts created together so they can be reviewed, approved, and
    scheduled as a unit — a week of content set up in one sitting. `cost_usd` covers the
    whole run: every draft call, plus the topic-proposal call when topics were generated.
    """

    batch_id: str
    posts: list[PostView]
    topics: list[str]
    prompt_version: str
    cost_usd: float


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """A batch as the review UI lists it, without loading every draft's body."""

    batch_id: str
    topics: list[str]
    draft_count: int
    total_count: int


def _parse_topics(text: str, n: int) -> list[str]:
    """Pull up to `n` clean topic lines out of the model's reply.

    The prompt asks for one topic per line, but models still sometimes number or bullet
    them; strip that and drop blanks. Deduplicated case-insensitively so two phrasings of
    the same subject do not both survive.
    """
    topics: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*•0123456789.) ").strip().strip('"')
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        topics.append(cleaned)
        if len(topics) == n:
            break
    return topics


def propose_topics(n: int, *, client: AIClient) -> tuple[list[str], float]:
    """Have the model propose `n` on-brand topics from the voice profile and recent work.

    One billed call, recorded to the ledger like any other. Returns the topics and what
    the call cost.
    """
    voice = get_or_seed()
    with get_session() as session:
        recent = list(PostRepo(session).recent_topics(RECENT_TOPIC_CONTEXT))
    recent_block = "\n".join(f"- {topic}" for topic in recent) or "(none yet)"

    prompt = registry.render(
        TOPICS_PROMPT_VERSION,
        voice_guidelines=voice.guidelines,
        voice_examples=voice.examples,
        recent_topics=recent_block,
        count=str(n),
    )
    result = client.complete(
        [Message(role="user", content=prompt.user)],
        tier=Tier.DRAFT,
        purpose=PURPOSE_PROPOSE,
        system=prompt.system,
        max_tokens=MAX_PROPOSE_TOKENS,
        prompt_version=prompt.version,
    )

    topics = _parse_topics(result.text, n)
    if not topics:
        raise WorkflowError("topic proposer returned no usable topics")
    return topics, result.cost_usd


def generate_batch(
    topics: list[str] | int,
    per_topic: int = 1,
    *,
    client: AIClient | None = None,
) -> BatchResult:
    """Create a whole batch of drafts in one call, tagged with a shared `batch_id`.

    Two ways in, one result:

    * pass an explicit ``list[str]`` of topics, or
    * pass an ``int`` N and let the model propose N on-brand topics first.

    Either way, `per_topic` variants are drafted for each topic, all sharing one
    `batch_id` so the approval screen can review them as a set. Each topic still runs
    through `create_drafts`, so dedup, embedding, and per-variant auditing are unchanged.

    Raises:
        WorkflowError: on an out-of-range count, an empty topic list, or a bad `per_topic`.
    """
    if not 1 <= per_topic <= MAX_VARIANTS:
        raise WorkflowError(f"per_topic must be between 1 and {MAX_VARIANTS}, got {per_topic}")

    resolved = client or get_client()
    proposal_cost = 0.0

    if isinstance(topics, int):
        if not 1 <= topics <= MAX_BATCH_TOPICS:
            raise WorkflowError(f"count must be between 1 and {MAX_BATCH_TOPICS}, got {topics}")
        topic_list, proposal_cost = propose_topics(topics, client=resolved)
    else:
        topic_list = [topic.strip() for topic in topics if topic.strip()]
        if not topic_list:
            raise WorkflowError("no non-empty topics given")
        if len(topic_list) > MAX_BATCH_TOPICS:
            raise WorkflowError(f"a batch may hold at most {MAX_BATCH_TOPICS} topics")

    batch_id = uuid.uuid4().hex
    posts: list[PostView] = []
    cost = proposal_cost
    for topic in topic_list:
        drafted = create_drafts(topic, per_topic, client=resolved, batch_id=batch_id)
        posts.extend(drafted.posts)
        cost += drafted.cost_usd

    logger.info(
        "generated batch %s: %d topic(s) x %d = %d draft(s) cost=$%.6f",
        batch_id,
        len(topic_list),
        per_topic,
        len(posts),
        cost,
    )
    return BatchResult(
        batch_id=batch_id,
        posts=posts,
        topics=topic_list,
        prompt_version=PROMPT_VERSION,
        cost_usd=cost,
    )


def get_batch(batch_id: str, status: PostStatus | None = None) -> list[PostView]:
    """Every post in one batch, oldest first, optionally narrowed to a single status."""
    with get_session() as session:
        return [to_view(post) for post in PostRepo(session).list_by_batch(batch_id, status)]


def recent_batches(limit: int = 10) -> list[BatchSummary]:
    """The most recent batches, newest first, for the review screen's batch picker."""
    summaries: list[BatchSummary] = []
    with get_session() as session:
        repo = PostRepo(session)
        for batch_id in repo.recent_batch_ids(limit):
            posts = repo.list_by_batch(batch_id)
            topics: list[str] = []
            for post in posts:
                if post.topic not in topics:
                    topics.append(post.topic)
            drafts = sum(1 for post in posts if post.status is PostStatus.DRAFT)
            summaries.append(
                BatchSummary(
                    batch_id=batch_id,
                    topics=topics,
                    draft_count=drafts,
                    total_count=len(posts),
                )
            )
    return summaries
