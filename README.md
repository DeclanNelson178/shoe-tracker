# shoe-tracker

Personal running shoe price tracker. Watches a configured rotation of shoes
across running-specialty retailers and emails me when a variant in my size and
an acceptable colorway drops below a threshold.

See `plan.md` for the full build plan.

## Quickstart

```bash
pip install -e .[dev]
python -m shoe_tracker init-db
python -m shoe_tracker rotation list
```

Edit `config/rotation.yaml` to add shoes.

## Layout

```
src/shoe_tracker/          # package
  models.py                # pydantic domain models
  config.py                # rotation.yaml loader
  db.py                    # sqlite + repository pattern
  cli.py                   # command-line entrypoint
  db/migrations/           # SQL schema migrations
config/rotation.yaml       # the watchlist (hand-edited)
tests/                     # pytest suite
plan.md                    # build plan
```
