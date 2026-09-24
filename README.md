# Personal Health Intelligence System

A longitudinal AI platform that transforms fragmented health records into structured health events, visual timelines, trend analysis, and evidence-grounded explanations with provenance, uncertainty handling, verification, and safety controls.

> **Product boundary:** This is an information-management and explanation prototype. It is **not** a diagnostic, prescribing, treatment, emergency, or clinical-decision system. Use synthetic data only for development and public demos.

## Status

Early development (MVP). Work is tracked in Linear (team `YGH`, project "Personal Health Intelligence App"). The full execution plan is in [docs/00_MVP_EXECUTION_PLAN.md](docs/00_MVP_EXECUTION_PLAN.md).

## Core pipeline

```text
Documents → Document Processing → Structured Extraction → Human Review
→ Health Events → Longitudinal Timeline → Hybrid Retrieval → Evidence Selection
→ AI Synthesis → Evidence Verification → Safety Layer → User
```

Principle: raw documents are evidence; confirmed structured health events are the source of truth.

## Planned stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI, Pydantic |
| Database | PostgreSQL, pgvector |
| Storage | Local filesystem (dev), S3-compatible (deploy) |
| Infra | Docker, Docker Compose, CI/CD |

## Repository layout

```text
apps/web/                 Next.js frontend
services/api/             FastAPI backend
services/document_processing/   PDF parsing, OCR, classification
services/extraction/      Structured health-event extraction
services/retrieval/       Structured + semantic + hybrid retrieval
services/reasoning/       AI assistant / answer generation
services/verification/    Claim and evidence verification
services/safety/          Safety classifier and response policy
packages/schemas/         Shared schemas
packages/evaluation/      Evaluation framework
packages/observability/   Logging and tracing helpers
database/                 Migrations and seed data
tests/                    unit, integration, evaluation, safety
benchmarks/personal_health_bench/   Synthetic benchmark
experiments/              Scratch work, not shipped
docs/                     Specs and reports
```

## Contributing

Branching and commit conventions are in [docs/BRANCHING.md](docs/BRANCHING.md).

## License

MIT, see [LICENSE](LICENSE).
