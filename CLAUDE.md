# Conventions for this repo

## TDD
- Write a failing test first. Make it pass. Refactor.
- One commit bundles the test + implementation + any doc updates for one slice of behavior.
- Tests live in `tests/`, mirror the module layout under `src/shoe_tracker/`.

## Commit discipline
- Small, focused commits. A commit should describe one concept.
- Commit message style: imperative subject, optional body explaining *why*.
- Never commit secrets. Gmail app passwords and DB connection strings live in env vars / GitHub secrets.

## Data access
- **All SQL goes through repository classes in `db.py`.** No raw SQL in CLI, evaluator, notifiers, or renderers.
- This is the contract that makes the v2 Postgres migration a driver swap.

## Scraping etiquette
- Realistic User-Agent, 1–2s jitter between requests, respect robots.txt.
- If a retailer requires Playwright/Selenium or hits Cloudflare, skip it — document in `docs/retailers/skipped.md`.

## What not to build
- No automated purchasing.
- No Playwright/Selenium.
- No paid services.
- No features beyond what `plan.md` calls for in the current chunk.
