"""The infrastructure layer — everything that talks to something external.

    db/       Postgres: engine, ORM tables, repositories
    search/   Qdrant and Elasticsearch: indexing and hybrid retrieval
    ml/       local model weights: embeddings, reranker, NLI scorer
    llm/      generation providers: Gemini, OpenAI
    pdf/      reading files off disk

Every module here is an *adapter*: it implements a port declared in
`app.domain.ports` and translates between that domain interface and some
vendor API. The direction of the dependency is the whole point — this layer
imports the domain, and the domain never imports this layer.

Two rules hold throughout:

1. **Nothing connects or loads at import time.** Clients and models are built
   by lazily-cached factories on first use. Importing this package must stay
   cheap enough that a CLI entrypoint or an offline unit test pays nothing
   for infrastructure it never touches.
2. **No vendor type crosses the boundary upward.** A repository returns
   `DocumentRecord`, not a SQLAlchemy row; a retriever returns `Chunk`, not a
   Qdrant `ScoredPoint`; a generator raises `GenerationUnavailable`, not
   `google.genai.errors.APIError`.
"""
