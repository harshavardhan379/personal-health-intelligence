# Privacy (YGH-125)

What this system stores about a person, why, for how long, how to delete it, and what leaves the
machine. Written from the code and the live database as they are, not from intentions.

> **This is a prototype for synthetic data.** It is not a certified medical or compliance
> product: no HIPAA or GDPR compliance is claimed, there is no consent workflow, no data-processing
> agreement, no export of a person's data, and no audit trail of who looked at what. Do not upload
> real health records. The settings page and the README say the same.

## 1. What is stored

| Where | What | Why it is kept |
|---|---|---|
| `users` | email, bcrypt password hash, created-at | to log in |
| `documents` | original filename, file type, storage path, processing status, document type guess, failure reason and stage | to list documents and show their state |
| Uploaded files (`storage/<user id>/`) | the original PDF or image, exactly as uploaded | to show the original next to what was extracted |
| `document_pages` | the full text of every page (read from the PDF, or OCR of a scan) | the source the extraction reads, and what the document page shows |
| `health_events` | each extracted fact (lab value, medication, visit, symptom), its date, confidence, and your review decision | the timeline, trends and the assistant's answers |
| `provenance` | for each fact, the page and the short quote it came from | so every fact can be traced back to where it was found |
| `embeddings` | a numeric vector and a one-line text of each **confirmed** fact | to find relevant facts for a question; made on this server, never sent out |
| `ai_traces` | per assistant call: time, model, prompt version, token counts, ids of the facts used, how many claims were supported, the safety decision, and the **length** of the question | to understand and debug the assistant. **Not** the question, the answer or any fact text |
| Browser (`localStorage`) | one login token | to stay logged in |

Not stored anywhere: your questions, the assistant's answers, the exact text sent to the model,
IP addresses on disk (the login rate limiter keeps an address in memory for up to 15 minutes and
forgets it on restart), analytics or advertising identifiers. There are
no third-party trackers or cookies in the web app.

### Logs

The API writes one JSON line per request to its standard error (the host decides where that goes
and for how long it is kept). A line holds: time, a random request id, the HTTP method, the
**route template** (`/api/v1/documents/{document_id}`, not the URL), the status and the duration.
Pipeline and deletion lines hold document ids, page and fact **counts**, a stage name and an
exception **class**. They never hold a question, an answer, a fact, a quote, a filename, an email,
a password or an exception message. Two mechanisms enforce it: a test that scans the whole source
tree for log calls that pass such values, and a logging filter that redacts fields with sensitive
names. The server's own access log (which prints query strings such as a search) is switched off.
See `docs/09_SECURITY_REVIEW.md`, "Sensitive log audit".

## 2. Retention

There is **no automatic expiry**. Everything above is kept until the person deletes it. Logs are
kept as long as the deployment's log store keeps them (this repository does not ship one).
`ai_traces` rows are removed when the person deletes their data or account.

## 3. Deleting data

All three are permanent, in the web app (Documents, Settings) and in the API:

| Action | API | Asks for the password | Removes |
|---|---|---|---|
| Delete one document | `DELETE /api/v1/documents/{id}` | no | the document, its pages, its facts, their sources, their embeddings, the original file |
| Delete all my data | `DELETE /api/v1/account/data` | yes | every document, page, fact, source, embedding, trace and uploaded file; the account stays |
| Delete my account | `DELETE /api/v1/account` | yes | all of the above and the account itself; the login stops working immediately |

How it behaves, each point covered by a test against a real Postgres
(`tests/integration/test_data_deletion.py`):

- The database part is **one transaction**: either everything for the request is gone or nothing.
- Files are removed **after** the commit. If a file cannot be removed, the records are already
  gone, the failure is logged (class only) and reported (`files_failed`), and the leftover file
  is private and not linked to anything. Deleting all data or the account removes the user's whole
  upload folder, which also removes any file no record pointed at.
- A stored path that points outside the storage folder is never deleted.
- The password is checked again for the two bulk deletions, and wrong guesses count against the
  same limit as login (5 in 15 minutes), so a stolen login token cannot be used to guess it.
- Deleting one person's data touches nobody else's.

What deletion does **not** reach, stated plainly:

- **Backups and snapshots** of the database or the storage volume, if the host makes them. This
  repository makes none; a deployment that does must delete from them too.
- **Log lines** already written. They hold ids, counts and durations only, no content.
- **The model provider**, if a hosted model was used (section 4): see below.
- **The browser**: the web app removes its login token when the account is deleted; it keeps
  nothing else.
- **Cached data**: the server has no cache layer. The in-process metrics hold timings only.

## 4. Third-party AI

The assistant's model is chosen by `LLM_PROVIDER`:

| Provider | Where it runs | What leaves this system |
|---|---|---|
| `ollama` (the default) | on the same machine or private network | nothing leaves it |
| `openai` / `grok` | the provider's servers | the **question**, and the **evidence text** the answer is built from, per call |

When a hosted provider is used, the request contains: the fixed instructions, the person's
question, and up to 8 short numbered lines of the confirmed facts retrieved for it, each like
`[1] (page 1, source=structured) HbA1c: 7.2 %`, plus a request id header. It does **not** contain
the uploaded files, the full page text, the person's email, the filename or any other fact. The
settings page shows the active provider and says whether the model is local.

What this system cannot control: how the provider retains or uses what it receives is governed
by that provider's terms and the account's settings (zero-retention options, regional processing,
and a data-processing agreement for real data are the operator's to arrange). Deleting a person's
data here does not delete what a provider may already hold. For that reason the default is local,
and the web app warns, on the settings page, whenever the provider is not.

Embeddings are computed in-process and no embedding service is called.

Text inside a document is treated as data, not instructions: only structured fields extracted by
fixed rules (never the surrounding sentences) reach the model, the prompt tells the model never to
follow instructions found in evidence, and an output guard and a claim verifier check what comes
back (`docs/09_SECURITY_REVIEW.md`, finding F14).

## 5. Who can see what

Every route that returns or changes personal data requires a login and is scoped to the logged-in
user's own id taken from the verified token, never from the request. Another user's document,
fact or file returns "not found". This is tested by enumerating every route in the app and with
two real users against every endpoint that takes an id
(`tests/integration/test_authorization_isolation.py`). There is no administrator role and no
back-office view; anyone with direct database or server access can of course read the data, so
production access to both must be restricted by the operator.

## 6. Known gaps (for a real deployment)

- No encryption of the stored data beyond what the disk, volume and database provide.
- No email verification, password reset, multi-factor login or login history.
- No data export (portability), no consent record, no age or jurisdiction checks.
- Login tokens last 24 hours and cannot be revoked individually (deleting the account does
  invalidate them).
- The rate limiter and metrics are per server process.
