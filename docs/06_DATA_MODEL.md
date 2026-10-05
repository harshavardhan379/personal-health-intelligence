# Data Model

Deliverable for YGH-20. Implemented in `packages/schemas/models.py`.

## Entities

```text
User
  id, email, hashed_password, created_at, updated_at

Document
  id, user_id, filename, mime_type, storage_path,
  uploaded_at, processing_status, classification, classification_confidence,
  processing_error

DocumentPage
  id, document_id, page_number, text, is_ocr

HealthEvent
  id, user_id, document_id, event_type (lab|medication|encounter|symptom),
  event_date, event_date_precision (day|month|year),
  data (JSONB — shape depends on event_type, see packages/schemas/extraction.py),
  confidence, extraction_method, review_status (pending|confirmed|edited|rejected),
  reviewed_at

Provenance
  id, health_event_id, document_id, page_number, quote

Embedding
  id, user_id, document_id, page_number, chunk_text, vector (pgvector, 384 dims)

AITrace
  id, user_id, question, intent, provider, latency_ms, token_usage,
  verification_result, created_at
```

## Relationships

```text
User 1─< Document 1─< DocumentPage
User 1─< HealthEvent >─1 Document (nullable: an event always has a source document)
HealthEvent 1─< Provenance >─1 Document
User 1─< Embedding >─1 Document
User 1─< AITrace
```

## Invariants enforced in code, not just convention

1. **No confirmed `HealthEvent` without at least one `Provenance` row.** Enforced in
   `services/api/app/api/v1/review.py::confirm_event`.
2. **Every read of "the patient's health data" (timeline, trends, retrieval) filters on
   `review_status IN ('confirmed', 'edited')`.** Enforced via the single shared query helper
   `packages/schemas/queries.py::confirmed_events_query`, so no call site can forget it.
3. **Every table that holds user data carries `user_id`, and every query filters on it.**
   Enforced via `packages/schemas/queries.py` helpers taking `user_id` as a required argument.

## Partial dates (YGH-31)

`event_date` is always a full date internally, but `event_date_precision` records how much of it
was actually documented (`day`, `month`, or `year`). Display code must respect precision and never
show a fabricated day/month the source document didn't state.
