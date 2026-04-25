# Shoe Price Tracker — plan

## North star

Alert me when a shoe in my current rotation, in my size and an acceptable
colorway, drops below a configurable threshold at a retailer that actually
stocks running shoes. Zero-cost to run.

## Status: v1 live

Daily scrape commits results back to the repo; static dashboard regenerates
on every run.

**Working:**
- Single user, manually-edited `config/rotation.yaml`
- Mapping engine with confidence tiers + flagged review at `docs/mapping_review.md`
- Variant-level scrape (size + colorway + width + price + stock)
- Threshold evaluation with colorway policy + 7-day notification dedup
- Gmail SMTP email alerts via app password
- Static dashboard at `https://declannelson178.github.io/shoe-tracker/`
- Daily cron 14:00 UTC + manual dispatch
- Weekly adapter-health probe Monday 05:00 UTC
- Rate-limit backoff (2s/4s/8s on 429/403); broken retailer is skipped, not fatal
- 90-day snapshot pruning keeps the committed sqlite bounded
- Failure email on any workflow crash
- Lint (ruff), CodeQL, Dependabot — see `.github/workflows/`
- Ops docs at `docs/runbook.md` and `docs/adding_retailers.md`

## Next up

### Fix the chunk-4 retailer adapters

The adapter-health probe (chunk 8) caught this on first dispatch: three of
four chunk-4 adapters shipped against fabricated fixtures and 404 against
the real sites. Pick one of these for each:

- **Holabird Sports** — keep + fix. Site is Shopify, not Magento. Real
  search: `/search?q=...`. Real product URLs: `/products/<slug>`. Rewrite
  parser against fresh fixtures pulled from the live site.
- **Road Runner Sports** — skip. `/search?q=...` returns 404; Cloudflare
  503's basic probes. Document in `docs/retailers/skipped.md`, remove from
  `ADAPTERS`. Revisit if the Fleet Feet integration ever surfaces a usable
  search URL.
- **JackRabbit** — skip. Same story (now part of Fleet Feet, Cloudflare-
  fronted, no obvious search URL). Document and remove.

Target after: 2 healthy retailers (Running Warehouse + Holabird), health
probe green, "min across retailers" is meaningful again.

### Grow the rotation

Currently one entry (ASICS Novablast 5, M 10.5 D, threshold $100). Add more
by editing `config/rotation.yaml`, running `rotation map --all` locally to
sanity-check confidences, then committing. Daily scrape picks them up.

### Recalibrate v1 acceptance

The original "every shoe auto-maps to ≥3 retailers at confidence ≥0.9"
won't hold with two retailers. Either tighten the rotation to shoes both
RW and Holabird carry, or relax the criterion to ≥1. Probably the latter.

## Data model

Source of truth: `db/migrations/001_initial.sql`. Shape:

```
users                 — v1 has one row: id="me"
canonical_shoes       — brand + model + version + gender + variant_type
shoe_variants         — size + width + colorway (+ code) per canonical_shoe
watchlist             — user × shoe × size × width × threshold + colorway policy
retailer_mappings     — canonical_shoe × retailer → product_url, confidence
price_snapshots       — variant × retailer × time → price + in_stock
notifications_sent    — dedup
```

`user_id` is on `watchlist` from day one. All SQL goes through repository
classes in `src/shoe_tracker/db/__init__.py` — no raw SQL anywhere else.

## Conventions

- **TDD.** Failing test → passing test → one commit per slice (test + impl + docs).
- **Repository pattern.** All SQL in `db.py`. Period.
- **Polite scraping.** Realistic User-Agent, 1–2 s jitter, 429/403 backoff.
  Cloudflare-locked retailers go in `docs/retailers/skipped.md`, not in
  `ADAPTERS`.
- **No** Playwright/Selenium, no paid services, no automated purchasing.

## Out of scope

Multi-user hosted product, web UI, OAuth, browser extension, Coros/Runna
integration, automated purchasing, paid tiers. The data model is designed
to carry into a v2 unchanged (`user_id` exists, repos abstract the DB,
variant-level data exists from day one) — but a v2 only happens if v1
generates real personal usage that justifies the lift.
