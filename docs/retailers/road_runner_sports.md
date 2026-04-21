# Road Runner Sports

Adapter: `shoe_tracker.adapters.road_runner_sports.RoadRunnerSportsAdapter`.

## Site shape

- **Search.** `GET /search?q=<query>`. Server-rendered grid of
  `<article class="product-tile">` cards. One product per card (the mens,
  womens, and GTX versions of a shoe are separate products with separate
  URLs). The mapping engine, not the adapter, picks the right one.
- **Product page.** `GET /shoes/<slug>/<productId>`. Every variant lives in a
  JSON blob inside `<script id="__NEXT_DATA__">`. The adapter parses the JSON,
  not the rendered size/color picker — that picker is React and goes stale
  visually before any underlying data changes.
- **Variant shape.** Each colorway on the PDP carries its own list of
  size/width/price/inStock entries plus a `mfrStyleCode` that matches the
  ASICS/Nike/etc. style number. We surface all of them.

## Stock handling

- Each variant carries an explicit `inStock` boolean. Out-of-stock variants
  are included in the JSON (not hidden), so the adapter passes them through
  with `in_stock=False` and the evaluator can reason about them.
- The JSON also carries the SKU, which we use as the `colorway_code` on
  `VariantPrice`; that gives us a stable opaque key per colorway per size.

## VIP pricing

RRS members see a lower "VIP" price. We deliberately surface the **public**
price — a scraper shouldn't impersonate a member. Users can apply their VIP
discount by following the notification link to the PDP.

## Rate / etiquette

- `polite_requests_per_minute = 20` (more conservative than RW because RRS
  has a busier search endpoint and more aggressive edge cache invalidation).
- `PoliteClient` sleeps 1–2s between requests.
- `robots.txt`: RRS disallows `/account/`, `/checkout/`, and a handful of
  filter endpoints. Search and PDPs are fair game.

## Known quirks

- **One URL per product, not per colorway.** Unlike Running Warehouse, a
  single RRS URL returns every colorway's variants. That means `fetch_variants`
  yields a lot more rows than RW per call — normal, not a bug.
- **`__NEXT_DATA__` drift.** If RRS migrates away from Next.js the JSON blob
  will move. The parser returns an empty list rather than crash so the daily
  run surfaces the breakage via the adapter-health probe (chunk 8) rather than
  via a traceback in the evaluator.
- **Width encoding.** RRS uses `D` and `2E` directly, matching what we want
  on `VariantPrice.width`. No translation needed.
