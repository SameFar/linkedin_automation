"""Semantic memory over the user's own writing. NumPy and SQLite, no vector database.

That omission is deliberate. This corpus is one person's posts — hundreds of rows, maybe
thousands after years. A brute-force cosine over every stored vector is a single
`(n, d) @ (d,)` matmul that finishes in microseconds at that scale, and it costs nothing
to operate. An index would be slower to build than to scan, and a vector database would
be a second service to run, back up, and keep in sync with the SQLite file that already
holds the posts.

Vectors are stored as raw little-endian float32 bytes. `model_name` travels with every
vector because embeddings from two different models are points in two different spaces;
a cosine between them is a number, not a similarity. Every read filters on it.

**Embedding and saving are separate steps on purpose.** `embed_text` makes a network
call and bills the ledger on its own database connection; `save_vector` only writes.
SQLite allows one writer, so embedding from inside a transaction that has already
written deadlocks against the ledger insert. Callers embed first, then open the write
transaction. Reads are exempt — WAL lets a reader and a writer coexist.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from linkedos.ai.client import AIClient
from linkedos.core.errors import WorkflowError
from linkedos.core.logging import get_logger
from linkedos.db.models import Embedding, EmbeddingKind, Post
from linkedos.db.repo import EmbeddingRepo, PostRepo

logger = get_logger(__name__)

#: Little-endian float32. Fixed so a database file stays readable on any machine.
_DTYPE = np.dtype("<f4")


@dataclass(frozen=True, slots=True)
class Match:
    """One nearest-neighbour hit."""

    ref_id: int
    score: float


@dataclass(frozen=True, slots=True)
class SimilarPost:
    """A past post that resembles the query, and how closely."""

    post: Post
    score: float


def pack(vector: list[float]) -> bytes:
    """Serialise a vector for the `embeddings.vector` BLOB column."""
    return np.asarray(vector, dtype=_DTYPE).tobytes()


def unpack(blob: bytes) -> NDArray[np.float32]:
    """Deserialise one stored vector."""
    return np.frombuffer(blob, dtype=_DTYPE)


def _normalise(matrix: NDArray[np.float32]) -> NDArray[np.float32]:
    """Scale each row to unit length. A zero row stays zero and scores 0 against everything."""
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms != 0)


def embed_text(text: str, *, client: AIClient, purpose: str = "embed") -> list[float]:
    """Embed a single string. Billed to the ledger like any other call.

    **Never call this inside an open write transaction.** `AIClient` writes its ledger
    row on its own connection, and SQLite permits exactly one writer: an outer
    transaction that has already written holds the lock, the ledger insert waits on it,
    and the call dies with `database is locked` after the busy timeout. Embed first,
    then open the transaction that saves the result. `save_vector` is the other half.
    """
    if not text.strip():
        raise WorkflowError("cannot embed empty text")
    return client.embed([text], purpose=purpose).vectors[0]


def has_vector(session: Session, *, kind: EmbeddingKind, ref_id: int, model_name: str) -> bool:
    """Whether `(kind, ref_id)` already has a vector under `model_name`."""
    return EmbeddingRepo(session).get_for_ref(kind, ref_id, model_name) is not None


def save_vector(
    session: Session,
    *,
    kind: EmbeddingKind,
    ref_id: int,
    vector: list[float],
    model_name: str,
) -> Embedding:
    """Persist an already-computed vector against `(kind, ref_id)`.

    Pure database work — no network, no ledger — so it is safe inside a write
    transaction. Idempotent per embedding model: an existing vector is returned
    untouched rather than duplicated.
    """
    repo = EmbeddingRepo(session)
    existing = repo.get_for_ref(kind, ref_id, model_name)
    if existing is not None:
        return existing

    return repo.add(Embedding(kind=kind, ref_id=ref_id, model_name=model_name, vector=pack(vector)))


def most_similar(
    session: Session,
    text: str,
    k: int = 5,
    *,
    kind: EmbeddingKind = EmbeddingKind.POST,
    client: AIClient,
    purpose: str = "embed_query",
) -> list[Match]:
    """The `k` stored vectors of `kind` closest to `text`, by cosine similarity.

    Returns fewer than `k` matches when the store holds fewer, and an empty list when it
    holds none — a first run must not be an error path. The empty case short-circuits
    before embedding the query, so an empty corpus costs nothing.

    Scores are in [-1, 1]; for the non-negative vectors these embedding models produce,
    effectively [0, 1].
    """
    if k <= 0:
        return []

    rows = EmbeddingRepo(session).list_by_kind(kind, client.embed_model)
    if not rows:
        logger.debug("memory: no stored %s vectors for model=%s", kind, client.embed_model)
        return []

    matrix = _normalise(np.vstack([unpack(row.vector) for row in rows]))
    query = _normalise(np.asarray(embed_text(text, client=client, purpose=purpose), dtype=_DTYPE))

    if query.shape[0] != matrix.shape[1]:
        raise WorkflowError(
            f"embedding dimension changed for model {client.embed_model!r}: "
            f"stored {matrix.shape[1]}, got {query.shape[0]}. Re-embed the corpus."
        )

    scores = matrix @ query

    # argsort ascending, take the tail, reverse. Cheaper than a full sort for small k,
    # and identical for the sizes we actually see.
    top = np.argsort(scores)[-k:][::-1]
    return [Match(ref_id=rows[int(i)].ref_id, score=float(scores[int(i)])) for i in top]


def similar_posts(
    session: Session,
    topic: str,
    k: int = 3,
    *,
    client: AIClient,
    min_score: float = 0.0,
    purpose: str = "embed_query",
) -> list[SimilarPost]:
    """Past posts resembling `topic`, best first.

    The content service feeds these to the prompt so a new draft avoids repeating an
    angle, and surfaces them to the user as "you have written about this before".
    """
    matches = most_similar(
        session, topic, k, kind=EmbeddingKind.POST, client=client, purpose=purpose
    )
    kept = [match for match in matches if match.score >= min_score]
    if not kept:
        return []

    by_id = {post.id: post for post in PostRepo(session).get_many([m.ref_id for m in kept])}

    # A post can be deleted while its embedding lingers; skip orphans rather than crash.
    return [
        SimilarPost(post=by_id[match.ref_id], score=match.score)
        for match in kept
        if match.ref_id in by_id
    ]
