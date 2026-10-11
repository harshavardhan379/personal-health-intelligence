# PersonalHealthBench: dataset and generator spec

Design record for YGH-64 (synthetic patient generator). The metrics live in
`docs/06_EVALUATION_FRAMEWORK.md`; this file says what the data contains and how it is
produced. **All patients are fictional. No real patient data is ever used.**

Status: DESIGN, version 0.1. Items under "Still to decide" are open.

## Where things live

| What | Path |
|---|---|
| Generator code | `evaluation/` (top level, so the runner is `python -m evaluation.run`) |
| Generated dataset (committed) | `benchmarks/personal_health_bench/data/` |
| This spec | `benchmarks/personal_health_bench/README.md` |

## How documents are produced

Plain-Python **templates plus a seeded random generator**. Facts are generated first
(the ground truth), then rendered into document text. Because the facts exist before the
text, ground truth is exact by construction, and the same seed produces byte-identical
output. No language model is involved, so there is no way for a document to drift away
from its ground truth.

Consequence to state in the benchmark report: documents are templated, so they are far
more regular than real clinical documents, even with the degradation below.

## Patients and split

- 50 patients, ids 1 to 50. **Development = 1 to 30, locked test = 31 to 50**, fixed by id.
- Archetype = assigned round-robin by id, so both splits contain every archetype and the
  test split has at least 2 of each.
- Nothing about a test-split patient may be used to change the generator, the extractors,
  retrieval or any prompt.

## Archetypes

| # | Archetype | Planted situation | Feeds question category |
|---|---|---|---|
| 1 | Improving chronic | HbA1c falling over 3 to 4 reports; Metformin dose rises | trend, temporal, comparison |
| 2 | Worsening chronic | HbA1c rising; dose escalation; a second medication added | trend, temporal |
| 3 | Stable | repeated readings within noise (systolic BP); one unchanged medication | trend (stable) |
| 4 | Contradiction | two documents disagree on a dose or value for the same date; **both are kept in the ground truth, marked as conflicting**, and a correct system surfaces the disagreement | contradiction |
| 5 | Missing information | sparse records; commonly asked-about tests are never done | missing information, uncertainty |
| 6 | Duplicate upload | the same report uploaded twice, or the same lab in two documents | timeline duplicate metrics |
| 7 | Acute episode | symptom, visit, and a short medication course with start and stop | temporal, evidence |
| 8 | Multi-document | 5 to 6 documents mixing labs, medications and visits | multi-document |

## Document types and mix (decision 10)

Five types. Every extractor runs on every document regardless of type, so a visit note can
carry a lab value and a lab report can mention a medication.

| Type | Contains | Typical events |
|---|---|---|
| Lab report | header with report date, 2 to 6 results | labs |
| Prescription | prescriber, medications with dose and frequency | medications |
| Visit note | visit details, symptoms, occasionally a medication or reading | encounter, symptoms |
| Discharge summary | admission and discharge dates, short medication course | encounter, symptoms, medications |
| Combined clinic report | labs, medications, visit and symptoms in one document | all four (hardest) |

Documents per patient, by archetype:

| Archetype | Lab | Rx | Visit | Discharge | Combined | Total |
|---|---|---|---|---|---|---|
| 1 Improving chronic | 3 | 2 | 1 | 0 | 0 | 6 |
| 2 Worsening chronic | 3 | 2 | 1 | 1 | 0 | 7 |
| 3 Stable | 3 | 1 | 0 | 0 | 0 | 4 |
| 4 Contradiction | 2 | 2 | 0 | 1 | 0 | 5 (the discharge medication list disagrees with the outpatient prescription) |
| 5 Missing information | 1 | 1 | 1 | 0 | 0 | 3 (sparse on purpose) |
| 6 Duplicate upload | 2 | 1 | 0 | 0 | 0 | 3, plus 1 re-upload = 4 |
| 7 Acute episode | 1 | 1 | 0 | 1 | 0 | 3 |
| 8 Multi-document | 2 | 1 | 1 | 1 | 2 | 7 |

Round-robin assignment gives archetypes 1 and 2 seven patients and the others six, so the
dataset has about 247 documents, of which about 25 (10%) are discharge summaries. Page
length: 85% one page, 15% two pages, with an event sometimes landing on page 2.

Lab lines normally carry their report's date; a few carry an earlier collection date.

**Hospital stays (decision 11).** A stay is **two** `encounter` events: "Hospital Admission"
on the admission date and "Hospital Discharge" on the discharge date. They have different
key fields, so each matches independently under the framework's match rule. Both carry the
same `stay_id` in their data; `stay_id` is not part of matching, and exists so questions
such as "how long was the stay?" can be written against ground truth. In the `Key: value`
layout each is its own `Encounter:` line, which the current extractor reads.

## Dates (decision 12)

All dates fall in a **fixed window, 2023-01-01 to 2026-06-30**, never relative to today:
the extraction validator rejects future dates, so a today-relative dataset would silently
change and could become invalid. Each patient's history starts at a seeded random date in the
window and must end inside it.

| Archetype | History length | Gap between reports |
|---|---|---|
| 1 Improving, 2 Worsening | 9 to 15 months | about 90 days, plus or minus 14 |
| 3 Stable | 12 to 18 months | about 150 days, plus or minus 20 |
| 4 Contradiction | 4 to 8 months | 30 to 120 days |
| 5 Missing information | 3 to 6 months | irregular |
| 6 Duplicate upload | 3 to 6 months | 60 to 120 days |
| 7 Acute episode | about 3 weeks | 2 to 9 days |
| 8 Multi-document | 8 to 14 months | 30 to 90 days |

Rules: admissions last 3 to 9 days; a prescription is dated at or before the visit it
follows; dose changes happen on a report date, not between reports; visits fall on weekdays.

## Medications, symptoms, encounters, trends (decision 13)

Fits the current medication extractor: `Rx: <name> <number><mg|mcg|g|mL|IU> <frequency> <date>`,
where the frequency ends in "daily" or "weekly" (or is "once"/"twice").

| Medication | Dose ladder (one step per change) | Frequency | Used by |
|---|---|---|---|
| Metformin | 500mg, 850mg, 1000mg | twice daily | archetypes 1, 2, 4, 8 |
| Glipizide | 2.5mg, 5mg, 10mg | once daily | 2 (second medication) |
| Insulin glargine | 10 IU, 14 IU, 20 IU | once daily | 2 |
| Lisinopril | 5mg, 10mg, 20mg | once daily | 3 |
| Atorvastatin | 10mg, 20mg, 40mg, 80mg | once daily | 5, 8 |
| Amoxicillin | 500mg | three times daily, 7 days | 7 (short course) |

Symptoms: headache, dizziness, fatigue, blurred vision, cough, fever, sore throat.
Encounter types: Annual Physical, Follow-up Visit, Specialist Consultation, Hospital
Admission, Hospital Discharge. Facilities (fictional): Riverside Clinic, Northgate Medical
Center, Lakeview Hospital.

**Trends are set by construction, never labelled with the system's own `trend_engine`.**
That function treats a change under 5% as stable, so using it to label the truth would be
the system grading itself. The generator instead plants margins on either side of that line:

| Planted trend | Rule | Example |
|---|---|---|
| Decreasing | total change of at least 15% | HbA1c 9.4 to 7.1 |
| Increasing | total change of at least 15% | HbA1c 6.8 to 8.3 |
| Stable | total change of at most 2% | systolic BP 128, 130, 129 |

No planted trend sits near the 5% boundary. **Contradictions:** the discharge summary and
the outpatient prescription give different doses on the same or a nearby date (for example
Metformin 1000mg against 850mg).

## Lab catalog

Restricted to what the current extractor can parse, so degradation, not an unsupported
format, is what makes extraction fail: HbA1c (%), glucose (mg/dL), total cholesterol
(mg/dL), LDL (mg/dL), creatinine (mg/dL), systolic blood pressure (mmHg), hemoglobin
(g/dL). Blood pressure is systolic only, because "120/80" breaks the current parser.

## Document degradation (noise profile v1)

Applied to **every** patient's documents (framework decision 2). Rates are approved
(framework decision 9) and may be tuned on the development split only.

**Per document**

| Choice | Options and share |
|---|---|
| Layout | 50% `Key: value` lines (the format the extractor expects), 25% table style, 25% prose sentences |
| Date style | ISO `2026-04-10`, `10 Jan 2026`, `January 10, 2026`. `10/01/2026` is excluded: it is ambiguous (day/month vs month/day) and would corrupt ground truth |
| Report header date | present for most documents; **absent for some patients**, so inheriting a date from the header is not always possible |

**Per event, drawn independently**

| Degradation | Rate | Ground truth keeps |
|---|---|---|
| Date omitted from the line, header date present | 10% | the header date |
| Month-only date | 5% | the month, `date_precision = month` |
| No date anywhere in the document | 3% | event exists, `date_precision = none` |
| Unit omitted | 5% | the true unit |
| Dose omitted | 5% | the true dose |
| Name variant (`HBA1C`, `Hb A1c`, `A1c`) | 10% | the canonical name; variants are in the alias table |

## Dataset layout (decision 14)

```
benchmarks/personal_health_bench/data/v1/
  manifest.json            dataset version, generator version, seed, noise-profile version,
                           counts, split definition, sha256 of every file, one root hash
  aliases.json             the name alias table (framework decision 8)
  inputs/                  WHAT A SYSTEM MAY SEE
    p001/index.json          documents in upload order, filenames only
    p001/d01.pdf             what the upload pipeline ingests
    p001/d01.txt             the same text, pages separated by a form-feed (Naive RAG indexes this)
  truth/                   WHAT ONLY THE SCORER MAY SEE
    p001.json                patient id, split, archetype, scenario tags, ground-truth
                             events, per-document metadata
  questions/               written in YGH-66
```

**`inputs/` and `truth/` are separate (14a, approved).** A system adapter is only ever given
the `inputs/` path, so a bug cannot leak ground truth into a baseline. `index.json` holds only
what a real user would supply (filenames in upload order); document type, layout and
degradations live in `truth/`.

A ground-truth event, in `truth/p001.json`:

```json
{"event_id": "p001-e001", "event_type": "lab", "event_date": "2026-01-10",
 "date_precision": "day",
 "data": {"test_name": "HbA1c", "value": 7.2, "unit": "%", "reference_range": "4.0-5.6"},
 "source": {"document_id": "p001-d01", "page": 1,
            "quote": "HbA1c: 7.2 % (ref 4.0-5.6) 2026-01-10"},
 "degradations": [], "conflicts_with": [], "duplicate_of": null}
```

IDs are deterministic: `p001`, `p001-d01`, `p001-e001`.

**The generated data is NOT committed to git (14b, declined).** `data/` is in `.gitignore`
and is regenerated on demand. Consequences the generator must therefore guarantee:

- Output is byte-identical for the same seed on any machine, so: seed one
  `random.Random` per patient from `(seed, patient_id)`; never iterate a set or rely on
  dict order that is not insertion order; write files with `newline="\n"` and UTF-8; build
  PDFs with ReportLab `invariant=1` (no timestamp, no random ID).
- A dataset change that alters output means a new version folder (`v2`), never an edit to `v1`.
- Every results file records the dataset root hash from `manifest.json`.

Risk accepted with this choice: a silent generator change would silently change the
benchmark. Mitigation to decide separately (see "Still to decide").

## What the generator outputs

Per patient: ground-truth events (type, date, `date_precision`, data); the documents, as
text and as PDF (PDF is what the upload pipeline ingests, text is what Naive RAG indexes);
for each event, the document, page and exact source quote; scenario tags (for example
`has_contradiction`, `trend=decreasing`); injected-duplicate counts; the alias table. The
question dataset (YGH-66) is written against these tags.

## Still to decide

- Time span and spacing of dates; calendar range.
- How to detect a silent generator change now that the data is not committed (one option:
  commit only the small `manifest.json`, so a regenerated dataset can be compared to the
  expected root hash).
- How the prose and table layouts are worded.
