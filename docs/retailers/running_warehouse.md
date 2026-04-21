# Running Warehouse

Adapter: `shoe_tracker.adapters.running_warehouse.RunningWarehouseAdapter`.

## Why this is the reference adapter

Running Warehouse ships all the variant detail we care about directly in the
server-rendered HTML — no Playwright, no hidden JSON blob. That's why chunk 2
picked it: parsing is boring, reliable, and exposes exactly the variant shape
the rest of the pipeline wants.

## Site shape

- **Search.** `GET /search-mens.html?searchtext=...` and its `-womens`
  counterpart. Each colorway is its own result card, so "Novablast 5 mens"
  returns ~10 cards — one per active colorway. The search is fuzzy enough to
  include adjacent products (e.g. "Novablast 5 GS" for the kids' shoe). The
  mapping engine filters canonically; the adapter stays honest and returns
  every card.
- **Product page.** `GET /<Brand>_<Model>/descpage-<PRODUCT_CODE>.html`. One
  page per colorway. The `<h1>` carries the model, `.desc_top-head-style`
  carries `"Men's Shoes - Gravel/White"`, and a `<p><b>Model Number:</b> ...`
  line gives the manufacturer style number (e.g. `1011B974.020`).
- **Variant table.** Each size+width is a `<tr class="js-ordering-subproduct">`
  with:
  - `data-code` like `ANB5M1105D` = product code + size code (`105` = 10.5)
    + width letter (`D`, `E` for 2E, etc.).
  - `<strong itemprop="itemOffered">` containing the human-readable name with
    `... <size> <width>` at the end.
  - `<span class="js-ordering-price">` for the live price.
  - `<span class="js-ordering-available">` for the stock count. Missing rows
    = completely sold out (the site hides them).

## Stock handling

RW usually just drops a sold-out size from the table entirely. When a row *does*
render with `In Stock: 0`, or with the `js-ordering-out-of-stock` class or a
"Notify Me" button, we mark `in_stock = False` and still surface the variant
(price + colorway) so the evaluator can reason about it. See
`product_oos_synthetic.html` for the fixture covering those cases.

## Rate / etiquette

- `polite_requests_per_minute = 30`.
- `PoliteClient` sleeps 1–2s between requests; the daily scrape makes ~dozen
  requests per RW adapter run, well under any reasonable rate limit.
- `robots.txt`: RW only disallows `/zzz/` and `/mailings/` for generic agents,
  plus crawl-delay rules for the big search bots. Our paths are fine.

## Known quirks

- **One URL per colorway.** `fetch_variants(url)` returns only that colorway's
  sizes. The chunk-3 mapping engine tracks siblings via the `product_code`
  prefix (`ANB5M1`, `ANB5M2`, …).
- **Width encoding.** `D` in the code, `D` in the display. `E` in the code
  displays as `2E`. We expose the display width on `VariantPrice.width`.
- **Size encoding.** Numeric, tenths-of-a-size: `070` = 7.0, `105` = 10.5.
