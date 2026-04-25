# shoe-tracker

[![Scrape](https://github.com/DeclanNelson178/shoe-tracker/actions/workflows/scrape.yml/badge.svg)](https://github.com/DeclanNelson178/shoe-tracker/actions/workflows/scrape.yml)
[![Tests](https://github.com/DeclanNelson178/shoe-tracker/actions/workflows/test.yml/badge.svg)](https://github.com/DeclanNelson178/shoe-tracker/actions/workflows/test.yml)

Personal running shoe price tracker. Watches a configured rotation of shoes
across running-specialty retailers and emails me when a variant in my size and
an acceptable colorway drops below a threshold.

See `plan.md` for the full build plan.

## Quickstart

```bash
pip install -e .[dev]
python -m shoe_tracker init-db
python -m shoe_tracker rotation list

# Probe a retailer end-to-end (hits the real site):
python -m shoe_tracker probe running_warehouse \
  --canonical "ASICS Novablast 5" --gender mens \
  --size-min 10 --size-max 11 --width D
```

Edit `config/rotation.yaml` to add shoes.

## Dashboard

The daily Scrape workflow renders `docs/index.html` and `docs/data.json` from
the committed sqlite. Once GitHub Pages is configured to serve from `docs/`
on `main`, the dashboard is glanceable at the published Pages URL. To render
it locally:

```bash
python scripts/render_dashboard.py        # writes docs/index.html + docs/data.json
open docs/index.html
```

## Layout

```
src/shoe_tracker/          # package
  models.py                # pydantic domain models
  config.py                # rotation.yaml loader
  db.py                    # sqlite + repository pattern
  cli.py                   # command-line entrypoint
  evaluator.py             # threshold + colorway-policy evaluator
  dashboard.py             # view-model + Jinja rendering for the static dashboard
  notifiers/               # email + (future) other channels
  adapters/                # retailer scrapers (RetailerAdapter + per-retailer)
  templates/               # Jinja templates (dashboard.html)
  db/migrations/           # SQL schema migrations
scripts/                   # workflow entry points (render_dashboard, notify_workflow_failure)
config/rotation.yaml       # the watchlist (hand-edited)
docs/retailers/            # per-retailer scraping notes
tests/                     # pytest suite (live tests env-gated)
plan.md                    # build plan
```

## Testing

```bash
pytest                         # unit tests, offline
SHOE_TRACKER_LIVE=1 pytest -m live    # hits real retailer sites
```
