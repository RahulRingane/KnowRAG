# KnowRAG

Monorepo root. The full architecture/design doc lives one level up in
`.claude/CLAUDE.md` (outside this repo) — read it before changing pipeline
behavior.

## Project Structure

- `frontend/` — Next.js frontend
- `backend/` — RAG/fact-verification backend (formerly `rag/`)

**`rag/` was renamed to `backend/` on 2026-08-11.** Only the folder name
changed: nothing inside `app/` moved, no import paths changed, and the compose
service is still `app` — there is no service named `rag` or `backend`. The
backend still does RAG internally, so `RAG`/`rag` as the technical concept
(docstrings, `rag_pipeline`-style naming) is untouched and should stay that way.

Backend commands (`docker compose`, `pytest`, `uvicorn`, `python -m app.cli.*`)
run from `backend/`, not from this directory:

```bash
cd backend
docker compose -p knowrag up -d          # pin -p: bare compose would derive `backend`
pytest -m "not slow"                     # 425 tests, offline
```

Frontend commands run from `frontend/` (`npm run dev`, `npm run test:run`).
