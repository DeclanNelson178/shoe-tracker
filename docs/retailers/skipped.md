# Skipped retailers

Retailers that hit Cloudflare/Akamai or require headless-browser rendering get
logged here and skipped. Policy is in `CLAUDE.md`: no Playwright, no Selenium.

## Zappos

**Status:** skipped in chunk 4. Revisit in chunk 8 if we ever get real data.

**Why:** Zappos sits behind Akamai Bot Manager. A plain `httpx` GET with a
realistic User-Agent returns a challenge page (JS-evaluated cookie + TLS
fingerprint check) rather than product HTML. Every approach consistent with
our ground rules — polite User-Agent, no headless browser, no paid unblocker
— fails to retrieve product detail pages.

**What we tried (and ruled out):**

1. **Realistic UA + Accept-Language.** Same as the RW/RRS/Holabird path.
   Response body is an Akamai challenge page, not the PDP.
2. **Backing off to `/product/<id>/color/<id>`.** Same result — the challenge
   is domain-wide, not path-scoped.
3. **Internal JSON API (`/marty/product/…`).** The public paths work for
   logged-in sessions only; scraping them unauthenticated runs into the same
   bot check.

**What would unblock this (explicitly out of scope for v1):**

- A residential proxy + TLS-fingerprint-matching HTTP client. Violates the
  zero-cost rule in `plan.md`.
- Playwright driving a real browser. Violates the no-headless-browser rule in
  `CLAUDE.md`.

**Impact on the rotation:** none. Zappos is usually priced at MSRP or close
to it for running shoes, so in practice it would almost never produce the
min-price winner for a threshold alert. The four working adapters (Running
Warehouse, Road Runner Sports, Holabird, JackRabbit) comfortably cover the
discount space that actually triggers alerts.
