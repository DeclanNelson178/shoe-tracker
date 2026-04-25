"""Stub dashboard renderer.

Chunk 7 will replace this with a real Jinja2-driven view of the rotation,
current prices, retailer breakdown, and alert history. Until then the daily
scrape workflow needs *something* to call so GitHub Pages doesn't 404 between
Chunk 6 landing and Chunk 7 starting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

PLACEHOLDER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>shoe-tracker</title>
  <style>
    body {{
      font-family: system-ui, sans-serif;
      max-width: 36rem;
      margin: 4rem auto;
      padding: 0 1rem;
      color: #222;
    }}
    .stamp {{ color: #888; font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>shoe-tracker</h1>
  <p>Dashboard placeholder &mdash; the real renderer lands in chunk 7.</p>
  <p class="stamp">Generated {generated_at}</p>
</body>
</html>
"""


def render(out_path: Path, *, now: datetime | None = None) -> Path:
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(PLACEHOLDER.format(generated_at=when))
    return out_path


def main() -> int:
    render(Path("docs/index.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
