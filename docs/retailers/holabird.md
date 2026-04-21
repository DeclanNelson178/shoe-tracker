# Holabird Sports

Adapter: `shoe_tracker.adapters.holabird.HolabirdAdapter`.

## Site shape

- **Search.** `GET /catalogsearch/result/?q=<query>`. Magento 2 renders a
  `<ol class="products list">` grid of `<li class="product-item">` cards. One
  URL per colorway, like Running Warehouse.
- **Product page.** One PDP per colorway. Price is product-level — Holabird
  doesn't vary price per size/width within a single colorway. Size picker is
  a `<ul class="product-sizes" data-width="D">` with one `<li>` per size;
  each `<li>` carries `.in-stock` or `.out-of-stock` as class hints. 2E-width
  variants render as a separate `<ul data-width="2E">`.
- **Style number** lives in a `.product-spec-table` row keyed "Style Number".
  Format matches RW — `1011B974.020`.
- **Clearance.** Clearance colorways are discoverable from the search page
  (title ends "– Clearance") and show both a `.was-price` and a current
  `.price` in the wrapper. We always report the current price.

## Stock handling

- In-stock vs out-of-stock is purely class-driven (`.in-stock` vs
  `.out-of-stock`). A size rendered without either class is treated as
  out-of-stock to stay conservative.
- Holabird leaves sold-out sizes in the DOM with `.out-of-stock`, so
  `fetch_variants` returns them with `in_stock=False` and the evaluator can
  reason about them.

## Rate / etiquette

- `polite_requests_per_minute = 20`.
- `PoliteClient` sleeps 1–2s between requests.
- Magento search is slower than the PDP — expect the search call to dominate
  wall time.

## Known quirks

- **One URL per colorway.** Same mapping-engine contract as RW: siblings are
  separate `SearchResult` entries.
- **Image CDN.** Image URLs are served from `cdn.holabirdsports.com`. That's
  what gets stored on `VariantPrice.image_url`.
- **No per-variant price.** Every variant on a given PDP shares the product's
  current price — `VariantPrice.price_usd` is identical across sizes/widths
  for that colorway. If a future Holabird redesign introduces per-size
  pricing, the parser will need a new data-attribute on each `<li>`.
