# Branching Strategy

Trunk-based with short-lived feature branches.

## Branches

- `main`: always deployable; protected. Changes land only through pull requests.
- Feature branches: one per Linear issue, named with Linear's generated branch name, e.g. `srinivasharshavardhan130/ygh-1-initialize-repository-structure-and-documentation`.

## Workflow

1. Branch from latest `main` for each Linear issue.
2. Keep branches small and short-lived (ideally under 2 days).
3. Open a PR referencing the issue ID (`YGH-<n>`); Linear moves it to In Review.
4. CI must pass (lint, typecheck, tests, safety suite once available) before merge.
5. Squash-merge into `main`; delete the branch.

## Commit messages

Imperative subject, issue ID in the body or prefix: `YGH-1: initialize repository structure`.

## Rules

- Never commit secrets, `.env` files, or real patient data. Synthetic data only.
- Any AI-related change must run the safety regression suite (from M13 onward).
- Release tags on `main`: `v0.<milestone>.<patch>`.
