# Environments and deployment (YGH-103, YGH-104, YGH-106)

> **Status, stated plainly:** the production images, the production compose file and the
> environment separation are built and were verified by running the production stack locally on
> synthetic data (section 5). **The system has not been deployed to a cloud or any public host.**
> That needs an account, a domain, TLS and spend that only the owner can provide; YGH-106 stays open
> until it is done. Section 6 is the checklist for it.

## 1. Three environments, one set of names

| | development | test | production |
|---|---|---|---|
| `ENVIRONMENT` | `development` (default) | `test` | `production` |
| Run with | `docker-compose.yml` (just Postgres) + `uvicorn` + `next dev` | `pytest` | `docker-compose.prod.yml` |
| Database | `phi` on `localhost:5433` | `phi_integration_test`, rebuilt from the migrations every run (name must end `_test`) | its own Postgres volume, password from the environment |
| Secrets | development defaults, in `.env.example` | none needed | **none defaulted**; every one must be provided |
| API docs (`/docs`) | on | on | **off** |
| Unsafe settings | allowed | allowed | **the API refuses to start** |
| Model | local Ollama | scripted | local Ollama or a hosted provider (read `docs/08_PRIVACY.md` first) |

Templates: `.env.example` (development), `.env.test.example`, `.env.production.example`. The real
`.env` and `.env.production` are git-ignored, and a test fails if one is ever tracked.

In production the API refuses to start (naming the variable, never the value) if: `JWT_SECRET` is
the development default or under 32 characters; the database URL contains the development password;
`CORS_ORIGINS` is empty or `*`; `STORAGE_DIR` is not an absolute path; a hosted `LLM_PROVIDER` has no
key; or `ENVIRONMENT` is not a known name. The production image defaults to `ENVIRONMENT=production`,
so the safe behaviour is what you get when you configure nothing.

## 2. The images

**API** (`services/api/Dockerfile`, build from the repository root): Python 3.12, **tesseract and
poppler installed** (the earlier image had neither, so scanned pages silently produced no text),
runtime dependencies only, an unprivileged user (uid 10001), `HEALTHCHECK` on `/api/v1/health`, and
one `uvicorn` worker with the access log off (`--no-access-log`: it prints URLs with query strings).
626 MB, mostly OCR.

**Web** (`apps/web/Dockerfile`): three stages (install, build, runtime), the Next.js standalone
server, run as the `node` user, 387 MB. `NEXT_PUBLIC_API_URL` is inlined into the browser bundle at
**build** time, so it is a build argument, and the Content-Security-Policy allows the page to call
exactly that origin.

**One worker, on purpose.** The login and assistant rate limits, the metrics, and the recovery of
interrupted documents are per process. Scale out only after moving the limiter to a shared store
(`docs/OBSERVABILITY.md`, `services/api/app/core/ratelimit.py`).

## 3. The production stack

```bash
cp .env.production.example .env.production      # fill in EVERY value; never commit it
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

`docker-compose.prod.yml` runs Postgres (pgvector, **no published port**), a one-shot `migrate`
service (`alembic upgrade head`, which the API waits for), the API and the web app. Every secret is
`${VAR:?message}`: leaving one out stops compose with the variable's name. Hardening: both app
containers run with `no-new-privileges`, all capabilities dropped and a **read-only root
filesystem** (only the storage volume and `/tmp` are writable). Only the API and web ports are
published; the model is **not** in the file (point `OLLAMA_BASE_URL` at one you run, or choose a
hosted provider).

Behind a reverse proxy: terminate TLS there. The API sends `Strict-Transport-Security` in
production, which is only honest behind HTTPS. For client addresses (used by the login limiter) run
uvicorn with `--forwarded-allow-ips` set to the proxy (`FORWARDED_ALLOW_IPS`); otherwise every
client looks like the proxy.

## 4. Web security headers

`next.config.ts` sets, on every page: `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, a `Permissions-Policy`, and **in production** a
Content-Security-Policy (`default-src 'self'`; `connect-src` the API origin only; `object-src
'none'`; `frame-ancestors 'none'`; `frame-src blob:` for the original document) and HSTS. Known gap:
`script-src` needs `'unsafe-inline'` for Next.js's own bootstrap scripts; nonces are the stricter
option (`docs/09_SECURITY_REVIEW.md`).

## 5. What was verified, on the production images

The stack was built and run on this machine (`phi-prod`, ports 18000 and 13000, generated secrets
kept outside the repository), exercised, and removed with its volumes:

- Postgres healthy; migration exited 0; both app containers healthy.
- Security headers present, including HSTS; `/docs`, `/redoc`, `/openapi.json` return 404.
- API runs as uid 10001; the root filesystem is read-only (a write to `/app` is refused); the storage
  volume is writable; `tesseract` and `pdftoppm` are present; all capabilities dropped.
- The whole product on synthetic data, through the containers: register; upload a text PDF, **a
  scanned PNG and an image-only PDF (OCR produced facts from both)**; a fake PDF refused (422);
  confirm facts; the timeline; a real assistant answer from the host's Ollama (supported by its
  evidence); metrics; account deletion; the login throttle (429 on the sixth wrong password).
- In a browser, against the production web build: the page renders under the CSP with no console
  errors, the original document shows from a `blob:` URL, deleting a document, a wrong password on
  account deletion (refused, message shown), and a correct one (account deleted, token cleared,
  redirected to login).
- Afterwards every table was empty and the user's storage folder was gone.
- The container's logs: JSON only, **0** access-log lines, **0** planted sensitive values.
- The API image started with safe settings and refused to start with the development ones.

## 6. Still needed to actually deploy (YGH-106)

- A host (a VM or container service), a **domain** and **TLS** in front of both services.
- Real secrets in `.env.production`, held in the host's secret store (not on disk in the repo).
- A model: a machine with enough memory for Ollama and the chosen model, **or** a hosted provider,
  after reading `docs/08_PRIVACY.md` section 4 and arranging a data-processing agreement.
- Backups of the Postgres and storage volumes that honour deletion requests; a restore drill.
- Log shipping and retention, and an alert on the API's health check.
- A first run of the CI workflow on GitHub, and branch protection that requires it.
- The list in `docs/09_SECURITY_REVIEW.md` section 5 before any real patient data.

Cost control for a demo: nothing in this stack runs a GPU by itself; the only continuous costs are
the host and, if used, a hosted model per question. Stop the stack with
`docker compose -f docker-compose.prod.yml down` (add `-v` to delete the data).
