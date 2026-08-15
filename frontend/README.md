# KnowRAG Frontend

Next.js UI for KnowRAG, a fact-checking RAG system that answers strictly from retrieved evidence and marks each claim with what the evidence supports. See [monorepo README](../README.md) and [`frontend_plan.md`](../frontend_plan.md) for full design.

**Status:** scaffold, design system, app shell (WS-A) only. Query interface, auth, documents/history/status pages, and the API client are later workstreams — routes currently show placeholders.

## Prerequisites

- Node v22, npm 9+ (confirmed: v22.22.1 / 9.2.0)
- Backend running (see below)

## Install

```bash
npm install
```

## Environment

```bash
cp .env.example .env.local
```

Gitignored. One variable:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Validated by `src/config/env.ts` (Zod) at load — fails loudly on a missing/invalid URL instead of silent fetch failures. This is the **only** file that reads `process.env`.

- `8000` — backend's `app` container (`docker compose up -d`)
- `8001` — host-side `uvicorn --reload` dev server (`../backend/`, hot-reload workflow; the container doesn't hot-reload)

## Run the backend

```bash
cd ../backend
docker compose up -d
```

- Backend must allow this app's origin in `CORS_ORIGINS` (backend `.env`) — e.g. `http://localhost:3000` for `next dev`. Otherwise every request dies at CORS preflight.
- **Auth:** first-user-registers-then-closed signup. Once a user exists, `/auth/register` returns `403` for everyone else. Register the first account at `/register` before anyone else does, or you'll need direct DB access to reset it.

## Develop

```bash
npm run dev        # http://localhost:3000
```

## Build / typecheck / lint / test

```bash
npm run build
npx tsc --noEmit    # or: npm run typecheck
npm run lint
npm run test:run    # vitest, single run
npm run test        # vitest, watch mode
```

Tests mock the network with MSW — no live backend or LLM spend needed.

## Tech stack

- **Next.js 15** (App Router), **TypeScript** strict + `noUncheckedIndexedAccess` (API returns parallel `citations`/`chunk_ids` arrays — off-by-one is a real risk)
- **Tailwind v4** (CSS-first config, no `tailwind.config.ts`) + **shadcn/ui** (source in `src/components/ui/`, built on Base UI for keyboard nav/ARIA)
- **TanStack Query v5** for server state, **Zod v4** at the API boundary
- **Vitest + Testing Library + MSW**

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
│   ├── utils/verdict.ts  verdict -> colour/label/icon mapping (single source of truth)
│   ├── api/ auth/ schemas/ hooks/ storage/   (later workstreams)
├── types/api.ts          API contract types (later workstream)
└── config/env.ts         only process.env read in the codebase
```

## Design tokens — verdict colours

Four CSS variables in `src/app/globals.css` (light + dark), one per NLI verdict:

| Token | Meaning | Light | Dark |
|---|---|---|---|
| `--verdict-supported` | evidence confirms the claim | `oklch(0.517 0.13 165)` teal-green | `oklch(0.652 0.12 165)` |
| `--verdict-unsupported` | evidence couldn't confirm — neutral/caution, not failure | `oklch(0.557 0.15 75)` amber | `oklch(0.709 0.14 75)` |
| `--verdict-contradicted` | evidence disagrees — the alarming one | `oklch(0.495 0.20 25)` red | `oklch(0.705 0.17 25)` |
| `--verdict-refusal` | system declined to answer — informational | `oklch(0.523 0.15 265)` blue-violet | `oklch(0.648 0.12 265)` |

Each has a `-bg` wash variant for badges/alerts; all eight exposed as Tailwind utilities (`text-verdict-supported`, `bg-verdict-contradicted-bg`, etc.) via `@theme inline`. Never hardcode a verdict colour — use `getVerdictMeta()` from `src/lib/utils/verdict.ts`.

**Why not red/green:** ~8% of men have red-green colour blindness. Hues are spread around the wheel (red, amber, teal, blue-violet) rather than opposed; teal and blue-violet stay distinguishable from red for protanopes/deuteranopes since neither sits on the red-green axis.

**Contrast:** all four clear WCAG AA (≥4.5:1) against `--background` — light 4.83–6.81:1, dark 6.00–7.51:1 (OKLCH→sRGB, standard WCAG luminance formula).

**Greyscale backup:** lightness is staggered by design, not just hue (light-mode luminance: contradicted 0.104 → refusal 0.137 → supported 0.156 → unsupported 0.167). Still a backup only — every verdict must pair colour with an icon and text label (`frontend_plan.md` §5.3); `getVerdictMeta()` returns all three together so colour can't be rendered alone.