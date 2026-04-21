# JackRabbit

Adapter: `shoe_tracker.adapters.jackrabbit.JackrabbitAdapter`.

## Site shape

- **Platform.** Shopify storefront at `jackrabbit.com`.
- **Search.** `GET /search?q=<query>`. Renders a grid of
  `<a class="grid-product__card">` anchors. One card per product (men's,
  women's, and GTX are distinct products with distinct URLs). Colorways live
  on the product page, not on the search page.
- **Product page.** Shopify PDP with every variant in an inline
  `<script type="application/json" id="ProductJson-…">` block. The adapter
  parses that JSON; the rendered size/color/width picker is hydrated from
  the same data.
- **Option order.** JackRabbit consistently lays options out as
  `["Size", "Color", "Width"]`. We look up each option by name (not
  position) so a reordered PDP doesn't silently mis-parse.
- **Cents.** Shopify prices are integers-in-cents. We divide by 100 to store
  dollars on `VariantPrice.price_usd`.

## Stock handling

- Each variant carries an explicit `available` boolean. Out-of-stock
  variants are included in the JSON, so `fetch_variants` returns them with
  `in_stock=False`.

## Rate / etiquette

- `polite_requests_per_minute = 20`.
- `PoliteClient` sleeps 1–2s between requests.
- Shopify storefronts cache aggressively at Cloudflare; our request volume
  (one search + one PDP per canonical shoe per day) is well under any
  reasonable rate limit.

## Known quirks

- **No manufacturer style code.** JackRabbit's SKU is an internal catalog
  code (`ASI-1011B974-020-100D` in our fixture), not the raw ASICS style
  number. `supports_style_codes = False` for this adapter; the mapping
  engine's style-code-prefix fast path therefore doesn't fire for JackRabbit
  and it falls back to token scoring.
- **Per-colorway price.** Shopify allows variant-level prices, and JackRabbit
  uses that for color-specific sales — different colorways of the same shoe
  can sit at different price points on the PDP. Our parser surfaces that
  correctly; the evaluator handles per-variant pricing already.
- **One URL per product.** Like Road Runner Sports. A single PDP fetch
  returns every colorway's variants.
