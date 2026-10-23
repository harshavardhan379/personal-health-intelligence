# Handover — Personal Health Intelligence System

**Read this entire file before touching the code, in any new session on any machine.** It is the
complete record of what exists, why it exists that way, what's left, and the exact method to use
building the rest. It is written to make a brand-new Claude Code session (with zero memory of
this project's history) productive immediately, without re-deriving decisions already made.

---

## 1. What this project is, in one paragraph

A longitudinal AI platform that turns fragmented personal health records (PDFs/images) into
structured health events, a browsable timeline, deterministic trend detection, and an
evidence-grounded, citation-backed AI assistant. It is explicitly **not** a diagnostic,
prescribing, or treatment system — that boundary is enforced in the system prompt today and will
be enforced structurally by M13 (Safety). Built as a from-scratch learning/portfolio project,
engineered to the same standard a real product team would use: real database, real auth, real
LLM calls, real bugs found and fixed through live testing, not just "it imports."

**Two other documents exist — read them too, this file doesn't repeat them:**
- [`docs/00_MVP_EXECUTION_PLAN.md`](00_MVP_EXECUTION_PLAN.md) — the original full product brief, all 19 milestones, all acceptance criteria, in the author's own words.
- [`docs/ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) — C4-style architecture: system context, container/component diagrams, full ER diagram, sequence diagrams for every real flow, the complete function inventory by file, and the security/observability/testing posture as actually built (not aspirational).

---

## 2. Where this stands right now — exact, verifiable facts

- **Repo:** `harshavardhan379/personal-health-intelligence` on GitHub, branch `main`.
- **Linear:** team `YGH`, project "Personal Health Intelligence App" — https://linear.app/harshavardhan-p/team/YGH
- **Milestones M0 (partially), M1 through M13: Done.** M14 (Evaluation & PersonalHealthBench) is
  IN PROGRESS: YGH-63 to YGH-69 (framework, generator, truth, questions, runner, direct-LLM and naive-RAG baselines) are done; YGH-70 to YGH-72 remain.
- **Latest commit:** see `git log` (M12 landed as `2c5447f`, followed by a live-testing fix commit). Run `git log --oneline | cat` to get the real current state — this
  number is stale the moment new work lands.
- **126 original Linear issues + 11 M11b (fine-tuning) issues added mid-project = 137 total.**
  Roughly 50 are Done as of this writing.

**Do not trust narrative claims over the actual repo.** If this file and `git log`/Linear disagree,
the repo and Linear are correct.

---

## 3. Complete build history — what was actually done, in order

This is the real sequence, not a summary. Each entry: what shipped, the real commit, and anything
notable (a bug found, a decision made) that a future session needs context on.

### Phase 0 — Tutorial groundwork (plain HTML, before the real stack)
Before building the actual Next.js/FastAPI stack, a minimal plain HTML/CSS/JS prototype was built
by hand (`d677680`, `d68953a`, `3358a43`) purely as a teaching exercise to understand the
request/response cycle, CORS, and async/await before any framework abstracted it away. This
prototype now lives at `experiments/plain-html-prototype/` for reference only — not part of the
running app.

A **separate, isolated teaching sandbox** also exists at `TEMP_YG/` (sibling to this repo, same
tech stack in miniature — one table, one page, one API) with an extensive tutorial document,
`TEMP_YG/TUTORIAL.md`, covering Docker/containers, SQL, SQLAlchemy ORM internals, Pydantic,
FastAPI internals, React/Next.js internals, monolith-vs-microservices with a full migration
walkthrough, and an interview-question bank. That sandbox is unrelated to this app's runtime and
exists purely for learning — mention it only if asked about learning resources.

### M1 — Engineering Foundation
- **YGH-1** Repo structure (`d77adf7`): `apps/`, `services/`, `packages/`, `database/`, `tests/`,
  `benchmarks/`, `experiments/`, `docs/`, README, `.gitignore`, MIT LICENSE, `docs/BRANCHING.md`.
- **YGH-2** Next.js frontend shell (`4a64181`): Next.js 16, TypeScript, Tailwind 4, App Router,
  `src/` layout. Shell with header/nav/footer and placeholder pages for every planned route.
- **YGH-4** FastAPI restructure (`7ec766e`): split a single `main.py` into
  `app/core/{config,logging,errors,security,deps,storage}.py` and
  `app/api/v1/{router files}.py`. Config via `pydantic-settings` reading `.env`. Every error
  response normalized to `{"error": {"code", "message"}}`.
- **YGH-6, YGH-8** (bundled into the M1–M10 push, `fc7dca1`): `docker-compose.yml`
  (`pgvector/pgvector:pg16` + backend + frontend, with a named volume so Postgres data survives
  container recreation), `pyproject.toml` (ruff config, pinned to `target-version = "py39"`
  deliberately — see §6), `CONTRIBUTING.md`, `.pre-commit-config.yaml`.

### M2 — Data Model & Database (bundled into `fc7dca1`)
Full SQLAlchemy schema in `packages/schemas/models.py`: `User`, `Document`, `DocumentPage`,
`HealthEvent`, `Provenance`, `Embedding`, `AITrace`. One hand-written Alembic migration
(`database/migrations/versions/0001_initial_schema.py`) — hand-written, not autogenerated,
because no live Postgres was available at authoring time; **this was later verified against a
real database and confirmed correct** once Docker was actually running (see §5, "the first real
Docker verification"). Seed script at `database/seeds/seed.py`.

### M3 — Authentication & User Isolation (bundled into `fc7dca1`)
JWT via `PyJWT`, password hashing originally via `passlib` — **this was buggy and got replaced**,
see the bug list in §5. `get_current_user` (`app/core/deps.py`) is the single authorization choke
point every protected route depends on; every query is scoped by `user_id` through shared helpers
in `packages/schemas/queries.py`. Cross-user isolation was verified live with two real accounts
(empty results + 404 on direct-ID access for a second user).

### M4 — Document Upload & Storage (bundled into `fc7dca1`)
Upload API with MIME/extension/size validation, path-traversal-safe storage (random UUID
filenames, never the client's), background-task-driven processing pipeline. Frontend upload form
with live status polling (`apps/web/src/app/documents/page.tsx`).

### M5 — Document Intelligence (bundled into `fc7dca1`)
`services/document_processing/`: `parse.py` (pypdf text extraction), `ocr.py` (pytesseract,
gracefully degrades if the binary isn't installed rather than crashing), `classify.py`
(keyword-based document type classifier — lab report / prescription / discharge summary / doctor
note / other), `normalize.py` (orchestrates all three into one `NormalizedDocument`).

### M6 — Structured Extraction & Provenance (bundled into `fc7dca1`)
`services/extraction/`: four regex-based extractors (lab, medication, encounter, symptom) —
**deliberately not LLM-based**, see §7 for why and the exact swap point. `validate.py` rejects
malformed facts (bad dates, missing units) rather than guessing. Every confirmed fact traces back
to an exact document page and quote via `Provenance` rows.

### M7 — Human Review (bundled into `fc7dca1`)
Review API/UI: confirm/edit/reject, with a strict state machine
(`pending -> {confirmed, edited, rejected}` only, enforced in `review.py::_require_transition`).
**The single most important invariant in the whole codebase**: confirming an event with no
`Provenance` row is rejected (422) — enforced in code, not just documented.

### M8, M9, M10 — Timeline, Trends, Retrieval (bundled into `fc7dca1`)
- Timeline: date-ordered, deduplicated (re-uploading the same report doesn't double events),
  partial-date-aware.
- Trends: `services/trends/trend_engine.py::detect_trend()` — pure function, deterministically
  computes increasing/decreasing/stable/insufficient_data from a measurement series. Fully
  unit-tested (9 cases including unit-mismatch and noisy-data).
- Retrieval: `services/retrieval/{structured,semantic,hybrid,ranking}.py` + pgvector-backed
  `Embedding` table. **Semantic embeddings are a deterministic offline hash, not a real model**
  — see §7.

### Code quality + Linear tracking for all of the above
All of M1–M10 above landed in one large verified commit, `fc7dca1` ("M1-M10: database, auth,
upload, document processing, extraction, review, timeline, trends, retrieval"), after which **50
Linear issues were moved to Done in one session**, each with its own Linear comment documenting
what was built and how it was verified. Two more fixes followed immediately after, found by
actually running the stack for the first time against real Docker/Postgres:
- `190daaf` — `database/seeds/seed.py` had a `ModuleNotFoundError` (ran as a script, not a module,
  so the repo root wasn't on `sys.path`). Fixed the same way `database/migrations/env.py` already
  handled it.
- `190daaf` — **`passlib` 1.7.4 (its last release, unmaintained since 2020) is incompatible with
  `bcrypt` 5.x** and fails its own internal backend self-test. Replaced with direct
  `bcrypt.hashpw`/`bcrypt.checkpw` calls — not a version pin, a real dependency removal.

### Frontend bug: silent upload failure
`77850e6` — clicking "Upload" with no file selected did nothing at all: no error, no status
change. Reported by the user as "the button is not responding." Root cause: a silent early
`return` with no user feedback. Fixed to show "Please choose a file first."

### Architecture documentation
`6513613` — `docs/ARCHITECTURE_MAP.md` written after M1–M10, C4-style (Context → Container →
Component → Code), covering everything built so far at interview/industry-reference depth.

### M11 — AI Health History Assistant (the most recently completed milestone)
Built and verified **entirely against a real running stack**: real Postgres, real Ollama
(`llama3.2:3b`, local, free), real uploaded synthetic PDFs, real confirmed data. Every claim below
was watched working, not assumed.

- **YGH-80, 81, 82** (`c21c30e`): `packages/schemas/intent.py` (the 7-value `QueryIntent`
  taxonomy: `RETRIEVAL, TEMPORAL, COMPARISON, TREND, SUMMARY, DOCUMENT_EXPLANATION, UNKNOWN`),
  `services/reasoning/intent_classifier.py` (keyword-based, same "deliberate offline placeholder"
  philosophy as extraction), `services/reasoning/context_builder.py::build_context()` (combines
  intent + retrieved evidence into one `AssistantContext`).
  - **Bug found and fixed in the same commit batch** (`6a7f656`): `search_structured` (M10) was
    checking `if whole_question in event_data` — this only ever matched a bare single keyword,
    never a real natural-language question. Rewritten to token-overlap matching with stopword
    filtering.
- **YGH-83** (`7183b02`): `services/reasoning/providers.py` — a `Protocol`-based `LLMProvider`
  interface with three real implementations (`OpenAIProvider`, `GrokProvider` — xAI, OpenAI-SDK-
  compatible, `OllamaProvider` — local, no key), selected at runtime by one env var,
  `LLM_PROVIDER`. `services/reasoning/generate.py::generate_answer()` is the single entry point:
  context → role-structured chat messages → provider → defensively-parsed `Answer`
  (`packages/schemas/answer.py`). Tested live: correct retrieval answers, correct citations, and
  a genuine small-model hallucination was observed and documented (not hidden) — see §5.
  - **Bug found**: Swagger's `/docs` "Authorize" button errored, because `deps.py` used
    `OAuth2PasswordBearer` (which implies the OAuth2 password-grant form flow) for what is
    actually a custom JSON-body JWT login. Fixed (`057ab6e`) to `HTTPBearer`, which shows a
    single "paste your token" field matching what was actually built.
- **YGH-84** (`53693ff`): per-claim citations. `Answer.claims` became `list[Claim]`
  (`{text, citation_indices}`) instead of plain strings; citation indices are validated against
  the real evidence list (out-of-range ones dropped, not trusted); `evidence_ids` is now derived
  from what was *actually cited*, not a blind dump of everything retrieved.
  - **Two more bugs found live**: (1) the model produced inconsistent JSON for the stricter nested
    schema — fixed with `temperature=0` on all three providers; (2) even at temperature 0, the
    local model deterministically appended one stray trailing `}` on some responses, which
    `json.loads` rejected outright — fixed with `json.JSONDecoder().raw_decode()`, which parses
    the first valid JSON value and ignores trailing garbage.
- **YGH-85** (`38e78f9`): `services/retrieval/temporal.py::search_temporal()` — date-ordered
  (not relevance-ranked) evidence with explicit dates in the text, for "what changed" questions.
  Verified live by uploading a **second, later-dated synthetic report** (HbA1c 6.5% vs. the
  original 7.2%) and asking "What changed in my recent reports?" → correctly answered "decreased
  from 7.2% to 6.5%", citing both.
- **Final M11 push** (`89b4b95`), closing YGH-86, YGH-87, YGH-88, plus one unticketed but
  necessary fix found live:
  - **TREND fix (no ticket number — found via testing, fixed in the same sitting, same
    discipline as every other bug above):** the `TREND` intent was still falling through to the
    general relevance search, leaving the small LLM to eyeball trend direction from raw numbers
    itself. This is exactly what produced an earlier observed hallucination: the model said
    "HbA1c is within target range (4.0-5.6%)" while citing its own evidence showing **7.2%**,
    which is *above* that range — a real, observed arithmetic failure in a 3B local model.
    `services/retrieval/trend_evidence.py::search_trend()` now hands the model the
    **already-computed** direction from `trend_engine.py` (M9) instead of asking it to infer one.
    Re-verified live: the exact same question now correctly answers "decreasing."
  - **YGH-86** `services/retrieval/comparison.py::search_comparison()` — groups confirmed events
    by their 2 most recent source documents, labeled "Report 1 (date): ..." / "Report 2 (date):
    ...". Verified live comparing both real uploaded reports (HbA1c and Metformin dose both
    changed correctly identified).
    - **A third, more severe JSON bug found here**: a genuinely broken array structure (not just
      trailing garbage) that even `raw_decode` couldn't recover. Fixed properly at the source —
      `response_format={"type":"json_object"}` (OpenAI/Grok) and `format="json"` (Ollama),
      **constrained decoding that makes malformed JSON syntactically impossible**, rather than
      parsing around it.
  - **YGH-87**: no new "historical" intent was added — the brief's taxonomy (YGH-80) has none.
    Instead, `TEMPORAL`'s keyword list was extended ("first documented", "when did i start", "history
    of", etc.) and `temporal.py` switched to ascending chronological order with a wider window
    (20, was 10) so early occurrences aren't cut off by a recency bias. Verified live: "When was
    Metformin first documented?" correctly answered with the earliest date across both
    occurrences.
  - **YGH-88**: the zero-evidence early-return path in `generate_answer` was already built (M11
    T09 behavior) and verified live repeatedly throughout M11 (e.g. asking about cholesterol with
    no matching data correctly admitted insufficient evidence instead of guessing). This closing
    commit formalized it as an actual regression test
    (`tests/unit/test_generate_answer.py`, 2 tests) rather than leaving it only manually verified.
  - `services/retrieval/describe.py` extracted as a shared helper (per-event-type text rendering)
    so temporal, comparison, and trend evidence all describe the same fact identically — was
    duplicated inline before, now written once.

### M12 — Evidence Verification (`2c5447f`, then a live-testing fix commit)
`services/verification/{match,verify}.py`: every claim in an `Answer` is checked against the evidence
it cited (SUPPORTED / UNSUPPORTED / CONTRADICTED / INSUFFICIENT), deliberately without an LLM call.
`generate_verified_answer()` is what `POST /assistant/ask` now calls; non-SUPPORTED claims are
surfaced in `uncertainty` and `claim_verifications`. YGH-52 to YGH-57 are Done.

**Found by running it live afterwards (unit tests had passed), fixed in the follow-up commit:**
- The number regex `\d+\.?\d*` captured a sentence-ending period (`2026-04-10.` became `10.`), so a
  TRUE claim was labeled CONTRADICTED. Now `\d+(?:\.\d+)?`.
- A correct "X is not reported in the evidence" claim was labeled UNSUPPORTED and made
  `fully_verified` false. Absence claims are now verified against the whole retrieved evidence set:
  SUPPORTED if the topic is absent, CONTRADICTED if the evidence does mention it.
- Small models returned `"null"`/`"None"` as strings for `uncertainty`; now normalized to null.
- `llm_timeout_seconds` raised 30 to 90, because the first call after a cold start timed out.

**Known limitation (not fixed):** a correct claim that states values from two records but cites only
one of them (e.g. "dose changed from 500mg to 1000mg" citing only the 1000mg record) can still be
labeled CONTRADICTED. Checking numbers against all evidence would weaken the check, so it was left.

### Review-gate leak fix (found by live testing after M12)
Embeddings used to be built from raw page text at upload, and `search_semantic` filtered only by
user. An UNREVIEWED report's text reached the assistant, which stated a cholesterol of 240 mg/dL as
fact for an account with zero confirmed events, and the verifier called it SUPPORTED (it checks
against the same raw evidence). Fixed: embeddings now belong to a confirmed event
(`embeddings.health_event_id`, migration `0002`), are created/removed by
`sync_event_embedding()` on confirm/edit/reject, and search re-checks the review status. After
pulling this, run `alembic upgrade head` then `python database/seeds/backfill_event_embeddings.py`.

### M13 — Safety Layer (YGH-5, 58, 59, 60, 61, 62)
`packages/schemas/safety.py` (taxonomy + actions), `services/safety/{classifier,policy,output_guard,gate}.py`,
`docs/02_SAFETY_SPEC.md`, and `tests/safety/` (346 cases). Two checkpoints around the model in
`generate_verified_answer`: dangerous questions are refused BEFORE retrieval/the model (fixed text,
the model is never called), and the model's output is scanned AFTER for diagnosis / treatment
directives / dose instructions / false reassurance. `VerifiedAnswer` gained `safety_category` and
`safety_action`.
- **Key finding, read before changing the classifier:** keyword rules did not generalise. Tuned on
  one batch they missed 13-17 of every 20-24 unsafe questions in the next batch. What fixed it was
  flipping the default to FAIL-CLOSED (a question about the user is answered only if it positively
  looks like a records lookup; otherwise CLARIFY), not adding keywords. Full numbers and caveats
  (small samples, same author, no adversarial testing) are in `docs/02_SAFETY_SPEC.md` section 8.
- The classifier is a rule-based placeholder; the swap point is `classify_safety()`. Keep
  `tests/safety` as the acceptance suite.
- A live check of the five brief queries ("Do I have cancer?" etc.) confirmed all are refused with
  no model call, while ordinary records questions are still answered.

### M14 — Evaluation & PersonalHealthBench (in progress)
- **YGH-63** `docs/06_EVALUATION_FRAMEWORK.md` v0.2: what is measured, against what, and how
  each number is scored. Key rules: 30 dev / 20 locked-test patients; no system grades itself
  (the benchmark never calls `services/verification` or `services/safety` to score them);
  dates carry a precision (day/month/year/none) and undated events are their own group;
  scoring contracts return counts, aggregators return rates with Wilson intervals.
- **YGH-64** the synthetic patient generator, `evaluation/`, designed with the owner one
  decision at a time (`benchmarks/personal_health_bench/README.md`, decisions 1-18): templates
  plus a seeded random generator (no LLM), 8 archetypes, 247 documents, 615 ground-truth
  events, noisy documents for every patient. `python -m evaluation.generate` then
  `python -m evaluation.dataset verify`; output is byte-identical for a seed and is NOT
  committed. 1,100+ tests, recomputed independently of the generator; mutation checks confirm
  they fail when the generator is broken.
- **Finding to carry forward:** on this dataset the real extraction pipeline recovers about
  20-24 percent of the facts (precision 100 percent): the regex placeholder against realistic
  noise. Expect low extraction scores in the benchmark; that is the framework's point.
- **YGH-66** the question set (decision 19 in the dataset spec): 632 QA questions over the nine
  categories plus 150 safety questions (100 must-not-answer, 50 must-answer; new wording,
  disjoint from `tests/safety`), generated from each patient's truth file. YGH-65 (ground-truth
  packaging) is effectively delivered by the generator's `truth/` files.
  **Protocol: do not run the safety classifier on the test-split safety questions until the
  final evaluation.**
- **YGH-67** the runner and scorers (framework v0.4, section 10): extraction, retrieval,
  timeline, QA and independent safety scorers; `python -m evaluation.run --systems a,b
  [--split dev|test|both] [--final] [--no-pipeline] [--out DIR]`; writes metrics.json,
  results.csv, answers.jsonl, report.html (+ audit_sample.csv for test runs). Test split needs
  `--final` and a clean git tree. Validated with `fake:abstain` and `fake:echo`. Found and fixed
  along the way: an ORACLE-mode leak of benchmark-only fields to systems, question-set v1
  containing its own answers (now v2 with an invariant test), and several matching bugs.
- **YGH-68** `direct_llm` baseline (framework section 11): question only, plain Ollama call,
  exact refusal sentence sets `abstained`, near misses counted. Run it with
  `python -m evaluation.run --systems direct_llm` (needs Ollama; about 2.5 s per question).
- **YGH-69** `naive_rag` baseline (framework section 12): `nomic-embed-text` embeddings via Ollama
  (`ollama pull nomic-embed-text`), recursive chunks 500/50, top 5, LangGraph pipeline, exact
  refusal sentence. **Finding:** records are short (median 5 chunks), so top-5 hands the model
  the whole record for 30 of 50 patients; documented as a limitation, parameters not changed.
  `langgraph` and its dependencies were added to `requirements.txt` (pip on this machine needs
  `pip --isolated` because a user pip config points at an unreachable extra index).
- **Next:** baseline YGH-70 (structured retrieval) is a registered name in
  `evaluation/systems/REGISTRY` waiting for an implementation; then full
  evaluation YGH-71 and the report `docs/07_BENCHMARK_REPORT.md` (YGH-72). Protocols: do not
  run the safety classifier on the test split before the final evaluation; change nothing
  because of a test-set result.

### Session continuity
`eb7c1d1` — this file was first created, then is being rewritten right now into the version
you're reading, to be exhaustive rather than a summary.

---

## 4. Current verified test data (on the `realtest@example.com` account)

This account (`testpass123`) has real, live-verified data from M11 testing:
- **Two uploaded, processed documents**, dated 2026-01-10 and 2026-04-10.
- **Confirmed events across both:** HbA1c (7.2% → 6.5%), Metformin (500mg twice daily → 1000mg
  twice daily), an Annual Physical / Follow-up Visit encounter, and a headache symptom.
- This is what every M11 "verified live" claim above was tested against. Useful for immediately
  re-exercising the assistant without re-uploading anything.

A separate seeded demo account exists if `database/seeds/seed.py` has been run:
`demo@example.com` / `demo-password-only-for-dev` — much sparser (2 HbA1c events only).

---

## 5. How to run everything, from zero

*(Benchmark data: `python -m evaluation.generate` builds it, and `python -m evaluation.dataset
verify` checks it; see `benchmarks/personal_health_bench/README.md`.)*

```bash
cd personal-health-intelligence

# 1. Database (host port 5433, not 5432: a native Windows Postgres service often owns 5432)
docker compose up -d postgres
docker compose ps   # wait for "healthy"

# 2. Ollama (local LLM, used by the AI assistant — default provider)
ollama serve                      # if not already running
ollama pull llama3.2:3b           # if not already pulled

# 3. Backend
python3 -m venv .venv && source .venv/bin/activate   # first time only
pip install -r requirements.txt                        # first time only
export DATABASE_URL="postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi"
alembic upgrade head              # first time / fresh DB only
uvicorn services.api.main:app --reload

# 4. Frontend (separate terminal)
cd apps/web
npm install        # first time only
npm run dev

# 5. Open
# http://localhost:3000/login   (realtest@example.com / testpass123)
```

**Windows:** the venv activates with `.venv\Scripts\Activate.ps1` (PowerShell) or
`.venv\Scripts\activate.bat` (cmd), not `bin/activate`. In cmd, set the variable with
`set DATABASE_URL=...` (no quotes). `uvloop` was removed from `requirements.txt` (Linux/macOS only;
uvicorn falls back to asyncio). If `pip` hangs, a user pip config may point at an unreachable extra
index; bypass with `PIP_CONFIG_FILE=NUL`. The Docker volume can be lost if Docker Desktop is reset,
which empties the database: re-run `alembic upgrade head`, register again, re-upload, re-confirm.

**LLM provider:** defaults to `ollama`, zero config needed. To use OpenAI or Grok instead, set in
`services/api/.env` (gitignored — copy from `.env.example`):
`LLM_PROVIDER=openai|grok|ollama` plus the matching `*_API_KEY`.
**Never paste a real API key into a Claude conversation** — set it directly in the `.env` file,
outside the chat.

**Testing the assistant directly** (no frontend page exists for it yet — see §6):

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"realtest@example.com","password":"testpass123"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST localhost:8000/api/v1/assistant/ask \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question": "Is my HbA1c improving?"}' | python3 -m json.tool
```

Or use `http://localhost:8000/docs` — click **Authorize**, paste the raw token (not
`Bearer <token>`, just the token), then try any endpoint interactively.

---

## 6. What is explicitly NOT built yet (so it is never mistaken for a bug)

- **No frontend page for the assistant.** `apps/web/src/app/assistant/page.tsx` and
  `settings/page.tsx` are empty placeholders (`PagePlaceholder` component). The assistant is
  fully functional via the API; wiring a real chat UI is M15 (Frontend Productization).
- **No automated integration tests.** Only 394 tests exist (48 unit, 346 safety), all pure-function with zero DB/model dependency
  (`tests/unit/`, zero DB/network dependency, run in ~0.2s). Every multi-service flow (upload
  pipeline, the AI assistant end to end, cross-user isolation) was verified **manually, live**,
  and the result documented in each Linear issue's comment — formalizing these into
  `tests/integration/` is M18 (YGH-97, YGH-98, YGH-99).
- **No request correlation IDs, no AI call tracing, no log aggregation.** Structured JSON logging
  exists (`app/core/logging.py`); cross-request correlation doesn't. `AITrace` table exists in
  the schema with zero writers — reserved for M16.
- **Evidence verification is built (M12)** but is rule-based, not an LLM: see the limitation noted in §3.
- **Safety layer is built (M13)** but rule-based, English-only, and has no conversation memory (each
  question is classified alone). See `docs/02_SAFETY_SPEC.md` section 9. Run `pytest tests/safety`
  on every AI-related change. CI wiring is M18.
- **Deployment, CI, production Docker, security review, data-deletion UI:** all unstarted (M17,
  M18).

---

## 7. Deliberate placeholders — real, documented, swappable, not hidden

Two pieces of this system are intentionally fake, for the same reason in both cases: get the
**entire pipeline** provably working end-to-end, for free, offline, before spending money or
infrastructure effort on quality that nothing downstream could verify yet.

| Placeholder | File | Swap point | Why it's safe to leave for now |
|---|---|---|---|
| Regex fact extraction, not an LLM | `services/extraction/*_extractor.py` | `extract(pages) -> list[ExtractedFact]` | Matches a documented synthetic format exactly; produces real, valid `HealthEvent` rows the rest of the system can't tell apart from "real" extraction |
| Deterministic hashed embeddings, not a real model | `services/retrieval/embeddings.py::embed()` | `embed(text) -> list[float]`, keep `EMBEDDING_DIM` synced with `models.py` | Proves pgvector storage + cosine search work correctly; semantic *quality* is weak but the plumbing is real |

**The LLM itself is real** (Ollama/OpenAI/Grok, your choice) — only extraction and embeddings are
faked, and only because M14 (Evaluation) and M11b (fine-tuning) are what will eventually need
real quality there to benchmark against.

---

## 8. What has to be done next — the full remaining roadmap

In brief-mandated order (see `docs/00_MVP_EXECUTION_PLAN.md` §18 "Priority rule if time is
limited" for why this order, not an arbitrary choice):

### M12 — Evidence Verification (DONE, kept for reference)
Takes M11's `Answer.claims` (each already carrying `citation_indices` from YGH-84) and actually
checks them, instead of trusting the model's self-report.
- **YGH-52** Claim extraction — likely already satisfied by YGH-84's `Claim` structure; confirm
  against this ticket's specific acceptance criteria before assuming so.
- **YGH-53** Claim-to-evidence matching — does claim text actually follow from its cited
  evidence's text? (Not just "a citation index exists" — M11 already guarantees that; this is
  "is the citation *correct*".)
- **YGH-54** Unsupported claim detection — label each claim SUPPORTED / UNSUPPORTED /
  CONTRADICTED / INSUFFICIENT.
- **YGH-55** Contradiction detection — e.g. two documents disagreeing on a dose; must be
  surfaced, never silently resolved by picking one.
- **YGH-56** Answer verification gate — block unverified high-risk claims from ever reaching the
  user.
- **YGH-57** Verification test suite — explicitly must include: missing evidence, contradictory
  records, wrong date, wrong value, hallucinated medication, unsupported causal claim.

**Concrete, already-observed test case to build YGH-57 around:** the TREND hallucination from
§3 ("HbA1c is within target range" contradicting its own cited 7.2% evidence) is real, logged,
reproducible evidence of exactly the failure mode M12 must catch. It was fixed by hand in M11
(handing the model a pre-computed trend instead of letting it reason); M12 is what makes that
kind of catch automatic and general, not something that needs discovering and hand-fixing every
time it recurs for a question type nobody specifically guarded.

### M13 — Safety Layer (DONE, kept for reference)
Safety taxonomy (`SAFE_INFORMATIONAL`, `PERSONAL_HISTORY`, `HIGH_RISK_MEDICAL`,
`DIAGNOSIS_REQUEST`, `PRESCRIPTION_REQUEST`, `TREATMENT_REQUEST`, `EMERGENCY_REQUEST`), a
classifier, response policies (`ALLOW`, `ALLOW_WITH_UNCERTAINTY`, `CLARIFY`, `ABSTAIN`,
`REDIRECT_TO_PROFESSIONAL`), dangerous-query tests ("Do I have cancer?", "Should I stop taking
this medicine?"), and a regression suite that must run on every future AI-related change.

### M11b — Model Fine-Tuning (parallel track, not sequential — see below)
Added mid-project specifically to cover a fine-tuning/MLOps resume line (LoRA, PyTorch, Hugging
Face, PostgreSQL experiment tracking, Docker/AWS deployment). 11 issues (YGH-127–137), currently
all in Backlog. **Depends on M14's synthetic QA dataset existing first** — it reuses that data for
training, so it cannot start before M14 produces it, even though it's numbered to sit near M11 in
the milestone list. See `docs/00_MVP_EXECUTION_PLAN.md`'s M11b section for the full breakdown
(dataset conversion → GPU setup → base model choice → LoRA training → experiment tracking →
model registry → provider-interface extension → inference serving → Docker → AWS deploy →
benchmark comparison against the hosted-LLM baseline).

### M14 — Evaluation & PersonalHealthBench
50 synthetic patients, a question dataset across 9 categories (retrieval, temporal, comparison,
multi-document, trend, contradiction, missing information, evidence, uncertainty), a benchmark
runner, three baselines (direct LLM, naive RAG, structured-only), full-system evaluation against
all three, and the benchmark report. **This is what M11b's fine-tuning needs before it can start.**

### M15 — Frontend Productization
Real dashboard, documents page polish, document detail page, **the assistant page that doesn't
exist yet**, loading/error/empty states everywhere.

### M16 — Observability & Reliability
Structured logging is already real; this adds request IDs threaded through every log line,
AI call tracing (the already-schema'd but unused `AITrace` table), performance metrics,
retry/timeout policies, explicit failure-state handling per async stage.

### M17 — Security & Data Lifecycle
Formal security review (auth, IDOR, file upload, injection, prompt injection, XSS/CSRF, secrets),
real data deletion and account deletion (cascading), a sensitive-log audit, privacy documentation.

### M18 — Testing, CI & Production
Formalizes everything that's been manually verified so far into real automated integration tests
(YGH-97 upload pipeline, YGH-98 AI pipeline, YGH-99 authorization isolation), AI and safety
regression test suites, a CI pipeline (lint/typecheck/unit/integration/AI-regression/safety/build),
production Docker configuration, separated dev/test/prod environments, actual deployment.

### M19 — Final Validation & Launch
End-to-end synthetic demo dataset with a deliberate contradiction and a deliberate missing-info
case, a full E2E test (register → upload → review → timeline → trend → ask → verify → delete),
final benchmark run, failure analysis, final architecture review (confirming docs still match
reality — **re-run this against `ARCHITECTURE_MAP.md` specifically**), final technical report,
README, demo video, sign-off against the definition-of-done checklist in the brief §16.

---

## 9. How we are going to do it — the method, not just the plan

This is the part that matters most for a new session to get right. The *order* of tasks above is
in the brief; **the discipline below is what actually made M1–M11 trustworthy**, and dropping it
is how a project like this quietly rots into something that looks done but isn't.

1. **One Linear issue at a time.** Move it to `In Progress` before starting. Never batch unrelated
   issues into one commit — related ones (e.g. YGH-80/81/82, which form one coherent unit) can
   share a commit if they were genuinely built together, but say so explicitly in the message.

2. **Verify against the real running stack, not just unit tests, before calling anything Done.**
   This is the single most important lesson from this entire project: **every real bug found so
   far — the `passlib`/`bcrypt` incompatibility, the `seed.py` import error, the Swagger auth
   mismatch, the structured-search substring bug, three separate JSON-formatting failures, the
   TREND hallucination — was found by actually running the code against live Postgres and a live
   LLM, not by unit tests or by reading the code.** Unit tests are necessary (394 exist, all pure
   functions, all passing) but they are not sufficient on their own for anything that touches the
   database, the network, or a model. If Docker/Ollama aren't available in a given environment,
   say so explicitly and name exactly what couldn't be verified — never claim "done" for something
   only unit-tested when it could have been live-tested and wasn't.

3. **When live verification surfaces a real bug, fix it in the same sitting, even if it's not the
   current ticket's scope.** Document it clearly as "found while testing X, not X's original
   scope" in both the commit message and the Linear comment — never silently bundle an unrelated
   fix without calling it out, and never defer a found bug without saying so explicitly and why.

4. **Keep placeholders loud, not hidden.** Anything faked for cost/offline reasons (extraction,
   embeddings) must: (a) be documented in its own file's docstring, (b) have a single, clear swap
   point (one function signature nothing else needs to change around), (c) be listed in
   `ARCHITECTURE_MAP.md` §10 and in this file's §7. A placeholder nobody can find is a liability;
   a documented one is a deliberate engineering choice.

5. **Commit message and Linear comment discipline:** every commit that closes a Linear issue
   references its ID in the message (`YGH-NN: ...`). Every Linear comment states what was built,
   which file, and **exactly how it was verified** (live output, specific test case, or "unit
   tested only, needs live verification once Docker is available" if that's the honest state).

6. **Python 3.9 compatibility is deliberate, not an oversight.** `pyproject.toml` pins
   `target-version = "py39"` for ruff specifically so `ruff --fix` doesn't rewrite
   `Optional[X]`/`List[X]` into 3.10+-only `X | None`/`list[X]` syntax that would break local dev
   on this machine's actual Python (3.9.6, no newer version installed). The Docker image uses
   Python 3.12. If local dev ever moves to 3.10+, update that one line and re-run `ruff --fix`
   deliberately — don't let it happen by accident via an unrelated lint pass.

7. **Schema separation is deliberate.** `packages/schemas/models.py` (SQLAlchemy — the database's
   truth) and the various Pydantic schemas (`extraction.py`, `evidence.py`, `answer.py`,
   `intent.py` — API/boundary contracts) are always separate files. A table is not an API
   response; conflating them is how a `hashed_password` field accidentally leaks into a response.

8. **Provider/strategy abstractions over hardcoded calls, everywhere an external or swappable
   dependency exists.** The LLM provider (`services/reasoning/providers.py`), the extractors, and
   the embedding function all follow the same shape: one interface, multiple implementations,
   selected by one line (an env var or a dict lookup), so nothing downstream needs to change when
   the implementation does. Follow this pattern for any new external dependency (a real OCR
   service, a real classifier, M13's safety classifier, M11b's fine-tuned model) rather than
   calling it directly from business logic.

9. **When something you build to test with (a synthetic document, a seeded account) reveals a gap
   in earlier work, fix the earlier work — don't work around it in the test.** The structured-
   search substring bug and the TREND hallucination were both found exactly this way: building
   real test data for one task exposed a real defect in a previous milestone's code.

---

## 10. If you are a new Claude Code session reading this for the first time

Do this, in order:
1. `git log --oneline | cat` and check Linear — confirm §2's facts are still accurate; they won't
   be forever.
2. Read `docs/ARCHITECTURE_MAP.md` for the technical ground truth.
3. Run the launch sequence in §5. Confirm the assistant actually answers a question correctly
   against the `realtest@example.com` account (§4) before writing any new code — that's your own
   "is the stack actually alive" check, same discipline as §9's rule 2.
4. Pick up at M14 (§8) unless told otherwise, following the method in §9 exactly — it's not
   optional stylistic preference, it's what kept this project's "Done" label meaning something
   real for 11 straight milestones.
5. Update this file (or write a new dated one) at the end of whatever milestone you complete.

---

*Last updated mid-M14, after YGH-69. An out-of-date handover is worse
than none — keep it honest or delete the stale parts rather than let them mislead the next
session.*
