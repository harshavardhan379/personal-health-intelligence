# Contributing

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd apps/web && npm install && cd ../..
```

## Running locally

```bash
docker compose up        # postgres + backend + frontend
# or, for backend-only dev against the compose postgres (run from the repo root):
uvicorn services.api.main:app --reload
alembic upgrade head
python database/seeds/seed.py
```

Copy `services/api/.env.example` to `services/api/.env` and fill in real values before running outside Docker.

## Code quality

```bash
ruff check .              # Python lint
cd apps/web && npm run lint   # TypeScript lint
pytest                    # unit tests (pure logic; DB-backed tests need `docker compose up` first)
```

## Pre-commit

Install hooks once:

```bash
pip install pre-commit
pre-commit install
```

## Rules

- Never commit `.env`, secrets, or real patient data. Synthetic data only.
- Any change touching `services/reasoning` or `services/safety` must pass the safety regression suite once it exists (M13+).
- See [docs/BRANCHING.md](docs/BRANCHING.md) for branch and commit conventions.
