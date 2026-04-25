# Holabird Sports

Adapter: `shoe_tracker.adapters.holabird.HolabirdAdapter`.

## Site shape

- **Platform.** Shopify storefront at `holabirdsports.com`. The pre-2026 site
  was Magento 2 — old fixtures and the old parser targeted that. The Shopify
  migration changed every selector, which is what the chunk-8 health probe
  caught.
- **Search.** `GET /search?q=<query>`. Returns a single page of
  `.product-item--vertical` cards. Each card's title link points at
  `/products/<slug>`. One URL per colorway, like Running Warehouse.
- **Product page.** Shopify PDP with every variant in an inline
  `<script type="application/json" id="ProductJson-…">` block. Options are
  `["Size", "Width"]` — colorway is part of the product title, not a variant
  option. We parse the JSON; the rendered size picker is hydrated from the
  same data.
- **Style number.** Rendered as a spec block with `id="style-number"` and a
  `title="Style #: 1011B974.020"` attribute. Format matches RW.
- **Cents.** Shopify prices are integers-in-cents. We divide by 100 to store
  dollars on `VariantPrice.price_usd`.

## Stock handling

- Each variant carries an explicit `available` boolean. Out-of-stock variants
  are still in the JSON, so `fetch_variants` returns them with
  `in_stock=False`.

## Sale pricing

- Sale variants carry both `price` (current/sale) and `compare_at_price`
  (regular). We always surface `price` — the current price the shopper would
  actually pay. Search-card markup is the same shape: `.price--highlight` is
  the current price, `.price--compare` is the was-price; the parser ignores
  the compare element.

## Width labels

- Shopify's `option2` ships as a verbose label: `"D - Medium"`, `"EE - Wide"`.
  The adapter normalizes the leading token to the short form used elsewhere
  in the system: `D`, `2E`, `B`, `2A`, `4E`. Anything outside that map falls
  through verbatim so the parser doesn't silently swallow a new label.

## Rate / etiquette

- `polite_requests_per_minute = 20`.
- `PoliteClient` sleeps 1–2s between requests.
- One search + one PDP per canonical shoe per day is well under any
  reasonable Shopify storefront rate limit.

## Known quirks

- **One URL per colorway.** Same mapping-engine contract as RW: siblings are
  separate `SearchResult` entries. JackRabbit, by contrast, ships one URL per
  product with all colorways inlined.
- **Image CDN.** `featured_image` comes back protocol-relative
  (`//www.holabirdsports.com/cdn/...`). The adapter prepends `https:`.
