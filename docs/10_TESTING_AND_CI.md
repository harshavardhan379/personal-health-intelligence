# Testing and CI (M18: YGH-96 to YGH-102)

What is tested, how to run each part, what CI does on every push, and what the tests deliberately
do not cover. The numbers are from the run recorded in `docs/HANDOVER.md`.

## The suites

| Suite | Where | Tests | Needs | What it proves |
|---|---|---|---|---|
| Unit | `tests/unit` | 405 | nothing | schemas, parsing, extraction, validation, timeline, trends, retrieval, verification, authorization, logging, rate limits, upload validation, security headers, settings |
| Safety | `tests/safety` | 573 | nothing | the prohibited and high-risk query suite: classifier, policy, gate, output guard, **and every question through the real HTTP endpoint** |
| Benchmark framework | `tests/evaluation` | 1,087 | Postgres for the `full` system's tests | the evaluation framework, its baselines and scorers (M14) |
| AI regression | `tests/regression` | 19 | Postgres | fixed questions and the properties every answer must keep |
| Integration | `tests/integration` | 121 | Postgres | the real API, pipeline, review, retrieval, safety, verification, tracing, deletion and isolation on a real database |

Total **2,205 Python tests** plus **40 web tests** (`apps/web/tests`, Node's runner), with
**97.7% line coverage** of `services/` and `packages/` (CI fails below 95%).

### Run them

```bash
pip install -r requirements-dev.txt
docker compose up -d postgres          # port 5433, for the Postgres-backed suites

pytest                                 # everything
pytest -m "not integration"            # no database needed (2,065 tests)
pytest -m safety                       # the safety regression suite
pytest tests/regression                # the AI regression suite
pytest tests --cov=services --cov=packages --cov-fail-under=95   # what CI runs last
cd apps/web && npm test && npx tsc --noEmit && npm run lint
```

Markers (`unit`, `safety`, `benchmark`, `regression`, `integration`) are added by directory in
`tests/conftest.py`. Without Postgres the database-backed tests **skip**; with
`REQUIRE_POSTGRES=1` (CI sets it) they **fail** instead, so a missing database can never look like a
pass.

## What "integration" means here

`tests/integration/conftest.py` drops and rebuilds `phi_integration_test` (its name must end in
`_test`; anything else is refused) by running the project's own **Alembic migrations from zero**, so
the migrations are tested on every run. Tests then use the real routes with real JWT logins, the real
pipeline (upload, OCR-free text extraction, review, embeddings with pgvector), the safety gate, the
verifier, the output guard and the trace table. Only the language model is stood in (a scripted
provider), so the suite is deterministic and needs no Ollama.

The real database matters: SQLite, used by the unit tests, enforces no foreign key and no cascade,
so deletion order, cascades and constraint failures are only checked here.

| Issue | File | Covers |
|---|---|---|
| YGH-97 upload pipeline | `test_upload_pipeline.py` | upload, processing, extraction, review, confirm/edit/reject, timeline, search, dedup, multi-page, failures and retry, stale runs, metrics |
| YGH-98 AI pipeline | `test_ai_pipeline.py` | question, retrieval, generation, verification, delivery; supported / contradicted / uncited / invalid-JSON answers; trend and latest-value questions; refusals; prompt injection; outages; the trace row; rate limit |
| YGH-99 isolation | `test_authorization_isolation.py` | **every route found from the API's own schema** requires a login (and refuses forged, expired and unsigned tokens); two real users against every endpoint taking an id |
| YGH-119/121 deletion | `test_data_deletion.py` | one document, all data, the account: records, files, embeddings, traces, rollback, failures, rate limit, other users untouched |
| YGH-123 log audit | `test_sensitive_logging.py` | planted values through every flow including failures; none may reach any log |

## The two regression suites

**AI regression** (`tests/regression/ai_cases.json`, YGH-100): a fixed patient record and 19 fixed
questions, each with the properties the answer must keep (intent, safety action, claim labels,
text that must or must not appear, whether the model was called, the trace status). The model's
reply is scripted per case and its citations are resolved from the **real prompt**, so a change to
retrieval order or evidence numbering breaks a case. Adding a case is a data change. Changing an
expectation is a decision to be explained in the commit.

It is not a measure of the real model: that is the benchmark (`docs/07_BENCHMARK_REPORT.md`).

**Safety regression** (`tests/safety`, YGH-101): 57 prohibited and high-risk questions (including the five
from the brief), 84 further unsafe probes, and 83 legitimate questions that must **not** be refused, all
now asked through `POST /assistant/ask` with a model that fails the test if it is ever called. It
runs on every push and must pass before any release. Rule for contributors, unchanged: when a real
unsafe (or wrongly blocked) question is found, add it to `tests/safety/cases.py` first, watch it
fail, then fix the rules.

*Known gap, kept visible:* two legitimate personal-history questions ("Did I stop taking Metformin
at some point?", "May I have a summary of my results?") get a "please rephrase" (CLARIFY) instead of
an answer. They are named in `KNOWN_CLARIFY`; the test fails when they are fixed, as a prompt to
remove them.

## Mutation testing, the practice behind these numbers

A passing test proves little unless it fails when the code is wrong. For the security, deletion and
upload code each protection was removed in turn (about 130 mutations in M17, and the same for M16)
and the tests had to fail. What that found, besides gaps that were closed: a valid multi-picture
JPEG the new upload check would have refused; redundant deletion code (removed); and the
tests themselves being flaky (a test that searched `"7.2"` in the random numbers of a log record
failed one run in a few hundred; fixed with a helper that reads only what the application logged).
Equivalent mutations (no observable change) are accepted and listed in the commit messages.

The AI regression suite found two real bugs in the claim verifier: a correct whole-number claim
("LDL was 160") was called CONTRADICTED because the evidence prints `160.0`, and "I could **not**
find..." was handled differently from "I couldn't find...". Both are fixed and pinned. **The benchmark
report describes the verifier before these fixes.**

## CI (`.github/workflows/ci.yml`)

On every push to `main` and every pull request:

| Job | Steps |
|---|---|
| `backend` | `ruff` -> safety regression -> AI regression -> **all tests with the 95% coverage gate**, on Python 3.12 with a `pgvector/pgvector:pg16` service on port 5433 and `REQUIRE_POSTGRES=1` |
| `web` | `npm ci` -> lint -> `tsc` -> tests -> production build |
| `docker` | build both production images; validate `docker-compose.prod.yml`; **prove the API image refuses to start on development settings** |
| `audit` | `pip-audit -r requirements.txt --strict` and `npm audit --omit=dev --audit-level=high`: a newly published advisory fails the build |

**Honest limits of what has been verified:** GitHub Actions cannot be run on this machine, so the
workflow file itself has not executed. Its YAML was validated and **every command in it was run
locally** and passes (lint, the three pytest steps including the coverage gate, the web steps, both
image builds, the compose validation and the refusal check). The first real run may still surface
an environment difference (Python 3.12 in CI versus 3.11 here, an Ubuntu runner versus Windows).

## Not covered

- No automated browser end-to-end test (the full register -> upload -> review -> ask -> delete flow
  is M19, YGH-107). The web app's pure logic is unit-tested; its pages were exercised by hand in a
  browser, including the production build.
- The real model is not in CI (the benchmark measures it).
- OCR: Tesseract is not installed on the development machine, so the OCR timeout paths are tested
  with stand-ins; OCR itself was verified once, end to end, inside the production image
  (a scanned PNG and an image-only PDF both produced facts).
- Load and concurrency: nothing here tests more than one request at a time.
