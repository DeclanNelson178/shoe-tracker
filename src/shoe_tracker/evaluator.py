"""Watchlist evaluator.

For each active watchlist entry:
  1. Load the latest snapshot per (variant, retailer) at the configured
     size + width, filtered to retailers that have a mapping.
  2. Drop out-of-stock rows and rows whose variant fails the colorway policy.
  3. Keep rows priced at or under the entry's threshold.
  4. Pick the cheapest qualifying (variant, retailer).
  5. Skip the alert if that (variant, retailer) was already notified within
     `dedup_window` (default 7 days) — `notifications_sent` is the source of
     truth.

Colorway policy (see plan.md):
  - `any`        — every variant passes
  - `allowlist`  — variant's colorway name/code must match a listed term
                   (case-insensitive substring)
  - `denylist`   — variant is excluded if it matches a listed term
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import (
    Database,
    NotificationRepo,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    WatchlistRepo,
)
from .models import CanonicalShoe, ShoeVariant, WatchlistEntry


DEDUP_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class TriggeredAlert:
    entry: WatchlistEntry
    shoe: CanonicalShoe
    variant: ShoeVariant
    retailer: str
    price_usd: float
    source_url: str

    @property
    def threshold_usd(self) -> float:
        return self.entry.threshold_usd

    @property
    def delta_usd(self) -> float:
        return self.entry.threshold_usd - self.price_usd


def evaluate(
    db: Database,
    *,
    user_id: str = "me",
    now: datetime | None = None,
    dedup_window: timedelta = DEDUP_WINDOW,
) -> list[TriggeredAlert]:
    now = now or datetime.now(timezone.utc)
    watch_repo = WatchlistRepo(db)
    shoe_repo = ShoeRepo(db)
    mapping_repo = RetailerMappingRepo(db)
    snap_repo = PriceSnapshotRepo(db)
    notif_repo = NotificationRepo(db)

    shoes_by_id = {s.id: s for s in shoe_repo.list_canonical()}
    alerts: list[TriggeredAlert] = []

    for entry in watch_repo.list_for_user(user_id=user_id, only_active=True):
        shoe = shoes_by_id.get(entry.canonical_shoe_id)
        if shoe is None:
            continue
        mappings = mapping_repo.list_for_shoe(entry.canonical_shoe_id)
        retailers = [m.retailer for m in mappings]
        if not retailers:
            continue

        candidates = snap_repo.latest_variants_with_prices(
            canonical_shoe_id=entry.canonical_shoe_id,
            size=entry.size, width=entry.width,
            retailers=retailers,
        )
        qualifying = [
            (variant, snap)
            for (variant, snap) in candidates
            if snap.in_stock
            and snap.price_usd <= entry.threshold_usd
            and _colorway_matches(entry, variant)
            and not _recently_notified(
                notif_repo, user_id, variant, snap.retailer, now, dedup_window,
            )
        ]
        if not qualifying:
            continue
        variant, snap = min(qualifying, key=lambda pair: pair[1].price_usd)
        alerts.append(TriggeredAlert(
            entry=entry, shoe=shoe, variant=variant,
            retailer=snap.retailer, price_usd=snap.price_usd,
            source_url=snap.source_url,
        ))
    return alerts


def _colorway_matches(entry: WatchlistEntry, variant: ShoeVariant) -> bool:
    policy = entry.colorway_policy
    if policy == "any":
        return True
    haystack = " ".join(
        s.lower() for s in (variant.colorway_name, variant.colorway_code) if s
    )
    hit = any(term.lower() in haystack for term in entry.colorway_list if term)
    if policy == "allowlist":
        return hit
    # denylist
    return not hit


def _recently_notified(
    notif_repo: NotificationRepo, user_id: str, variant: ShoeVariant,
    retailer: str, now: datetime, window: timedelta,
) -> bool:
    assert variant.id is not None
    last = notif_repo.last_sent_at(
        user_id=user_id, shoe_variant_id=variant.id, retailer=retailer,
    )
    if last is None:
        return False
    return (now - last) < window
