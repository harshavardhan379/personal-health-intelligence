# Observability & Reliability (M16)

What the system records about itself, how failures are handled, and the one rule behind all of it.
Code lives in `packages/observability/` and, for the pipeline, `services/api/app/core/pipeline.py`.

## The rule: metadata only

Telemetry (logs, traces, metrics, error bodies) may hold **ids, counts, durations, statuses and
exception class names**. It may never hold a question, an answer, a claim, a quote, a filename, a
parser or model error message, or any field of a health record. Exception *text* is never logged
or stored, because parser, OCR and database errors can quote the document.

This is enforced, not just intended:

- `RedactionFilter` (logging) replaces any field with a sensitive name by `[redacted]` and truncates
  over-long messages, as a last line of defence.
- `tests/unit/test_logging_hygiene.py` scans the source with the AST and fails if any log call
  passes a risky name (text, question, answer, quote, filename, ...) or an exception object.
- `ai_traces` has no column that could hold content; `tests/unit/test_ai_tracing.py` checks the
  stored row for the question, answer and evidence text.

## Request IDs (YGH-91)

One id follows a request everywhere: `X-Request-ID`.

| Hop | What happens |
|---|---|
| Browser | `apiFetch`/`apiBlob` send a fresh 32-hex id per call |
| API | `RequestContextMiddleware` accepts a caller's id only if it matches `[A-Za-z0-9._-]{1,64}`, else makes a new one; it is a ContextVar, so it reaches sync endpoints (threadpool copies the context) |
| Logs | every JSON line carries `request_id` |
| Response | echoed in the `X-Request-ID` header and in the error body's `request_id`; exposed through CORS; also added by the unhandled-exception handler, which runs outside the middleware |
| Model | sent to Ollama/OpenAI as `X-Request-ID` |
| Background | upload and reprocess hand the id to `run_pipeline`, which sets it for the run |
| UI | a failed call shows "Reference: <id>" (not for session-expired or not-found) |

## Logging (YGH-90)

JSON, one object per line: `timestamp, level, logger, request_id, message` plus the event's own
fields. Event names are fixed strings (`request_finished`, `stage_failed`, `document_failed`,
`retrying_dependency`, ...), not sentences with data in them. Each request logs one
`request_finished` line (method, route template, status, `duration_ms`, `slow`); `slow` is true
above 5 s. Level: `LOG_LEVEL` (default INFO).

## AI tracing (YGH-92)

Every call to `POST /assistant/ask` writes one `ai_traces` row, including blocked, no-evidence,
provider-error and crashed calls. A failure to write the trace is logged (class only) and
swallowed: telemetry never breaks the answer.

| Column | Meaning |
|---|---|
| `request_id` | joins to the log lines |
| `provider`, `model`, `prompt_version` | what answered; `PROMPT_VERSION` is pinned to the prompt text by a hash test, so changing the prompt forces a version bump |
| `latency_ms`, `llm_ms` | whole call / model call |
| `input_tokens`, `output_tokens`, `token_usage` | from the provider; NULL if it did not report |
| `retrieval_event_ids`, `evidence_count` | which events were retrieved |
| `claim_labels`, `verification_result` | counts per label; `no_claims` / `all_supported` / `flagged` |
| `safety_action`, `status`, `intent` | `status` is ok, blocked, no_evidence, provider_error or error |
| `question_chars` | the **length** of the question, never the question |

Migration `0003` dropped the old `ai_traces.question` column (it was never written to).

## Metrics (YGH-93)

`GET /api/v1/metrics` (signed in): count, errors, mean, p50, p95 and max in milliseconds, per
stage — `upload`, `processing`, `extraction`, `retrieval`, `llm`, `verification` — and per route
(`requests`, keyed by route template so one route is one series).

Limits, stated plainly: in-process, so **per server process and reset on restart**; percentiles
cover the last 1000 observations of each series. It is a diagnostic for a prototype, not a
monitoring system. Nothing is exported.

## Timeouts and retries (YGH-94)

One table, `POLICIES` in `packages/observability/resilience.py`.

| Dependency | Timeout | Retry | Why |
|---|---|---|---|
| LLM | 10 s connect, `LLM_TIMEOUT_SECONDS` read (90 s) | 3 tries, 1 s then 2 s (max 8 s), +25% jitter, only on refused/dropped connection, 429, 5xx | a slow model stays slow, so a read timeout is **not** retried; a 4xx never is |
| OCR | 60 s per page | none | a page that hits the limit hits it again; the document fails with a clear reason and the user can retry |
| Embeddings | none | none | computed in-process, there is no network call. A hosted embedding service would take LLM-style retries and a 30 s timeout |
| Storage | none | 3 tries, 50 ms start, only locks/interrupts (`PermissionError`, `EAGAIN`, `EBUSY`, `EINTR`) | a missing file or a full disk is not transient |
| Database | 5 s connect, 30 s per statement, 10 s pool wait; connections recycled hourly | 3 tries, only for marking a document failed | request handlers do not retry their own writes |

The database timeouts are Postgres settings, applied only to Postgres URLs (`engine_options` in
`packages/schemas/db.py`). Checked live: `SHOW statement_timeout` returns 30 s and a runaway
statement is cancelled with `QueryCanceled`.

## Pipeline failure handling (YGH-95)

```
read_file -> parse -> ocr -> classify -> extract -> save
```

- Each stage fails as a typed error carrying its **stage** and a **fixed, user-safe message**.
- Nothing is written until every stage has succeeded: pages, events and provenance are added in
  one transaction and committed once. A failure rolls back, then records `failed` on a **fresh
  session** with `failed_stage` and `processing_error`. The rollback comes first on purpose: on
  Postgres a still-open transaction holds a lock on the document row, so the failure write would
  wait for it until the statement timeout.
- Marking failed is retried with the database policy; if even that fails it is logged
  (`document_failure_not_recorded`) and the stale-recovery below catches it.
- A crash in an unforeseen place still ends in a recorded failure (`failed_stage = unknown`).

| `failed_stage` | Meaning | What the page tells the user |
|---|---|---|
| `read_file` | stored file missing or unreadable | try again, or upload again |
| `parse` | damaged/protected PDF, or unsupported type | upload a fresh copy |
| `ocr` | scan unreadable, or a page timed out | try again; a clearer scan helps |
| `classify`, `extract`, `save` | later stages | try again |
| `interrupted` | the process died mid-run (see below) | try again |
| `unknown` | unforeseen crash | try again |

**Stale runs.** A document left `uploaded`/`processing` longer than 10 minutes (counted from
`processing_started_at`, falling back to `uploaded_at`) is marked `failed` at `interrupted`. This
runs at API startup and whenever documents are listed. It assumes one API process; the age limit
is what stops a second worker from killing a run the first is still doing.

**Reprocess.** `POST /documents/{id}/reprocess` (owner only; 404 for anyone else's, 409 unless the
document is `failed`, so a double click cannot start two runs). It first drops the earlier
attempt's pages and its still-pending events; **confirmed or edited events are never touched**.
The UI shows it as "Try processing again" on the list and the detail page.

## Honest limits

- The OCR timeout and page-render timeout are tested with stand-ins: **Tesseract is not
  installed on the development machine**, so a real hung OCR was not produced.
- The lock-order argument (rollback before marking failed) is tested by checking that the run's
  session holds no transaction at that moment; the actual Postgres lock wait was not reproduced.
- Metrics are per process and not persisted; there is no alerting.
- Stale recovery assumes a single API process (see above).
