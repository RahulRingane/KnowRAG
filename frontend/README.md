# KnowRAG Frontend

Next.js UI for KnowRAG — a fact-checking RAG system that answers questions
strictly from retrieved evidence and marks every claim with what that
evidence does or does not establish. See the [monorepo
README](../README.md) and [`frontend_plan.md`](../frontend_plan.md) for the
full design.

**Status:** scaffold, design system, and app shell (WS-A). The query
interface, auth, documents/history/status pages, and the API client land in
later workstreams — the routes currently render "coming in a later
workstream" placeholders.

## Prerequisites

- Node **v22** and npm **9+** (confirmed working: Node v22.22.1 / npm 9.2.0).
- The backend running — see below.

## Install

```bash
npm install
```

## Environment

```bash
cp .env.example .env.local
```

`.env.local` is gitignored. The only variable is:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Validated by `src/config/env.ts` (Zod) at module load — an invalid or
missing URL fails loudly instead of producing silent fetch failures. This is
the **only** file that reads `process.env`; nothing else in the app should.

- `8000` — the backend's `app` container (`docker compose up -d`).
- `8001` — a host-side `uvicorn --reload` dev server (`../rag/`'s
  hot-reload workflow; the container does not hot-reload).

## Run the backend

The frontend talks to the KnowRAG API over HTTP — nothing works without it
running and reachable from the browser:

```bash
cd ../rag
docker compose up -d
```

The backend must also allow this app's origin in `CORS_ORIGINS` (backend
`.env`) — e.g. `http://localhost:3000` for `next dev`'s default port.
Without it, every browser request from this app dies at CORS preflight.

**Auth:** the backend uses first-user-registers-then-closed signup. Once a
user exists, `/auth/register` returns `403` for anyone else. Register the
first account at `/register` in this app before anyone else does, or you'll
need direct database access to reset it.

## Develop

```bash
npm run dev        # http://localhost:3000
```

## Build / typecheck / lint / test

```bash
npm run build       # production build
npx tsc --noEmit    # or: npm run typecheck
npm run lint
npm run test:run     # vitest, single run
npm run test         # vitest, watch mode
```

The test suite mocks the network with MSW — no live backend and no LLM spend
required to run it.

## Tech stack

- **Next.js 15** (App Router), **TypeScript** strict + `noUncheckedIndexedAccess`
  (the API returns parallel `string[]` arrays — `citations` / `chunk_ids` —
  where an off-by-one is a real risk).
- **Tailwind v4** (CSS-first config — no `tailwind.config.ts`) + **shadcn/ui**
  (component source lives in `src/components/ui/`, built on Base UI
  primitives for keyboard nav and ARIA).
- **TanStack Query v5** for server state, **Zod v4** at the API boundary.
- **Vitest + Testing Library + MSW** for tests.

## Project structure

```
src/
├── app/                 App Router. (app)/ = authenticated shell (nav, one <main>).
│                        (auth)/ = login/register, no nav.
├── components/
│   ├── ui/               shadcn primitives
│   ├── layout/            shell, nav, theme toggle, error/empty states, skeletons
│   ├── auth/ query/ verification/ documents/ history/ system/   (later workstreams)
├── lib/
│   ├── utils/verdict.ts  verdict -> colour/label/icon mapping (the only place this is defined)
│   ├── api/ auth/ schemas/ hooks/ storage/   (later workstreams)
├── types/api.ts          API contract types (later workstream)
└── config/env.ts         the only process.env read in this codebase
```

## Design tokens — verdict colours

Four CSS variables in `src/app/globals.css`, defined once for light and dark,
representing the four NLI verdict states:

| Token                    | Meaning                                                       | Light                                 | Dark                    |
| ------------------------ | ------------------------------------------------------------- | ------------------------------------- | ----------------------- |
| `--verdict-supported`    | evidence confirms the claim                                   | `oklch(0.517 0.13 165)` (teal-green)  | `oklch(0.652 0.12 165)` |
| `--verdict-unsupported`  | evidence could not confirm — **neutral/caution, not failure** | `oklch(0.557 0.15 75)` (amber)        | `oklch(0.709 0.14 75)`  |
| `--verdict-contradicted` | evidence actively disagrees — **the alarming one**            | `oklch(0.495 0.20 25)` (red)          | `oklch(0.705 0.17 25)`  |
| `--verdict-refusal`      | system declined to answer — informational                     | `oklch(0.523 0.15 265)` (blue-violet) | `oklch(0.648 0.12 265)` |

Each also has a `-bg` wash variant (e.g. `--verdict-supported-bg`) for badge/alert
fills, and all eight are exposed as Tailwind utilities (`text-verdict-supported`,
`bg-verdict-contradicted-bg`, `border-verdict-refusal`, ...) via the `@theme inline`
block — never hardcode a colour for a verdict; import `getVerdictMeta` from
`src/lib/utils/verdict.ts` instead.

**Why these four hues, not red/green:** red/green as the primary signal is
unreadable for ~8% of men (red-green colour blindness). These hues are spread
around the wheel — red, amber, teal, blue-violet — rather than a red/green
opposition, and teal (which keeps a blue component) and blue-violet remain
distinguishable from red for protanopes/deuteranopes specifically because
neither depends on the red-green axis.

**Contrast:** every value clears WCAG AA (≥4.5:1 for normal text) as an
icon/text colour against this file's `--background` — light mode ratios
range 4.83–6.81:1, dark mode 6.00–7.51:1, computed via OKLCH→sRGB conversion
and the standard WCAG relative-luminance formula.

**Greyscale:** lightness is deliberately staggered, not just hue — in light
mode, relative luminance runs contradicted (0.104, darkest) → refusal (0.137)
→ supported (0.156) → unsupported (0.167, lightest); dark mode has its own
ordering. This is a backup, not the primary mechanism: **components must
still pair every verdict with an icon and a text label, never colour alone**
(§5.3 of `frontend_plan.md`) — `getVerdictMeta()` returns all three
(`icon`, `label`, colour classes) together so no consumer can render colour
in isolation.
