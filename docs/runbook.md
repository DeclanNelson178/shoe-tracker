# Runbook

Operational notes for the deployed shoe-tracker. Pair this with `plan.md`
for the design context.

## Add a shoe to the rotation

1. Edit `config/rotation.yaml`. Copy an existing entry as a template.
2. Find the manufacturer style prefix when you can — improves mapping
   confidence (`mfr_style_prefix: "1011B867"` for the ASICS Novablast 5,
   for example).
3. Pick the right colorway policy:
   - `any` (default) — alert on any colorway.
   - `allowlist` — only the listed colorway names/codes alert.
   - `denylist` — alert on everything except the listed names/codes.
   Match is case-insensitive substring on both `colorway_name` and
   `colorway_code`.
4. Sync + map locally to confirm everything resolves before pushing:

   ```bash
   python -m shoe_tracker init-db
   python -m shoe_tracker rotation map --all
   python -m shoe_tracker rotation status
   ```

5. Commit `config/rotation.yaml` and push. The next daily scrape picks
   up the new entry.

## Re-run a failed scrape

`Actions → Scrape → Run workflow` (drop-down on the right). It uses the
same secrets as the cron run and commits its results back to `main` if
anything changed.

## Read the local sqlite

The daily scrape commits `shoe_tracker.db` back to the repo. To inspect:

```bash
git pull
sqlite3 shoe_tracker.db
sqlite> .tables
sqlite> SELECT * FROM watchlist;
sqlite> SELECT retailer, price_usd, in_stock, scraped_at
        FROM price_snapshots ORDER BY scraped_at DESC LIMIT 20;
```

## Debug a broken adapter

Symptom: the weekly health probe (`Adapter health` workflow) emails a
failure, or the daily Scrape's email mentions an adapter you don't expect
to be quiet.

1. Reproduce locally with the live integration tests:

   ```bash
   SHOE_TRACKER_LIVE=1 pytest -m live -k <retailer>
   ```

2. Probe the retailer directly to see what it's actually returning:

   ```bash
   python -m shoe_tracker probe <retailer> --canonical "ASICS Novablast 5"
   ```

3. If the site changed structure, save a fresh fixture into
   `tests/fixtures/<retailer>/`, port the parser, and update the
   offline tests.

4. If the retailer hits Cloudflare and the rate-limit backoff isn't
   helping, add it to `docs/retailers/skipped.md` with a note and remove
   from `ADAPTERS` until the situation resolves.

## Rotate the Gmail app password

Required if the password gets compromised, the Google account is changed,
or after revoking it for any reason.

1. https://myaccount.google.com/apppasswords (2FA must be on)
2. Create a new app password — copy it immediately, you can't view it
   again.
3. GitHub → Settings → Secrets and variables → Actions → update
   `GMAIL_APP_PASSWORD`.
4. Revoke the old password from the same app-passwords page.
5. (Optional) Manually dispatch the Scrape workflow to confirm the new
   secret works end-to-end.

## Interpret the dashboard

The static dashboard at `https://<owner>.github.io/shoe-tracker/` is
re-rendered by the daily scrape. Each watchlist entry shows:

- **Headline price** — cheapest in-stock variant matching the entry's
  size, width, and colorway policy. Below threshold = green; up to +10%
  = yellow; further above = red; no in-stock data = grey.
- **Per-retailer breakdown** — every (retailer, colorway) snapshot at
  your size+width, including out-of-stock rows so you can see what's
  available.
- **Last scrape timestamp** — when the most recent snapshot for this
  entry was taken.

If the page banner says "Stale", the most recent scrape across all
retailers is more than 36 hours old. Check the Scrape workflow's most
recent runs.

## What to do when secrets leak

- Revoke the Gmail app password (steps above).
- Rotate any other affected secrets in GitHub settings.
- Force a fresh scrape with `Actions → Scrape → Run workflow` to confirm
  the rotation took.
- The repo doesn't store the secrets — they only live in GitHub Actions
  secrets — so there's no need to scrub git history.
