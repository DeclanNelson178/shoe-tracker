"""Render the static dashboard from the local sqlite DB.

Thin entry point — all real work lives in `shoe_tracker.dashboard`. The daily
scrape workflow runs this after `rotation evaluate`; the resulting
`docs/index.html` and `docs/data.json` get committed back to the repo and
served by GitHub Pages.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from shoe_tracker.dashboard import render_to_dir
from shoe_tracker.db import DEFAULT_DB_PATH, Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=Path("docs"))
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"render_dashboard: DB not found at {args.db}; nothing to render.")
        return 0

    with Database(args.db) as db:
        html_path, json_path = render_to_dir(db, args.out)
    print(f"Wrote {html_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
