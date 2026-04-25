# Adding a retailer adapter

Reference for adding a new retailer to the scrape set. The Running Warehouse
adapter (`src/shoe_tracker/adapters/running_warehouse.py`) is the canonical
example — start by reading it.

## Decide whether the retailer is worth adapting

Skip and document in `docs/retailers/skipped.md` if:

- The site requires Playwright/Selenium (we don't ship those).
- The site is Cloudflare-fronted to the point that 429/403 backoff doesn't
  recover it.
- The site needs an account, paid subscription, or anything else that's
  fundamentally not free.
- The site's robots.txt forbids the URLs we'd need to hit.

## Implement `RetailerAdapter`

Subclass `RetailerAdapter` from `src/shoe_tracker/adapters/base.py`.
You're responsible for:

- `name` — short, snake_case, used as the dictionary key in `ADAPTERS`
  and as the retailer column in price snapshots. Stable forever — don't
  rename it.
- `search(canonical: CanonicalShoe) -> list[SearchResult]` — return one
  candidate per matching listing. Multiple colorways of one shoe become
  multiple results.
- `fetch_variants(product_url: str) -> list[VariantPrice]` — return every
  size+colorway+width combination on the product page, **including
  out-of-stock variants** the page lists explicitly. Sizes the retailer
  hides entirely when sold out are, by definition, not returned.
- `polite_requests_per_minute` — keep it modest (default 30). The
  PoliteClient sleeps 1–2 s between requests already; this is just a
  declarative ceiling.

Use the shared `PoliteClient` from `adapters/http.py`. Don't introduce a
parallel HTTP wrapper — backoff and the realistic User-Agent already live
there. Inject a fake client in tests via the constructor.

## Parse JSON over HTML where possible

Most modern retailer pages embed a structured JSON blob (`__INITIAL_STATE__`,
`ld+json`, a `data-*` attribute) that drives the size/color picker. Find
that and parse it instead of the rendered HTML — it's much more stable
across cosmetic redesigns.

## Tests

1. Save a real product page and a real search-results page into
   `tests/fixtures/<retailer>/` (commit them — they're stable test
   inputs).
2. Add `tests/test_<retailer>.py` covering:
   - The search parser, against the saved search HTML.
   - The variant parser, against the saved product HTML.
   - At least one out-of-stock case (find a product with sold-out sizes).
   - At least one multi-colorway case if the retailer ships colorways
     under separate URLs.
3. Add one `@pytest.mark.live` test (env-gated on `SHOE_TRACKER_LIVE=1`)
   that hits the real retailer for one search + one variant fetch. The
   weekly Tests workflow runs these.

## Wire into `ADAPTERS`

`src/shoe_tracker/adapters/__init__.py` keeps the registry. Add an entry
in the dict and re-export the class. Be deliberate about `name` — once
it's in price snapshots, renaming requires a migration.

## Document the retailer's quirks

Drop a short note at `docs/retailers/<name>.md` covering:

- Where the variant data lives (JSON blob? HTML table?).
- VIP / member-only pricing if it shows up unauthenticated.
- Any sections we deliberately don't scrape (clearance-only pages, etc.).
- Any 403 / Cloudflare quirks that needed special handling.

## Verify the full pipeline

After the unit tests are green:

```bash
python -m shoe_tracker init-db
python -m shoe_tracker rotation map --retailer <name>
python -m shoe_tracker rotation status
python scripts/adapter_health.py
```

The probe should report your new adapter as PASS. If it does, the next
daily scrape will start collecting data and the dashboard will surface it
the day after.
