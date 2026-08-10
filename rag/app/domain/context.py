"""§5.1 — presenting retrieved chunks to a generator.

One function, extracted from the old `app/chain.py`. It lives in the domain
layer rather than beside the orchestration because the citation-tag
convention it establishes is the contract three other places depend on:
`app.domain.verification.build_chunk_tag_map` reproduces the same numbering,
`ClaimVerdict.citations` carries those tags back, and
`app.domain.assembly.assemble_answer_text` re-appends them to the answer.
Only one module gets to decide what `[C1]` means, and this is it.
"""

from __future__ import annotations

from app.domain.models import Chunk


def format_context(chunks: list[Chunk]) -> tuple[str, dict[str, Chunk]]:
    """Number each chunk `[C1]`, `[C2]`, ... per §5.1.

    Tags are assigned purely positionally (first chunk -> C1, second -> C2,
    ...), never content-hashed, so calling this twice on an identical input
    list yields identical tags every time — the stability §5.1 requires.

    Returns `(formatted_context_block, tag_to_chunk_map)`. The map is what
    lets verification resolve a `Claim.citations` entry like `"C2"` back to
    the actual `Chunk` it refers to.
    """
    blocks: list[str] = []
    tag_map: dict[str, Chunk] = {}

    for i, chunk in enumerate(chunks, start=1):
        tag = f"C{i}"
        blocks.append(
            f"[{tag}] (doc: {chunk.document_id}, chunk: {chunk.chunk_index})\n{chunk.text}"
        )
        tag_map[tag] = chunk

    return "\n\n".join(blocks), tag_map
