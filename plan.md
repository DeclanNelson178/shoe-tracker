# Shoe Price Tracker — Build Plan

## North star

Alert me when a shoe in my current rotation, in my size and an acceptable colorway, drops below a configurable threshold at a retailer that actually stocks running shoes. Zero-cost to run.

---

# V1 — Personal use

## Scope

- Single user (me)
- I manually edit a yaml config to add shoes
- Full pipeline runs as a GitHub Action
- Email notifications (Gmail SMTP with app password, free)
- Static GitHub Pages dashboard (read-only) for glanceable status
- Zero recurring cost

## Non-goals for v1

- Other users / accounts / auth
- Interactive web UI
- Automated purchasing
- Paid services of any kind

## Architecture

```
config/rotation.yaml     ← I edit this to add shoes
         │
         ▼
    mapping engine       ← resolves canonical shoe → per-retailer product URL
         │
         ▼
   retailer adapters     ← returns variant-level data: {size, colorway, width, price, in_stock}
         │
         ▼
      sqlite DB          ← variants, snapshots, notifications_sent, mappings
         │
         ├──→  evaluator  →  email notifier
         └──→  dashboard renderer  →  docs/index.html (GitHub Pages)

All orchestrated by GitHub Actions cron. Sqlite committed back to repo after each run.
```

## Tech constraints

- Python 3.11+
- Deps: `httpx`, `selectolax`, `pydantic`, `pyyaml`, `pytest`, `jinja2`
- No Playwright/Selenium — if a retailer needs them, skip it
- Storage: sqlite file in repo
- Scheduler: GitHub Actions cron
- Hosting: GitHub Pages for the read-only dashboard
- Notifications: Gmail SMTP app password
- Conventions: TDD, one commit per test+impl+docs bundle

## Data model (important — designed to carry into v2 unchanged)

```
users                 -- v1 has one row: id="me"
  id, email, created_at

canonical_shoes       -- the shoe as a model, regardless of variant
  id, brand, model, version, gender, variant_type (null | "GTX" | "Wide" | "Trail")
  mfr_style_prefix    -- e.g. "1011B867" for Novablast 5 men's, if known

shoe_variants         -- specific size + colorway + width
  id, canonical_shoe_id, size, width, colorway_name, colorway_code (nullable),
  mfr_style_code (nullable, more specific than prefix), image_url

watchlist
  id, user_id, canonical_shoe_id, size, width,
  colorway_policy ("any" | "allowlist" | "denylist"),
  colorway_list (json array, used when policy is allowlist or denylist),
  threshold_usd, active, created_at

retailer_mappings     -- resolved product URL per retailer per canonical shoe
  canonical_shoe_id, retailer, product_url, product_id,
  confidence, last_verified_at

price_snapshots       -- every variant's price at a retailer, at a point in time
  id, shoe_variant_id, retailer, price_usd, in_stock,
  scraped_at, source_url

notifications_sent    -- dedup
  id, user_id, shoe_variant_id, retailer,
  triggering_price, sent_at, channel
```

Key design call: `shoe_variants` is keyed by the actual size+colorway+width combination. Price snapshots point to variants, not canonical shoes. This is what lets us answer "Novablast 5 in size 10.5, Black/Mint, under $100" precisely.

## Colorway policy

Three modes per watchlist entry:

- **`any`** (default) — match any colorway. Notification includes colorway name + image. I decide in the moment.
- **`allowlist`** — only notify for colorways I've listed.
- **`denylist`** — notify for all except listed colorways.

Start with `any`. Add `denylist` entries when a particular ugly colorway keeps triggering noise alerts. Don't over-engineer.

## Concrete work chunks

Each chunk is sized for a single Claude Code subagent session. Each ends with a real-data checkpoint — a concrete command that proves real data flows, not just that tests pass.

---

### Chunk 1: Repo scaffolding + data model ✅

**Status:** Complete. `python -m shoe_tracker init-db` + `python -m shoe_tracker rotation list` produces the expected checkpoint output.

**Goal:** Repo exists, schema is defined, config loads cleanly.

**Work:**
- Initialize repo with `CLAUDE.md` (TDD + commit discipline conventions), `README.md`, `.gitignore`, `pyproject.toml` with pinned deps
- Pydantic models matching the schema above. `CanonicalShoe`, `ShoeVariant`, `WatchlistEntry`, `RetailerMapping`, `PriceSnapshot`, `NotificationRecord`.
- `rotation.yaml` schema with 1 hand-filled entry (Novablast 5, M 10.5 D, `any` colorway, threshold $100)
- `config.py` loader with pydantic validation + tests
- sqlite migrations in `db/migrations/` (001_initial.sql). Schema matches the data model above, `user_id` column present on watchlist, default `"me"`.
- `db.py` with repository pattern: `WatchlistRepo`, `ShoeRepo`, `PriceSnapshotRepo`, etc. No raw SQL outside repo classes — this is what makes the v2 Postgres migration a driver swap.
- `cli.py` with Click or argparse, stubs for commands used in later chunks

**Checkpoint:**
```bash
python -m shoe_tracker init-db
python -m shoe_tracker rotation list
# ASICS Novablast 5 (M 10.5 D) — any colorway — threshold $100 — unmapped
```

---

### Chunk 2: Running Warehouse adapter + adapter interface ✅

**Status:** Complete. `python -m shoe_tracker probe running_warehouse --canonical "ASICS Novablast 5" --gender mens --size-min 10 --size-max 11 --width D` returns real per-colorway pricing from RW (≈40 D-width variants across ~15 colorways).

**Goal:** One retailer working end-to-end with variant-level data, adapter interface locked in.

**Why RW first:** Clean HTML, exposes manufacturer style codes including colorway, reliable size/color tables, scrape-friendly.

**Work:**
- `RetailerAdapter` abstract base class:
  - `search(canonical: CanonicalShoe) -> list[SearchResult]` — candidates for mapping
  - `fetch_variants(product_url: str) -> list[VariantPrice]` — **returns every in-stock and out-of-stock variant** with size, colorway name, colorway code if available, width, price, in_stock flag
  - Declares: name, style-code support, polite rate (req/min), whether it requires JS rendering
- Implement `RunningWarehouseAdapter`
  - Find the JSON blob their product page uses to populate the size/color picker (typically `window.__INITIAL_STATE__` or a `ld+json` block) — parse that instead of scraping the rendered HTML
  - Map the JSON shape to `VariantPrice` records
- Polite scraping: realistic User-Agent, 1–2s jitter between requests, respect robots.txt
- Tests: save real responses to `tests/fixtures/running_warehouse/` (one full product page, one search page, one out-of-stock product, one multi-colorway product). Unit tests parse fixtures offline.
- One `@pytest.mark.live` integration test, env-gated, covering one real search + one real variant fetch

**Checkpoint:**
```bash
python -m shoe_tracker probe running_warehouse \
  --canonical "ASICS Novablast 5" --gender mens
# Found product: <url>
# Variants (showing size 10–11, D width):
#   10.0 / Black Mint     $94.95   in stock
#   10.0 / Safety Yellow  $74.95   in stock
#   10.5 / Black Mint     $94.95   in stock
#   10.5 / Safety Yellow  $74.95   out of stock
#   ...
```
Real data from real RW with real colorway-specific pricing. This is the moment the variant model proves itself.

---

### Chunk 3: Mapping engine with confidence tiers

**Goal:** Given a canonical shoe, auto-resolve per-retailer product URLs with confidence scoring.

**Work:**
- `mapping.py` scoring function: `CanonicalShoe` + `SearchResult` → float in [0, 1]
- Hard rejects: brand mismatch, gender mismatch, version number mismatch, variant_type mismatch (GTX vs non-GTX vs Wide vs Trail)
- Style code prefix match → auto 0.99
- Token overlap with distinctive-token weighting (brand/model/version carry more than "men's"/"running")
- Tiers: ≥0.9 auto-map, 0.6–0.9 auto-map + flag in `mapping_review.md`, <0.6 unmapped (reported)
- `rotation map [--retailer X | --all]` CLI command
- Tests covering the nasty collisions: Speed 4 vs Speed 5, Vomero 18 vs Vomero 18 GTX, men's vs women's, D vs 2E width, Speedgoat 6 vs Speedgoat 6 GTX

**Checkpoint:**
```bash
python -m shoe_tracker rotation map --retailer running_warehouse
# Novablast 5 (M) → mapped (0.97) → <url>
python -m shoe_tracker rotation status
# Novablast 5 (M 10.5 D, any)  threshold $100  current min $94.95 Black Mint @ RW
```
First moment the system does the core thing end-to-end.

---

### Chunk 4: Additional retailer adapters (parallelizable)

**Goal:** 4+ retailers so "min price across retailers" is meaningful.

Priority order:
1. Road Runner Sports
2. Holabird Sports
3. Zappos
4. Jackrabbit

Each adapter follows the same shape as RW (Chunk 2). Can be farmed out to parallel subagents once Chunks 2 and 3 are merged. Each subagent gets the RW adapter as a reference.

**Per-adapter deliverables:**
- Adapter class conforming to `RetailerAdapter`
- Variant-level data extraction (size + colorway + width + price + stock)
- Fixture-based unit tests
- Live integration test (env-gated)
- `docs/retailers/<n>.md` documenting quirks (RRS VIP pricing, Zappos colorway naming, Holabird clearance sections, etc.)

**Skip rules:** If an adapter requires Playwright or hits Cloudflare, document it in `docs/retailers/skipped.md` and move on. Don't block on any single retailer.

**Checkpoint:**
```bash
python -m shoe_tracker rotation map --all
python -m shoe_tracker rotation status
# Novablast 5 (M 10.5 D, any)  threshold $100
#   current min: $89.00 Black Mint @ Holabird
#   all retailers: RW $94.95, RRS $99.00, Holabird $89.00, Zappos $110.00
```

---

### Chunk 5: Evaluator + email notification

**Goal:** System detects threshold crossings with correct colorway filtering and sends an email I actually receive.

**Work:**
- `evaluator.py`: for each active watchlist entry, find matching in-stock variants (size + width + colorway policy applied), determine min price, compare to threshold, return `TriggeredAlert` list
- Colorway policy implementation: `any` matches all, `allowlist` matches listed colorway names (case-insensitive substring match on both name and code), `denylist` excludes listed
- Dedup: don't re-notify same `(watchlist_entry, retailer, variant)` within 7 days. Stored in `notifications_sent`.
- `notifiers/email.py`: Gmail SMTP, HTML email body with shoe name, variant (size/colorway), price, retailer, direct link, image thumbnail, delta from threshold. Notifier interface should be abstract (`Notifier.notify(user, alert) -> bool`) so v2 can add other channels cleanly.
- CLI: `rotation evaluate` runs full pipeline locally for manual testing
- Tests: dedup boundary, multi-retailer min selection, colorway policy combinations, out-of-stock exclusion, width filter

**Checkpoint:**
```bash
# Temporarily raise threshold on Novablast 5 to $999 to force a trigger
python -m shoe_tracker rotation set-threshold "Novablast 5" 999
python -m shoe_tracker rotation evaluate
# Email arrives in my Gmail within 2 min, shows variant + image + link
# Run again immediately → no duplicate (dedup working)
```

---

### Chunk 6: GitHub Actions automation

**Goal:** Whole pipeline runs autonomously on schedule, forever, free.

**Work:**
- `.github/workflows/scrape.yml`: daily cron at 14:00 UTC. Runs: fetch variants → snapshot → evaluate → notify → render dashboard → commit sqlite + dashboard back
- Secrets: `GMAIL_APP_PASSWORD`, `GMAIL_FROM`, `NOTIFY_EMAIL`
- Concurrency guard (only one scrape run at a time)
- Workflow failure alert: if the workflow itself crashes, send an email to me. Silent failure is the enemy.
- `.github/workflows/test.yml`: unit tests on PR, live integration tests on weekly cron (catches adapters that silently broke due to site changes)
- Workflow health badge in README

**Checkpoint:**
- Manually trigger workflow via GitHub UI → completes <5 min, commits updated sqlite, dashboard reflects new data
- Wait 24h → scheduled run fires on its own
- Deliberately break one adapter → failure email arrives

---

### Chunk 7: Static dashboard (read-only)

**Goal:** Glanceable status at a URL, regenerated each run.

**Work:**
- `scripts/render_dashboard.py`: reads sqlite, writes `docs/index.html` and `docs/data.json`
- Jinja2 template, no frontend framework, plain CSS (or Tailwind via CDN), zero build step
- Layout: table of watchlist entries showing current min price + variant, threshold, delta, last scrape timestamp, expandable per-retailer/per-variant breakdown with thumbnails
- Visual state: green below threshold, yellow within 10%, red far above
- Prominent staleness warning if last scrape >36h old
- Alert history section (last 30 days of notifications_sent)
- Mobile-friendly (I'll check it from my phone)
- GitHub Pages configured to serve from `docs/` on main branch

**Checkpoint:**
- `https://<me>.github.io/<repo>/` shows current rotation with real prices and colorway thumbnails
- Refresh after next cron run → timestamps and prices update

---

### Chunk 8: Operational hardening

**Goal:** Runs unattended for months without babysitting.

**Work:**
- `docs/runbook.md`: how to add a shoe, debug a broken adapter, rotate Gmail app password, read sqlite locally, re-run failed workflow, interpret the dashboard
- `scripts/adapter_health.py`: weekly Action that probes every adapter with a known-good canonical shoe; alerts if any adapter returns empty/anomalous results (silent breakage detection)
- Rate limit backoff: on 429/403, back off exponentially and skip that retailer for the run (don't hammer, don't fail whole pipeline)
- sqlite size guard: prune `price_snapshots` older than 90 days (committed DB can't grow unbounded)
- `docs/adding_retailers.md`: reference guide for adding new adapters, pointing at the RW adapter as canonical example

**Checkpoint:**
- Manual adapter health run → all green
- Point one adapter's base URL at a 404 → health probe fails loudly within a week
- Simulate 429 in fixtures → pipeline completes with retailer skipped, no crash

---

## V1 acceptance criteria

All true before calling v1 done:

- [ ] My full current rotation is in `rotation.yaml` with thresholds + colorway policies set
- [ ] Every shoe auto-maps to ≥3 retailers at confidence ≥0.9
- [ ] A full scrape returns variant-level prices (size + colorway + width) from every adapter
- [ ] GitHub Actions cron runs daily without my involvement
- [ ] Setting threshold above current min triggers an email within ~30 min of next scheduled run
- [ ] Dedup prevents repeat notifications within 7 days
- [ ] Colorway policy filtering works (verified by flipping allowlist/denylist on a real shoe)
- [ ] Dashboard at GitHub Pages URL shows current state with thumbnails, refreshes daily
- [ ] Adapter health probe catches a deliberately broken adapter within a week
- [ ] Adding a new shoe is: edit yaml, run `rotation map`, commit
- [ ] Total recurring cost: $0

## Subagent farming notes

- Chunks 1, 2, 3, 5, 6, 7, 8 are sequential
- Chunk 4 is parallelizable per adapter once Chunks 2 and 3 are merged
- Every chunk ends with a concrete real-data checkpoint — don't merge a chunk that can't pass its checkpoint
- Subagents that can't make their checkpoint work should stop and report, not mock around the problem
- TDD discipline: failing test → passing test → single commit bundling test + implementation + docs

---

# V2 — Multi-user hosted product

## Scope change

- Anyone can sign up with email + password (or OAuth)
- Users manage their own watchlists through a web UI (add/edit/remove shoes, set thresholds, manage colorway policies)
- Users receive notifications at their own email, optionally other channels
- Admin surface for mapping review and user support
- Hosted somewhere real with a proper DB

## What carries over from v1 unchanged

- Data model (we designed `user_id` in from the start)
- Canonical shoe + variant abstraction
- Retailer adapters (scraping logic is identical)
- Mapping engine + confidence scoring
- Evaluator + colorway policy logic
- GitHub Actions scraping cron (still the cheapest way to run the scrape — the workload is small and batched)

## What changes

**Storage migration.** Sqlite committed-to-repo → managed Postgres (Neon or Supabase free tiers are generous enough for early v2). Because all DB access in v1 goes through the repository pattern, this is a driver swap plus migration tooling (Alembic).

**Scraper decoupling.** The GitHub Actions scraper no longer commits sqlite — it writes price snapshots directly to the shared Postgres. The web app reads from the same DB. Clean separation: scraper is a batch job, web app is an online service, they share storage.

**Web application.** FastAPI backend + a lightweight frontend (Next.js, or server-rendered Jinja + HTMX — choose based on what's fun to build vs fast to ship).
- Auth: something boring and well-trodden. Clerk or Supabase Auth for hosted; Authlib + OAuth for roll-your-own.
- Endpoints: CRUD on watchlist, read-only on price snapshots, read-only on alert history
- Pages: dashboard (personalized view of my watchlist), add/edit shoe flow, notification settings

**Hosting.**
- Web app: Fly.io (free allowance covers a small always-on app) or Cloudflare Workers + Pages. Cloudflare is the cleanest fit if we choose HTMX or server-rendered.
- Database: Neon Postgres free tier
- Scraper: still GitHub Actions, writes via a service-role DB connection string

**Notifications.** Per-user channels. Email is universal; Pushover / ntfy.sh as optional user-configured add-ons. SMS deferred (A2P 10DLC pain still applies).

**Shared scrape set optimization.** The scraper now fetches the union of all users' watchlist entries. Already handled by v1's design (scraper keys off variants, not watchlist entries), but this is where the multi-tenant efficiency pays off — 100 users tracking Novablast 5 = one scrape.

**Mapping review UI.** Flagged mappings (confidence 0.6–0.9) show up in an admin page. I (or trusted users) can approve/reject. The v1 `mapping_review.md` file becomes a DB table and a small UI.

**User-reported issues.** The "report a duplicate / wrong shoe" flow you mentioned — a button on the dashboard's shoe card that files a `MappingIssue` record. Admin reviews.

**Abuse controls.** Rate limits on watchlist size per user (otherwise one user can balloon the scrape set), rate limits on the API.

## V2 design decisions I want to lock in during v1

These are the things where getting v1 wrong creates real v2 pain:

1. **Repository pattern for all DB access in v1.** Makes the Postgres migration mechanical.
2. **`user_id` column on watchlist from day one.** Zero-cost in v1, no schema migration pain in v2.
3. **Scraper keys off `shoe_variants`, never off watchlist entries directly.** Makes union-across-users work trivially.
4. **Notifier abstraction (not Gmail-hardcoded).** v1 ships with one `EmailNotifier` implementation; v2 adds more. Interface should be `notify(user, alert) -> bool`.
5. **Variant-level pricing data from day one.** Can't retrofit colorway/size-specific data later without re-scraping months of history.
6. **Mapping review surface is data, not markdown.** v1 writes `mapping_review.md` by rendering from a `mapping_issues` table. v2 just adds a UI over the same table.

## V2 deferred further

- SMS (A2P 10DLC)
- Mobile app (PWA is probably enough)
- Browser extension (add shoe from retailer page directly)
- Coros/Runna integration (mileage-aware auto-thresholds for rotation replacement — the genuinely unique feature, save for v3)
- Automated purchasing (still a bad idea)
- Paid tiers (worry about monetization only if there are users)
