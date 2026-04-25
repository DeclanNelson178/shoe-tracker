"""View-model + HTML/JSON rendering for the static dashboard.

The daily scrape workflow calls `render_to_dir()` after `rotation evaluate`.
The output (`docs/index.html`, `docs/data.json`) is committed back to the
repo and served by GitHub Pages.

All SQL access goes through repository classes (see CLAUDE.md). This module
shapes that data into a dashboard-friendly view-model, then renders.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from .db import (
    Database,
    NotificationRepo,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    WatchlistRepo,
)
from .evaluator import colorway_matches
from .models import NotificationRecord, ShoeVariant, WatchlistEntry

ALERT_HISTORY_DAYS = 30
STALE_AFTER = timedelta(hours=36)
NEAR_THRESHOLD_FACTOR = 1.10  # min price within +10% of threshold => "near"


@dataclass(frozen=True)
class RetailerRow:
    retailer: str
    colorway_name: str
    colorway_code: str | None
    image_url: str | None
    price_usd: float
    in_stock: bool
    source_url: str
    scraped_at: datetime
    matches_policy: bool


@dataclass(frozen=True)
class Headline:
    price_usd: float
    retailer: str
    colorway_name: str
    image_url: str | None
    source_url: str


@dataclass(frozen=True)
class DashboardEntry:
    shoe_display: str
    gender_letter: str
    size: float
    width: str
    threshold_usd: float
    policy_label: str
    headline: Headline | None
    state: str  # "below" | "near" | "above" | "no_data"
    last_scraped_at: datetime | None
    rows: list[RetailerRow] = field(default_factory=list)


@dataclass(frozen=True)
class AlertHistoryRow:
    shoe_display: str
    colorway_name: str
    size: float
    width: str
    retailer: str
    triggering_price: float
    sent_at: datetime
    channel: str


@dataclass(frozen=True)
class DashboardData:
    generated_at: datetime
    entries: list[DashboardEntry]
    alerts: list[AlertHistoryRow]
    last_scrape_at: datetime | None
    is_stale: bool


# --- view-model construction -------------------------------------------------

def build(
    db: Database,
    *,
    user_id: str = "me",
    now: datetime | None = None,
    history_days: int = ALERT_HISTORY_DAYS,
) -> DashboardData:
    now = now or datetime.now(timezone.utc)
    watch_repo = WatchlistRepo(db)
    shoe_repo = ShoeRepo(db)
    mapping_repo = RetailerMappingRepo(db)
    snap_repo = PriceSnapshotRepo(db)
    notif_repo = NotificationRepo(db)

    shoes_by_id = {s.id: s for s in shoe_repo.list_canonical()}
    entries: list[DashboardEntry] = []
    last_scrape_at: datetime | None = None

    for entry in watch_repo.list_for_user(user_id=user_id, only_active=True):
        shoe = shoes_by_id.get(entry.canonical_shoe_id)
        if shoe is None:
            continue
        mappings = mapping_repo.list_for_shoe(entry.canonical_shoe_id)
        retailers = [m.retailer for m in mappings]
        candidates = (
            snap_repo.latest_variants_with_prices(
                canonical_shoe_id=entry.canonical_shoe_id,
                size=entry.size, width=entry.width,
                retailers=retailers,
            ) if retailers else []
        )
        rows = [_build_row(entry, variant, snap) for (variant, snap) in candidates]
        # Stable display ordering: retailer, in-stock first, then price.
        rows.sort(key=lambda r: (r.retailer, not r.in_stock, r.price_usd))

        headline = _pick_headline(rows)
        state = _state_for(headline.price_usd if headline else None, entry.threshold_usd)
        entry_last_scraped = max((r.scraped_at for r in rows), default=None)
        if entry_last_scraped and (last_scrape_at is None or entry_last_scraped > last_scrape_at):
            last_scrape_at = entry_last_scraped

        entries.append(DashboardEntry(
            shoe_display=shoe.display_name,
            gender_letter=_gender_letter(shoe.gender),
            size=entry.size,
            width=entry.width,
            threshold_usd=entry.threshold_usd,
            policy_label=_policy_label(entry),
            headline=headline,
            state=state,
            last_scraped_at=entry_last_scraped,
            rows=rows,
        ))

    notifs = notif_repo.list_recent_for_user(
        user_id=user_id, since=now - timedelta(days=history_days),
    )
    alerts = _build_alert_history(db, notifs)

    is_stale = last_scrape_at is None or (now - last_scrape_at) > STALE_AFTER

    return DashboardData(
        generated_at=now,
        entries=entries,
        alerts=alerts,
        last_scrape_at=last_scrape_at,
        is_stale=is_stale,
    )


def _build_row(
    entry: WatchlistEntry, variant: ShoeVariant, snap,
) -> RetailerRow:
    return RetailerRow(
        retailer=snap.retailer,
        colorway_name=variant.colorway_name,
        colorway_code=variant.colorway_code,
        image_url=variant.image_url,
        price_usd=snap.price_usd,
        in_stock=snap.in_stock,
        source_url=snap.source_url,
        scraped_at=snap.scraped_at,
        matches_policy=colorway_matches(entry, variant),
    )


def _pick_headline(rows: list[RetailerRow]) -> Headline | None:
    qualifying = [r for r in rows if r.in_stock and r.matches_policy]
    if not qualifying:
        return None
    cheapest = min(qualifying, key=lambda r: r.price_usd)
    return Headline(
        price_usd=cheapest.price_usd,
        retailer=cheapest.retailer,
        colorway_name=cheapest.colorway_name,
        image_url=cheapest.image_url,
        source_url=cheapest.source_url,
    )


def _state_for(price: float | None, threshold: float) -> str:
    if price is None:
        return "no_data"
    if price <= threshold:
        return "below"
    if price <= threshold * NEAR_THRESHOLD_FACTOR:
        return "near"
    return "above"


def _build_alert_history(
    db: Database, notifs: list[NotificationRecord],
) -> list[AlertHistoryRow]:
    if not notifs:
        return []
    shoe_repo = ShoeRepo(db)
    variants = shoe_repo.list_variants_by_ids({n.shoe_variant_id for n in notifs})
    variants_by_id = {v.id: v for v in variants}
    shoes_by_id = {s.id: s for s in shoe_repo.list_canonical()}
    out: list[AlertHistoryRow] = []
    for n in notifs:
        v = variants_by_id.get(n.shoe_variant_id)
        if v is None:
            continue
        shoe = shoes_by_id.get(v.canonical_shoe_id)
        if shoe is None:
            continue
        out.append(AlertHistoryRow(
            shoe_display=shoe.display_name,
            colorway_name=v.colorway_name,
            size=v.size,
            width=v.width,
            retailer=n.retailer,
            triggering_price=n.triggering_price,
            sent_at=n.sent_at,
            channel=n.channel,
        ))
    return out


# --- rendering ---------------------------------------------------------------

def render_html(data: DashboardData) -> str:
    template_text = (
        resources.files("shoe_tracker.templates").joinpath("dashboard.html").read_text()
    )
    env = Environment(autoescape=True, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["money"] = _fmt_money
    env.filters["size"] = _fmt_size
    env.filters["dt"] = _fmt_dt
    template = env.from_string(template_text)
    return template.render(data=data)


def render_json(data: DashboardData) -> str:
    return json.dumps(_to_jsonable(data), indent=2, sort_keys=True, default=_json_default)


def render_to_dir(db: Database, out_dir: Path, *, now: datetime | None = None) -> tuple[Path, Path]:
    """Build the view-model and write index.html + data.json into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(db, now=now)
    html_path = out_dir / "index.html"
    json_path = out_dir / "data.json"
    html_path.write_text(render_html(data))
    json_path.write_text(render_json(data))
    return html_path, json_path


# --- formatting helpers ------------------------------------------------------

def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def _fmt_size(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _gender_letter(gender: str) -> str:
    return {"mens": "M", "womens": "W", "unisex": "U"}.get(gender, "?")


def _policy_label(entry: WatchlistEntry) -> str:
    if entry.colorway_policy == "any":
        return "any colorway"
    listed = ", ".join(entry.colorway_list) or "(none)"
    return f"{entry.colorway_policy}: {listed}"


# --- json serialisation ------------------------------------------------------

def _to_jsonable(data: DashboardData) -> dict[str, Any]:
    return asdict(data)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")
