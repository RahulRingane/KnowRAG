"""The domain layer — what KnowRAG *is*, independent of how it is delivered.

Contains the vocabulary every other layer speaks (`models`), the interfaces
they depend on (`ports`), and the rules that make this a fact-checking system
rather than a RAG demo:

    models.py              the types: Chunk, Claim, ClaimVerdict, ...
    ports.py               the interfaces: Retriever, EntailmentScorer, ...
    context.py             §5.1  how chunks are numbered for a generator
    verification.py        §6.2  whether a claim's evidence supports it
    assembly.py            §6.3  what the caller is shown, and what is audited
    text_normalization.py        repairing extractor-mangled text
    tables.py                    recovering tables from positioned text
    chunking.py                  how a document is cut into chunks

The constraint that keeps this layer honest: **it imports nothing from
`app.infrastructure`, `app.services`, or `app.api`.** Only `app.core` (for
configuration defaults and the shared exception types) and third-party
libraries that are pure computation.

So there is no torch here, no SQLAlchemy, no HTTP, no provider SDK — which
is why the rules above are testable offline in milliseconds, and why
replacing Qdrant, Postgres, or the LLM vendor does not touch a single line
of the logic that decides whether an answer is trustworthy.
"""
