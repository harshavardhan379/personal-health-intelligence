# Architecture Map — Personal Health Intelligence System

**Status:** covers M1–M10 (Engineering Foundation → Retrieval & Evidence). M11 onward (AI
Assistant, Verification, Safety, Evaluation) is not yet built and is called out explicitly
where relevant, not silently omitted.

**Document structure:** this follows the [C4 model](https://c4model.com) (Context → Container
→ Component → Code), the standard way engineering orgs (including Google and Meta design docs)
layer a system description from "what is this, for whom" down to "which function does what" —
each level zooms in on one part of the level above it, so a reader can stop at whatever depth
they need.

---

## 1. System Context (C4 Level 1)

*Who uses this system, and what does it talk to?*

```text
                        ┌─────────────────────────────┐
                        │           PATIENT             │
                        │   (the only user role today)   │
                        └───────────────┬───────────────┘
                                         │ uses, via browser
                                         ▼
                   ┌─────────────────────────────────────────┐
                   │   Personal Health Intelligence System       │
                   │   (this repository)                         │
                   └─────────────────┬───────────────────────┘
                                         │
                     ┌───────────────────┼───────────────────┐
                     ▼                                           ▼
          ┌─────────────────────┐                   ┌─────────────────────┐
          │  No external LLM      │                   │  No external OCR      │
          │  provider wired in     │                   │  service wired in      │
          │  yet (M11)               │                   │  (local pytesseract,   │
          │                            │                   │  optional, degrades     │
          │                            │                   │  gracefully)            │
          └─────────────────────┘                   └─────────────────────┘
```

**In scope today:** one user role (an authenticated patient), interacting entirely through the
web frontend. **Not yet in scope:** clinicians, family sharing, any external AI provider, any
third-party integration — all explicitly out of scope per the product brief, or scheduled for
a later milestone.

---

## 2. Container diagram (C4 Level 2)

*What are the independently-runnable/deployable pieces, and how do they talk to each other?*

```text
┌───────────────────────┐        HTTPS/JSON        ┌──────────────────────────────┐
│       Web Client          │ ────────────────────────▶ │         API Server              │
│  Next.js 16 (TypeScript)    │                             │  FastAPI (Python 3.12 in         │
│  apps/web/                    │ ◀──────────────────────── │  Docker; 3.9 locally)             │
│  Runs on :3000                 │        JSON responses       │  services/api/                     │
└───────────────────────┘                             │  Runs on :8000                      │
                                                            └───────────────┬──────────────┘
                                                                                │ SQL (SQLAlchemy / psycopg)
                                                                                ▼
                                                            ┌──────────────────────────────┐
                                                            │          PostgreSQL 16              │
                                                            │       + pgvector extension           │
                                                            │  Docker container, :5432              │
                                                            │  Persistent volume: pgdata             │
                                                            └──────────────────────────────┘
```

| Container | Technology | Responsibility | Talks to |
|---|---|---|---|
| **Web Client** | Next.js 16, React, TypeScript, Tailwind | Renders UI, holds the JWT client-side, issues `fetch` calls | API Server only |
| **API Server** | FastAPI, Python, SQLAlchemy, Pydantic | Auth, validation, business logic, orchestrates all services below | PostgreSQL only |
| **PostgreSQL** | Postgres 16 + pgvector | Durable storage for all structured data and vector embeddings | Nobody — purely passive |

**Deployment unit boundary:** today this is a **modular monolith** — the "API Server" box above
is *one* running process. Internally it is organized into separate modules (`services/*`) with
narrow interfaces, which is what would make a future split into real microservices tractable —
see `docs/00_MVP_EXECUTION_PLAN.md` and the TEMP_YG tutorial's §12 for the deeper theory on that
transition. No such split exists yet; there is exactly one backend deployable.

**No message queue, no cache layer, no CDN, no load balancer** exist in this system yet — all
explicitly absent, not implied. `BackgroundTasks` (FastAPI's in-process async task runner) is
used for the one asynchronous workflow that exists (document processing), not a real job queue.

---

## 3. Component diagram (C4 Level 3) — inside the API Server

*What are the API Server's internal modules, and what does each own?*

```text
services/api/  (the ONLY container with a network listener on :8000)
│
├── main.py ─────────────────────────────── composition root: wires routers + middleware
│
├── app/core/  ─────────────────────────── cross-cutting concerns, used by every route
│   ├── config.py     Settings (env-driven: DATABASE_URL, JWT_SECRET, CORS_ORIGINS, ...)
│   ├── logging.py     JSON-structured logging setup
│   ├── errors.py       uniform {"error": {code, message}} for every failure mode
│   ├── security.py    password hashing (bcrypt), JWT issue/verify
│   ├── deps.py          get_current_user — THE authorization choke point (see §6)
│   └── storage.py      file validation + path-traversal-safe disk writes
│
├── app/api/v1/  ──────────────────────── HTTP boundary — one router per resource
│   ├── auth.py        /auth/{register,login,logout,me}
│   ├── health.py       /health
│   ├── documents.py   /documents  — ALSO owns the async pipeline orchestration (see §5)
│   ├── review.py        /events/{id}/{confirm,edit,reject}
│   ├── timeline.py      /timeline
│   ├── trends.py         /trends/{test_name}
│   └── search.py         /search
│
└── (imports, does not expose over HTTP, the domain services below)

services/{document_processing, extraction, retrieval, trends}/  ── domain logic, pure Python
packages/schemas/  ──────────────────────────────────────────── shared data contracts
```

**The governing rule of this layout:** `app/api/v1/*.py` is the only layer allowed to know
about HTTP (status codes, request/response shapes). `services/*` modules take and return plain
Python/Pydantic objects and know nothing about FastAPI — this is what makes them independently
unit-testable (see `tests/unit/`) and what would make extracting one into its own microservice
later a contained change rather than a rewrite.

---

## 4. Data architecture

### 4.1 Entity-relationship diagram

```text
┌─────────┐
│  User   │
└────┬────┘
   1 │
     ├──────────────┬──────────────┬──────────────┐
   * ▼            * ▼            * ▼            * ▼
┌──────────┐  ┌───────────┐  ┌───────────┐  ┌──────────┐
│ Document │  │HealthEvent│  │ Embedding │  │ AITrace  │
└────┬─────┘  └─────┬─────┘  └───────────┘  └──────────┘
   1 │             1 │                        (reserved
     ▼ *             ▼ *                       for M11,
┌──────────┐  ┌───────────┐                   unused today)
│DocumentPg│  │Provenance │
└──────────┘  └─────┬─────┘
                   * │
                     ▼ 1
                (→ Document, again — a
                 HealthEvent's provenance
                 points back at the page
                 it was extracted from)
```

### 4.2 Table reference

| Table | Purpose | Key columns | Written by |
|---|---|---|---|
| `users` | Accounts | `email` (unique), `hashed_password` | `auth.py::register` |
| `documents` | Uploaded file metadata | `storage_path`, `processing_status`, `classification` | `documents.py::upload_document`, `_run_pipeline` |
| `document_pages` | Per-page extracted text | `page_number`, `text`, `is_ocr` | `_run_pipeline` |
| `health_events` | Candidate/confirmed structured facts | `event_type`, `event_date`, `data` (JSONB), `review_status` | `_run_pipeline` (insert), `review.py` (status transitions) |
| `provenance` | Fact → source traceability | `document_id`, `page_number`, `quote` | `_run_pipeline` |
| `embeddings` | Vector index for semantic search | `chunk_text`, `vector` (pgvector, 384-dim) | `_run_pipeline` → `retrieval/semantic.py::embed_document_pages` |
| `ai_traces` | AI call observability | *(schema defined, zero writers today — M11)* | — |

### 4.3 Invariants enforced in code (not just documented)

| Invariant | Enforced in |
|---|---|
| Every row with user data carries `user_id`; every query filters on it | `packages/schemas/queries.py` — the one shared helper module every read path is expected to use |
| A `HealthEvent` cannot be confirmed without a `Provenance` row | `review.py::confirm_event` — explicit check before the status transition |
| Only `confirmed`/`edited` events are ever treated as authoritative | `queries.py::CONFIRMED_STATUSES` + `confirmed_events_query()`, used by `timeline.py`, `trends.py`, `retrieval/structured.py` |
| Review status transitions are restricted to `pending → {confirmed, edited, rejected}` | `review.py::_ALLOWED_TRANSITIONS` + `_require_transition()` |

### 4.4 Schema evolution

Managed exclusively through **Alembic** (`database/migrations/`), never hand-edited against a
running database. Current state: one migration, `0001_initial_schema`, hand-authored against
`packages/schemas/models.py` (no live Postgres was available at authoring time to autogenerate
it) and since verified against a real database.

---

## 5. Key flows (sequence-diagram style)

### 5.1 Authentication

```text
Client              API (auth.py)         security.py          users table
  │  POST /auth/register   │                      │                     │
  ├────────────────────▶│                      │                     │
  │                        │  hash_password()     │                     │
  │                        ├─────────────────▶│                     │
  │                        │                      │  INSERT             │
  │                        ├──────────────────────────────────────▶│
  │                        │  create_access_token()                    │
  │                        ├─────────────────▶│                     │
  │  { access_token }       │                      │                     │
  │◀────────────────────┤                      │                     │
```

Every subsequent request carries `Authorization: Bearer <token>`. `deps.py::get_current_user`
decodes it, loads the `User` row, and is the single dependency every protected route declares —
see §6 for why this is the entire authorization model.

### 5.2 Document upload → structured, searchable data (the system's core workflow)

```text
Client        documents.py          storage.py    document_processing/   extraction/      retrieval/
  │ POST /documents  │                   │                  │                   │                │
  ├──────────────▶│                   │                   │                   │                │
  │                  │ validate_upload() │                   │                   │                │
  │                  ├───────────────▶│                   │                   │                │
  │                  │ save_upload()     │                   │                   │                │
  │                  ├───────────────▶│                   │                   │                │
  │                  │  INSERT Document (status=uploaded)      │                   │                │
  │  201 { id, ... }  │                   │                   │                   │                │
  │◀──────────────┤                   │                   │                   │                │
  │                  │  [response already sent — everything below runs in the BACKGROUND]              │
  │                  │ process_document()                   │                   │                │
  │                  ├────────────────────────────────▶│                   │                │
  │                  │                   │          parse → OCR → classify       │                │
  │                  │  embed_document_pages()              │                   │                │
  │                  ├───────────────────────────────────────────────────────▶│                │
  │                  │                   │                   │          embed() per chunk → INSERT Embedding
  │                  │ extract_all()                          │                   │                │
  │                  ├──────────────────────────────────────▶│                │
  │                  │                   │                   │   4 extractors → validate → INSERT HealthEvent + Provenance (status=pending)
  │                  │  UPDATE Document (status=processed)          │                   │                │
```

**Client-observable behavior:** the frontend polls `GET /documents` every 3 seconds
(`apps/web/src/app/documents/page.tsx`) until `processing_status` flips from `processing` to
`processed` or `failed` — there is no push/websocket notification, by design, at this stage.

### 5.3 Human review gate

```text
Client            review.py                  health_events / provenance
  │ GET /events?status_filter=pending   │
  ├────────────────────────────────▶│
  │  [pending HealthEvents]            │
  │◀────────────────────────────────┤
  │ POST /events/{id}/confirm          │
  ├────────────────────────────────▶│
  │                                     │  has_provenance check (422 if none)
  │                                     │  _require_transition (409 if illegal)
  │                                     │  UPDATE review_status = confirmed
  │  200 { ...confirmed event }         │
  │◀────────────────────────────────┤
```

Only after this step does a fact become visible to `timeline.py`, `trends.py`, or
`retrieval/structured.py` — none of them query `health_events` directly; all three go through
`queries.py::confirmed_events_query`.

### 5.4 Hybrid retrieval

```text
Client          search.py         structured.py      semantic.py        ranking.py
  │ GET /search?q=  │                   │                   │                 │
  ├─────────────▶│                   │                   │                 │
  │                 │ hybrid_search()   │                   │                 │
  │                 ├──▶ search_structured()                                 │
  │                 │     (exact keyword match over confirmed HealthEvents)   │
  │                 ├──────────────▶ search_semantic()                        │
  │                 │     (embed(query) → pgvector cosine distance ORDER BY)   │
  │                 ├──────────────────────────────▶ rank_evidence()           │
  │                 │     (structured weighted 1.0, semantic weighted 0.85)     │
  │  [Evidence...]  │                   │                   │                 │
  │◀─────────────┤                   │                   │                 │
```

---

## 6. Security model (as built, not aspirational)

| Concern | Current implementation | Known gap / future milestone |
|---|---|---|
| **Authentication** | JWT (HS256), 24h expiry, `bcrypt` password hashing | No refresh-token flow; no server-side revocation (logout is client-side-only, documented as a deliberate MVP simplification in `auth.py`) |
| **Authorization** | Every protected route depends on `get_current_user`; every query is scoped by `user_id` via `queries.py` helpers | No role-based access (single user role exists) |
| **Transport** | Plain HTTP in local dev; TLS termination is an infrastructure concern not yet configured | M18 (production Docker/deployment) |
| **File upload safety** | MIME + extension + size validated (`storage.py::validate_upload`); stored filename is a random UUID, never the client's filename (path-traversal prevention) | Antivirus/malware scanning: not implemented |
| **Secrets** | Read from environment variables (`JWT_SECRET`, `DATABASE_URL`); `.env` gitignored, `.env.example` committed | No secrets manager / rotation — fine for local dev, not production-ready |
| **CORS** | Explicit allow-list (`http://localhost:3000`), not `*` | Will need a real production origin added at deploy time |
| **Logging hygiene** | Structured JSON logs; code review confirms no request bodies/file contents are logged | No automated sensitive-data audit yet — that's M17 (`Sensitive Log Audit`) |
| **Cross-user isolation** | Verified live: a second test user received empty results and a 404 on direct-ID access to the first user's data | No automated regression test suite for this yet — M18 (`Add authorization isolation tests`) |

---

## 7. Observability — what exists, what doesn't

**Exists today:**
- JSON-structured logging (`app/core/logging.py`), applied process-wide via `configure_logging()`.
- Every unhandled exception is caught and logged with a traceback (`errors.py::unhandled_error_handler`), never silently swallowed.
- Pipeline failures are logged per-document (`documents.py::_run_pipeline`, `logger.warning` / `logger.exception`).

**Explicitly does not exist yet** (M16, not started):
- Per-request correlation/request IDs threaded through logs.
- Any latency, token-usage, or AI-call tracing (`ai_traces` table exists in the schema, has zero writers).
- Centralized log aggregation, dashboards, or alerting.

---

## 8. Testing strategy (as built)

```text
tests/unit/
├── test_trends.py       9 tests — trend_engine.py, pure functions, no DB required
├── test_extraction.py   5 tests — the 4 extractors + validate.py, no DB required
└── test_retrieval.py    6 tests — ranking.py + embeddings.py, no DB required
```

**Deliberate scope boundary:** every existing automated test exercises *pure functions* — no
database, no network, no fixtures to manage, which is why they run in ~0.2s and were verifiable
in this development environment without Docker. **What is not yet covered by automated tests**,
and was instead verified manually against a live Postgres in this session: the full HTTP
surface (`app/api/v1/*`), the upload→extract→review→timeline pipeline end to end, and
cross-user isolation. Formalizing those into `tests/integration/` is explicitly scheduled —
M18 (`YGH-97` upload pipeline, `YGH-98` AI pipeline, `YGH-99` authorization isolation).

---

## 9. Full component → function reference

### 9.1 Frontend (`apps/web/src/`)

| File | Exports | Role |
|---|---|---|
| `lib/api.ts` | `apiFetch()`, `getToken()`, `setToken()`, `clearToken()`, `ApiError` | Single chokepoint for every HTTP call; attaches the JWT, normalizes errors |
| `lib/auth.tsx` | `AuthProvider()`, `useAuth()` | Client-side session state (React Context), backed by `localStorage` |
| `components/RequireAuth.tsx` | `RequireAuth()` | Route guard — redirects to `/login` if unauthenticated |
| `components/HeaderNav.tsx` | `HeaderNav()` | Nav bar, shows login/logout state |
| `components/PagePlaceholder.tsx` | `PagePlaceholder()` | Shell for not-yet-built pages (`assistant`, `settings`) |
| `app/layout.tsx` | `RootLayout()` | App shell, wraps every page in `AuthProvider` |
| `app/login/page.tsx` | `LoginPage()` | Register/login form |
| `app/documents/page.tsx` | `DocumentsPageInner()`, `DocumentsPage()` | Upload form + polling document list |
| `app/review/page.tsx` | `ReviewPageInner()`, `ReviewPage()` | Pending-event confirm/reject UI |
| `app/timeline/page.tsx` | `TimelinePageInner()`, `describeEvent()`, `TimelinePage()` | Confirmed-event timeline |
| `app/assistant/page.tsx`, `app/settings/page.tsx` | placeholder only | **Not built — M11/M15** |

### 9.2 Backend — HTTP layer (`services/api/app/api/v1/`)

| Router | Endpoints | Private helpers |
|---|---|---|
| `auth.py` | `register`, `login`, `logout`, `me` | — |
| `health.py` | `health` | — |
| `documents.py` | `upload_document`, `list_documents`, `get_document_status` | `_run_pipeline` (background), `_to_out` |
| `review.py` | `list_pending_events`, `confirm_event`, `edit_event`, `reject_event` | `_get_owned_event`, `_require_transition`, `_to_out` |
| `timeline.py` | `get_timeline`, `get_event_detail` | `_dedup_key`, `_to_out` |
| `trends.py` | `get_trend` | — |
| `search.py` | `search` | — |

### 9.3 Backend — cross-cutting core (`services/api/app/core/`)

| File | Functions |
|---|---|
| `security.py` | `hash_password`, `verify_password`, `create_access_token`, `decode_access_token` |
| `deps.py` | `get_current_user` |
| `storage.py` | `validate_upload`, `save_upload`, `read_upload`, `delete_user_storage` |
| `logging.py` | `configure_logging` |
| `errors.py` | `http_error_handler`, `validation_error_handler`, `unhandled_error_handler` |
| `config.py` | `Settings` (class), `settings` (instance) |

### 9.4 Backend — domain services (`services/*`, no HTTP awareness)

| Module | Functions |
|---|---|
| `document_processing/parse.py` | `extract_pdf_pages` |
| `document_processing/ocr.py` | `ocr_available`, `ocr_pdf_page`, `ocr_image` |
| `document_processing/classify.py` | `classify` |
| `document_processing/normalize.py` | `process_document` (orchestrator) |
| `extraction/{lab,medication,encounter,symptom}_extractor.py` | `extract` (×4, one per fact type) |
| `extraction/validate.py` | `validate_fact`, `validate_facts` |
| `extraction/pipeline.py` | `extract_all` (orchestrator) |
| `retrieval/embeddings.py` | `embed`, `chunk_text` |
| `retrieval/structured.py` | `search_structured` |
| `retrieval/semantic.py` | `search_semantic`, `embed_document_pages` |
| `retrieval/ranking.py` | `rank_evidence` |
| `retrieval/hybrid.py` | `hybrid_search` (orchestrator) |
| `trends/trend_engine.py` | `detect_trend`, `measurement_history` |

### 9.5 Shared contracts (`packages/schemas/`)

| File | Contents |
|---|---|
| `db.py` | `Base`, `get_db`, `engine`, `SessionLocal` |
| `models.py` | SQLAlchemy ORM: `User`, `Document`, `DocumentPage`, `HealthEvent`, `Provenance`, `Embedding`, `AITrace` |
| `queries.py` | `confirmed_events_query`, `user_documents_query`, `user_owns_document` |
| `extraction.py` | Pydantic: `ExtractedFact`, `LabResult`, `MedicationEvent`, `Encounter`, `SymptomEvent` |
| `evidence.py` | Pydantic: `Evidence` |

---

## 10. What is deliberately *not* real yet

Two components are intentionally offline/deterministic placeholders, documented inline in their
own source files, each swappable behind its existing function signature without touching any
caller:

| Placeholder | Real file | Why | Swap point |
|---|---|---|---|
| Regex-based fact extraction, not an LLM | `services/extraction/*_extractor.py` | Works offline, free, deterministic, enough to exercise the full pipeline honestly | `extract(pages) -> list[ExtractedFact]` |
| Deterministic hashed embeddings, not a real model | `services/retrieval/embeddings.py::embed` | No API key / GPU required to prove storage + pgvector search work end to end | `embed(text) -> list[float]`, keep `EMBEDDING_DIM` in sync with `models.py` |

Both are scheduled to be replaced by real providers in M11 and the M11b fine-tuning track —
see `docs/00_MVP_EXECUTION_PLAN.md`.

---

## 11. Repository map (annotated)

```text
personal-health-intelligence/
├── apps/web/                    Next.js frontend — §9.1
├── services/
│   ├── api/                       The ONLY container with an HTTP listener — §3, §9.2, §9.3
│   ├── document_processing/   Pure domain logic — §9.4
│   ├── extraction/                 Pure domain logic — §9.4
│   ├── retrieval/                   Pure domain logic — §9.4
│   ├── trends/                      Pure domain logic — §9.4
│   ├── reasoning/                  empty — M11
│   ├── verification/               empty — M12
│   └── safety/                       empty — M13
├── packages/schemas/          Shared contracts, imported by everything — §9.5
├── database/
│   ├── migrations/                Alembic — §4.4
│   └── seeds/                        database/seeds/seed.py — one demo user + 2 HbA1c events
├── tests/unit/                     §8
├── docs/                              this file, plus 00_MVP_EXECUTION_PLAN.md, 06_DATA_MODEL.md
└── docker-compose.yml           §2 — postgres + backend + frontend container defs
```

---

## 12. Revision note

This document reflects the codebase as of the completion of M10 (commit range `fc7dca1` →
`77850e6`). It should be re-read and updated at the end of each subsequent milestone — an
architecture map that drifts from the code it describes is worse than no map at all.
