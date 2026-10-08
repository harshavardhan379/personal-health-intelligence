# Handover — Personal Health Intelligence System

**Read this first, in a fresh Claude Code session, before touching anything.** It tells you
exactly where this project stands, how to run it, and how to keep working on it the same way
it's been built so far.

---

## 1. What this project is

A longitudinal AI platform that turns fragmented personal health records (PDFs/images) into
structured health events, a timeline, trend detection, and an evidence-grounded, citation-backed
AI assistant — built as a learning/portfolio project, with a product brief that explicitly
forbids diagnosis, prescribing, or treatment direction.

Full product brief: [`docs/00_MVP_EXECUTION_PLAN.md`](00_MVP_EXECUTION_PLAN.md)
Full architecture (C4-style, code-accurate): [`docs/ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) — **re-read this**, it has the real diagrams, schema, and function inventory. This handover doesn't repeat it.

## 2. Where things actually stand

- **GitHub:** `harshavardhan379/personal-health-intelligence`, branch `main`.
- **Linear:** team `YGH`, project "Personal Health Intelligence App" — https://linear.app/harshavardhan-p/team/YGH
- **Milestones M1–M11 are Done** (foundation, DB, auth, upload, document processing, extraction,
  review, timeline, trends, retrieval, and the full AI assistant with per-claim citations and
  intent-specific retrieval).
- **M12 (Evidence Verification) is next, not started.** That's the natural pickup point.
- Latest commit as of this handover: `89b4b95` ("Complete M11...").

**Don't trust this list to stay accurate — check Linear and `git log` for the real current
state**, this file will drift the moment more work lands.

## 3. How to run everything

```bash
cd personal-health-intelligence

# 1. Database
docker compose up -d postgres
docker compose ps   # wait for "healthy"

# 2. Ollama (local LLM, used by the AI assistant)
ollama serve                      # if not already running
ollama pull llama3.2:3b           # if not already pulled

# 3. Backend
python3 -m venv .venv && source .venv/bin/activate   # first time only
pip install -r requirements.txt                        # first time only
export DATABASE_URL="postgresql+psycopg://phi:phi_dev_password@localhost:5432/phi"
alembic upgrade head              # first time / fresh DB only
uvicorn services.api.main:app --reload

# 4. Frontend (separate terminal)
cd apps/web
npm install        # first time only
npm run dev

# 5. Open
# http://localhost:3000/login
```

**Test account:** `realtest@example.com` / `testpass123` — has real data (2 uploaded synthetic
reports, confirmed lab/medication/encounter/symptom events across two dates). Good for exercising
Timeline, Trends and the Assistant immediately.

**LLM provider:** defaults to `ollama` (no key needed). To use OpenAI or Grok instead, set in
`services/api/.env` (gitignored, copy from `.env.example`): `LLM_PROVIDER=openai|grok|ollama` plus
the matching API key. **Never paste a real API key into a Claude conversation** — set it directly
in the `.env` file.

## 4. What's real vs. deliberately faked (read this before "fixing" something)

Two pieces are intentional offline placeholders, documented inline in their own files, each
swappable without touching callers:

- **Extraction is regex-based**, not an LLM (`services/extraction/*_extractor.py`). Works on a
  specific synthetic format (see comments in each file, e.g. `HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10`).
- **Embeddings are a deterministic offline hash**, not a real model (`services/retrieval/embeddings.py`).

Everything else (auth, upload, pipeline, review, timeline, trends, retrieval, the AI assistant
with real LLM calls) is real and has been verified live against a running Postgres + Ollama, not
just unit-tested.

## 5. Things not yet built, so you don't "discover" them as bugs

- `apps/web/src/app/assistant/page.tsx` and `settings/page.tsx` are empty placeholders. The
  assistant works fully via `POST /api/v1/assistant/ask` — just not wired to a frontend page yet
  (that's M15).
- No automated integration tests yet (M18) — only pure-function unit tests (31, in `tests/unit/`).
  Everything else was verified manually, live, and documented in Linear comments on each issue.
- No request IDs / tracing / log aggregation (M16). Structured JSON logging exists; correlation
  across requests doesn't yet.
- `ai_traces` table exists in the schema with zero writers — reserved for M16.

## 6. How this project has been worked on — keep doing it this way

- **One Linear issue at a time.** Move it to `In Progress`, build/verify it, commit referencing
  its ID (`YGH-NN: ...`), comment on the Linear issue with what was done and how it was verified,
  then move it to `Done`.
- **Never mark something Done on unit tests alone if it touches the live stack.** This project's
  standard is: run it against the real Postgres/Ollama, show the actual output, *then* call it
  done. Several real bugs (structured search's substring-match bug, two separate JSON-formatting
  failures from the local model, a Swagger auth-scheme mismatch, a `passlib`/`bcrypt`
  incompatibility) were only caught this way — unit tests alone would have missed every one of
  them.
- **Fix real bugs found while verifying a task, in the same sitting**, even if they're not their
  own ticket — document them clearly in the commit message and the Linear comment as "found while
  testing X, not Y's original scope" rather than silently bundling or silently ignoring them.
- **Placeholders are fine, hidden placeholders are not.** Anything faked for offline/no-cost
  development (extraction, embeddings) is loudly commented in the code, in `ARCHITECTURE_MAP.md`
  §10, and swappable behind the same function signature.
- **Commit and push after every task**, don't batch multiple issues into one commit unless they're
  genuinely one unit of work.
- The user prefers being asked before destructive/ambiguous actions, and prefers real verification
  over claimed-but-untested completion. When in doubt, run it and show the output rather than
  asserting it works.

## 7. Suggested next steps (M12 — Evidence Verification)

Per `docs/00_MVP_EXECUTION_PLAN.md`, M12 takes M11's `Answer.claims` (each already carrying
`citation_indices` from YGH-84) and actually checks them:

- **YGH-52** Claim extraction (largely already done by YGH-84's `claims` structure — may just need
  confirming it satisfies this ticket's acceptance criteria)
- **YGH-53** Claim-to-evidence matching
- **YGH-54** Unsupported claim detection
- **YGH-55** Contradiction detection (e.g. two documents disagreeing on a dose)
- **YGH-56** Answer verification gate (block unverified high-risk claims)
- **YGH-57** Verification test suite

The TREND hallucination we found and fixed by hand in M11 (model misreading "7.2% within
4.0-5.6% range") is a concrete, already-observed example of exactly what M12 should catch
automatically going forward.

---

*This file should be re-read at the start of a new session and updated (or regenerated) at the
end of each milestone — an out-of-date handover is worse than none.*
