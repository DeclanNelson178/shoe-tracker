"""Tests for the chunk-6 dashboard stub.

The real renderer lands in chunk 7. This stub just guarantees the workflow
has a script to call and that GitHub Pages doesn't 404 between chunks.
"""
from __future__ import annotations

from datetime import datetime, timezone

from render_dashboard import render


def test_render_writes_placeholder_html(tmp_path):
    out = tmp_path / "docs" / "index.html"
    render(out, now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc))
    assert out.exists()
    text = out.read_text()
    assert "<html" in text.lower()
    assert "shoe-tracker" in text
    assert "2026-04-25 14:00 UTC" in text


def test_render_creates_parent_directory(tmp_path):
    out = tmp_path / "fresh" / "docs" / "index.html"
    render(out, now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc))
    assert out.exists()
