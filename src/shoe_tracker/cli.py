"""Command-line entry point. Stubs for later chunks live here too."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click

from . import config as config_mod
from .adapters import ADAPTERS, VariantPrice, get_adapter
from .adapters.http import RateLimitedError
from .db import (
    DEFAULT_DB_PATH,
    Database,
    NotificationRepo,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    UserRepo,
    WatchlistRepo,
)
from .db import (
    init_db as run_init_db,
)
from .evaluator import TriggeredAlert, evaluate
from .mapping import MappingOutcome, MappingTier, pick_best
from .models import (
    CanonicalShoe,
    NotificationRecord,
    PriceSnapshot,
    RetailerMapping,
    RotationConfig,
    ShoeVariant,
    User,
    WatchlistEntry,
)
from .notifiers import Notifier, email_notifier_from_env

MAPPING_REVIEW_PATH = Path("docs/mapping_review.md")


@click.group(invoke_without_command=False)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=DEFAULT_DB_PATH,
              help="SQLite database path.")
@click.option("--config", "config_path", type=click.Path(path_type=Path),
              default=config_mod.DEFAULT_PATH, help="Path to rotation.yaml.")
@click.pass_context
def main(ctx: click.Context, db_path: Path, config_path: Path) -> None:
    """shoe-tracker CLI."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["config_path"] = config_path


@main.command("init-db")
@click.pass_context
def init_db_cmd(ctx: click.Context) -> None:
    """Apply migrations and sync rotation.yaml into the database."""
    db_path: Path = ctx.obj["db_path"]
    config_path: Path = ctx.obj["config_path"]
    run_init_db(db_path)
    click.echo(f"Initialized database at {db_path}")

    if not Path(config_path).exists():
        click.echo(f"No rotation config at {config_path} — skipping sync.")
        return
    cfg = config_mod.load_rotation(config_path)
    with Database(db_path) as db:
        _sync_rotation(db, cfg)
    click.echo(f"Synced {len(cfg.shoes)} rotation entr{'y' if len(cfg.shoes) == 1 else 'ies'}.")


@main.group()
def rotation() -> None:
    """Manage the rotation."""


@rotation.command("list")
@click.pass_context
def rotation_list(ctx: click.Context) -> None:
    """List watchlist entries with mapping status."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise click.ClickException(
            f"Database not found at {db_path}. Run 'shoe-tracker init-db' first."
        )
    with Database(db_path) as db:
        watch_repo = WatchlistRepo(db)
        shoe_repo = ShoeRepo(db)
        mapping_repo = RetailerMappingRepo(db)
        entries = watch_repo.list_for_user()
        if not entries:
            click.echo("(rotation is empty)")
            return
        shoes_by_id = {s.id: s for s in shoe_repo.list_canonical()}
        for e in entries:
            shoe = shoes_by_id.get(e.canonical_shoe_id)
            shoe_label = shoe.display_name if shoe else f"canonical#{e.canonical_shoe_id}"
            gender_letter = _gender_letter(shoe.gender if shoe else "mens")
            mappings = mapping_repo.list_for_shoe(e.canonical_shoe_id)
            mapping_label = (
                ", ".join(f"{m.retailer}({m.confidence:.2f})" for m in mappings)
                if mappings else "unmapped"
            )
            policy = _policy_label(e.colorway_policy, e.colorway_list)
            click.echo(
                f"{shoe_label} ({gender_letter} {_fmt_size(e.size)} {e.width}) "
                f"— {policy} — threshold ${_fmt_money(e.threshold_usd)} "
                f"— {mapping_label}"
            )


# --- stubs for later chunks ---

@rotation.command("sync")
@click.pass_context
def rotation_sync(ctx: click.Context) -> None:
    """Re-read rotation.yaml and upsert into the database."""
    db_path: Path = ctx.obj["db_path"]
    config_path: Path = ctx.obj["config_path"]
    cfg = config_mod.load_rotation(config_path)
    with Database(db_path) as db:
        n = _sync_rotation(db, cfg)
    click.echo(f"Synced {n} rotation entries.")


@rotation.command("map")
@click.option("--retailer", type=click.Choice(sorted(ADAPTERS)))
@click.option("--all", "all_retailers", is_flag=True,
              help="Map every registered retailer.")
@click.option("--review-path", type=click.Path(path_type=Path),
              default=MAPPING_REVIEW_PATH,
              help="Where to write the mapping-review markdown for flagged entries.")
@click.pass_context
def rotation_map(
    ctx: click.Context,
    retailer: str | None,
    all_retailers: bool,
    review_path: Path,
) -> None:
    """Resolve per-retailer product URLs with confidence scoring.

    Writes to `retailer_mappings` and snapshots variants from every mapped
    product page. Low-confidence mappings (0.6–0.9) are recorded and also
    surfaced in `docs/mapping_review.md` for manual eyeballing.
    """
    if not retailer and not all_retailers:
        raise click.UsageError("pass --retailer NAME or --all")

    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise click.ClickException(
            f"Database not found at {db_path}. Run 'shoe-tracker init-db' first."
        )

    retailers = sorted(ADAPTERS) if all_retailers else [retailer]  # type: ignore[list-item]
    flagged: list[tuple[CanonicalShoe, str, MappingOutcome]] = []

    with Database(db_path) as db:
        shoes_by_id = {s.id: s for s in ShoeRepo(db).list_canonical()}
        entries = WatchlistRepo(db).list_for_user()
        if not entries:
            click.echo("(rotation is empty — nothing to map)")
            return

        for r_name in retailers:
            adapter = get_adapter(r_name)
            rate_limited = False
            for entry in entries:
                if rate_limited:
                    continue
                shoe = shoes_by_id.get(entry.canonical_shoe_id)
                if shoe is None:
                    continue
                try:
                    outcome = _map_one(adapter, shoe)
                except RateLimitedError as exc:
                    click.echo(
                        f"  ! {r_name} rate-limited: {exc} — skipping for this run",
                        err=True,
                    )
                    rate_limited = True
                    continue
                _echo_outcome(shoe, r_name, outcome)
                if outcome.best is None:
                    continue
                try:
                    _persist_outcome(db, shoe, r_name, outcome)
                except RateLimitedError as exc:
                    click.echo(
                        f"  ! {r_name} rate-limited during fetch: {exc} — skipping for this run",
                        err=True,
                    )
                    rate_limited = True
                    continue
                if outcome.tier is MappingTier.FLAGGED:
                    flagged.append((shoe, r_name, outcome))

    _write_mapping_review(review_path, flagged)


@rotation.command("status")
@click.pass_context
def rotation_status(ctx: click.Context) -> None:
    """Show current minimum price per watchlist entry."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise click.ClickException(
            f"Database not found at {db_path}. Run 'shoe-tracker init-db' first."
        )
    with Database(db_path) as db:
        shoes_by_id = {s.id: s for s in ShoeRepo(db).list_canonical()}
        entries = WatchlistRepo(db).list_for_user()
        if not entries:
            click.echo("(rotation is empty)")
            return
        mapping_repo = RetailerMappingRepo(db)
        for e in entries:
            shoe = shoes_by_id.get(e.canonical_shoe_id)
            label = shoe.display_name if shoe else f"canonical#{e.canonical_shoe_id}"
            gender = _gender_letter(shoe.gender if shoe else "mens")
            header = (
                f"{label} ({gender} {_fmt_size(e.size)} {e.width}, "
                f"{_policy_label(e.colorway_policy, e.colorway_list)})  "
                f"threshold ${_fmt_money(e.threshold_usd)}"
            )
            mappings = mapping_repo.list_for_shoe(e.canonical_shoe_id)
            if not mappings:
                click.echo(f"{header}  unmapped")
                continue
            winner = _current_min(db, e, mappings)
            if winner is None:
                click.echo(f"{header}  no price data yet")
                continue
            price, retailer, colorway = winner
            click.echo(
                f"{header}  current min ${_fmt_money(price)} {colorway} @ {retailer}"
            )


@rotation.command("set-threshold")
@click.argument("shoe")
@click.argument("threshold", type=float)
@click.pass_context
def rotation_set_threshold(ctx: click.Context, shoe: str, threshold: float) -> None:
    """Update the USD threshold for watchlist entries matching SHOE.

    SHOE is matched case-insensitively as a substring of the canonical shoe's
    display name — "Novablast 5" is enough to hit "ASICS Novablast 5".
    """
    if threshold <= 0:
        raise click.ClickException("threshold must be positive")
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise click.ClickException(
            f"Database not found at {db_path}. Run 'shoe-tracker init-db' first."
        )
    with Database(db_path) as db:
        shoe_repo = ShoeRepo(db)
        watch_repo = WatchlistRepo(db)
        needle = shoe.lower()
        matches = [s for s in shoe_repo.list_canonical() if needle in s.display_name.lower()]
        if not matches:
            raise click.ClickException(f"no canonical shoe matches {shoe!r}")
        if len(matches) > 1:
            names = ", ".join(s.display_name for s in matches)
            raise click.ClickException(f"ambiguous shoe {shoe!r}: {names}")
        target = matches[0]
        entries = [
            e for e in watch_repo.list_for_user(only_active=False)
            if e.canonical_shoe_id == target.id
        ]
        if not entries:
            raise click.ClickException(
                f"no watchlist entry for {target.display_name}"
            )
        for entry in entries:
            watch_repo.upsert(WatchlistEntry(
                user_id=entry.user_id,
                canonical_shoe_id=entry.canonical_shoe_id,
                size=entry.size,
                width=entry.width,
                colorway_policy=entry.colorway_policy,
                colorway_list=list(entry.colorway_list),
                threshold_usd=threshold,
                active=entry.active,
            ))
        click.echo(
            f"{target.display_name}: threshold set to ${_fmt_money(threshold)} "
            f"(updated {len(entries)} entr{'y' if len(entries) == 1 else 'ies'})"
        )


@rotation.command("evaluate")
@click.option("--dry-run", is_flag=True,
              help="Print alerts without building a notifier or writing notifications_sent.")
@click.pass_context
def rotation_evaluate(ctx: click.Context, dry_run: bool) -> None:
    """Run the evaluator, send an email per triggered alert, record dedup rows."""
    db_path: Path = ctx.obj["db_path"]
    if not db_path.exists():
        raise click.ClickException(
            f"Database not found at {db_path}. Run 'shoe-tracker init-db' first."
        )
    notifier: Notifier | None = None if dry_run else email_notifier_from_env()
    with Database(db_path) as db:
        alerts = evaluate(db)
        if not alerts:
            click.echo("No alerts.")
            return
        user = UserRepo(db).get("me")
        assert user is not None, "init-db should have synced the 'me' user"
        for alert in alerts:
            _echo_alert(alert)
            if dry_run:
                continue
            if notifier is None:
                click.echo("  (SMTP not configured — skipping send)")
                continue
            if not notifier.notify(user, alert):
                click.echo(f"  ! {notifier.channel} send failed", err=True)
                continue
            NotificationRepo(db).insert(NotificationRecord(
                user_id=user.id,
                shoe_variant_id=alert.variant.id,  # type: ignore[arg-type]
                retailer=alert.retailer,
                triggering_price=alert.price_usd,
                sent_at=datetime.now(timezone.utc),
                channel=notifier.channel,  # type: ignore[arg-type]
            ))
            click.echo(f"  email sent to {user.email}")


def _echo_alert(alert: TriggeredAlert) -> None:
    click.echo(
        f"{alert.shoe.display_name} "
        f"(size {_fmt_size(alert.variant.size)} {alert.variant.width}, "
        f"{alert.variant.colorway_name}) "
        f"${alert.price_usd:.2f} @ {alert.retailer} "
        f"(threshold ${_fmt_money(alert.threshold_usd)}, "
        f"save ${alert.delta_usd:.2f}) — {alert.source_url}"
    )


@main.command()
@click.argument("retailer", type=click.Choice(sorted(ADAPTERS)))
@click.option("--canonical", required=True,
              help='Canonical shoe name, e.g. "ASICS Novablast 5".')
@click.option("--gender", type=click.Choice(["mens", "womens", "unisex"]), default="mens")
@click.option("--variant-type", default=None,
              help='Optional: "GTX", "Wide", "Trail".')
@click.option("--size-min", type=float, default=None, help="Filter output to sizes ≥ this.")
@click.option("--size-max", type=float, default=None, help="Filter output to sizes ≤ this.")
@click.option("--width", default=None, help="Filter output to this width (e.g. 'D', '2E').")
def probe(
    retailer: str,
    canonical: str,
    gender: str,
    variant_type: str | None,
    size_min: float | None,
    size_max: float | None,
    width: str | None,
) -> None:
    """Probe a retailer end-to-end for a canonical shoe.

    Runs the retailer's search, then fetches variant data from each candidate
    product page and prints every variant found. No DB writes — this is a
    human-facing sanity check.
    """
    shoe = _parse_canonical(canonical, gender=gender, variant_type=variant_type)
    adapter = get_adapter(retailer)
    results = adapter.search(shoe)
    if not results:
        raise click.ClickException(f"No results from {retailer} for '{canonical}'.")

    click.echo(f"Searched {retailer} for {shoe.display_name} ({gender}) — {len(results)} candidates.")
    all_variants: list[VariantPrice] = []
    for r in results:
        click.echo(f"  • {r.title} — {r.colorway_name or '?'} — {r.product_url}")
        all_variants.extend(adapter.fetch_variants(r.product_url))

    filtered = [
        v for v in all_variants
        if (size_min is None or v.size >= size_min)
        and (size_max is None or v.size <= size_max)
        and (width is None or v.width == width)
    ]
    click.echo("")
    click.echo(f"Variants ({len(filtered)} of {len(all_variants)} shown):")
    for v in sorted(filtered, key=lambda x: (x.size, x.colorway_name, x.width)):
        marker = "in stock" if v.in_stock else "out of stock"
        click.echo(
            f"  {_fmt_size(v.size):>5} / {v.width:<3} {v.colorway_name:<32} "
            f"${v.price_usd:>7.2f}  {marker}"
        )


# --- helpers ---

def _parse_canonical(canonical: str, *, gender: str, variant_type: str | None) -> CanonicalShoe:
    """Split a '<Brand> <Model> [version]' string into a CanonicalShoe.

    The probe command takes a free-form canonical name to keep the invocation
    friendly. The mapping engine in chunk 3 will use richer matching; here we
    just need enough structure for the search URL.
    """
    tokens = canonical.split()
    if len(tokens) < 2:
        raise click.ClickException(
            f"Canonical name must be '<Brand> <Model> [version]' (got: {canonical!r})."
        )
    brand = tokens[0]
    # A trailing token that looks like a version (digit or digits+letter) peels off.
    version: str | None = None
    body = tokens[1:]
    if body and (body[-1].isdigit() or (body[-1][:-1].isdigit() and body[-1][-1].isalpha())):
        version = body[-1]
        body = body[:-1]
    if not body:
        raise click.ClickException(f"Canonical name is missing the model: {canonical!r}.")
    model = " ".join(body)
    return CanonicalShoe(
        brand=brand, model=model, version=version,
        gender=gender, variant_type=variant_type,  # type: ignore[arg-type]
    )


def _sync_rotation(db: Database, cfg: RotationConfig) -> int:
    UserRepo(db).upsert(User(id="me", email=cfg.user_email))
    shoe_repo = ShoeRepo(db)
    watch_repo = WatchlistRepo(db)
    count = 0
    for rs in cfg.shoes:
        canonical = shoe_repo.upsert_canonical(CanonicalShoe(
            brand=rs.brand, model=rs.model, version=rs.version,
            gender=rs.gender, variant_type=rs.variant_type,
            mfr_style_prefix=rs.mfr_style_prefix,
        ))
        watch_repo.upsert(WatchlistEntry(
            user_id="me",
            canonical_shoe_id=canonical.id,  # type: ignore[arg-type]
            size=rs.size,
            width=rs.width,
            colorway_policy=rs.colorway_policy,
            colorway_list=rs.colorway_list,
            threshold_usd=rs.threshold_usd,
        ))
        count += 1
    return count


def _gender_letter(gender: str) -> str:
    return {"mens": "M", "womens": "W", "unisex": "U"}.get(gender, "?")


def _fmt_size(size: float) -> str:
    return f"{size:g}"  # 10.5 -> "10.5", 10.0 -> "10"


def _fmt_money(v: float) -> str:
    return f"{v:g}" if v == int(v) else f"{v:.2f}"


def _map_one(adapter, shoe: CanonicalShoe) -> MappingOutcome:
    """Search the retailer for one canonical shoe and score the candidates.

    `RateLimitedError` propagates out so the caller can short-circuit the
    whole retailer for this run. Other exceptions are logged and treated as
    a rejected mapping for this single shoe.
    """
    try:
        results = adapter.search(shoe)
    except RateLimitedError:
        raise
    except Exception as exc:  # pragma: no cover — defensive; live adapter errors
        click.echo(f"  ! {adapter.name} search failed: {exc}", err=True)
        return MappingOutcome(best=None, confidence=0.0, tier=MappingTier.REJECTED)
    return pick_best(shoe, results)


def _echo_outcome(shoe: CanonicalShoe, retailer: str, outcome: MappingOutcome) -> None:
    if outcome.best is None:
        click.echo(f"{shoe.display_name} → {retailer}: unmapped (no candidates passed)")
        return
    label = {
        MappingTier.AUTO: "mapped",
        MappingTier.FLAGGED: "mapped (review)",
    }.get(outcome.tier, "unmapped")
    click.echo(
        f"{shoe.display_name} → {retailer}: {label} ({outcome.confidence:.2f}) "
        f"→ {outcome.best.product_url}"
    )


def _persist_outcome(
    db: Database, shoe: CanonicalShoe, retailer: str, outcome: MappingOutcome,
) -> None:
    assert shoe.id is not None
    best = outcome.best
    assert best is not None
    RetailerMappingRepo(db).upsert(RetailerMapping(
        canonical_shoe_id=shoe.id,
        retailer=retailer,
        product_url=best.product_url,
        product_id=best.product_code,
        confidence=outcome.confidence,
    ))
    # Snapshot the variants from the mapped page so `rotation status` has data
    # to render. Rate-limit errors propagate so the caller can skip the whole
    # retailer; other failures are logged and don't break the map run.
    try:
        adapter = get_adapter(retailer)
        variants = adapter.fetch_variants(best.product_url)
    except RateLimitedError:
        raise
    except Exception as exc:
        click.echo(f"  ! variant fetch failed: {exc}", err=True)
        return
    _store_variants(db, shoe, retailer, variants)


def _store_variants(
    db: Database, shoe: CanonicalShoe, retailer: str, variants,
) -> None:
    shoe_repo = ShoeRepo(db)
    snap_repo = PriceSnapshotRepo(db)
    now = datetime.now(timezone.utc)
    for v in variants:
        variant = shoe_repo.upsert_variant(ShoeVariant(
            canonical_shoe_id=shoe.id,  # type: ignore[arg-type]
            size=v.size,
            width=v.width,
            colorway_name=v.colorway_name,
            colorway_code=v.colorway_code,
            mfr_style_code=v.mfr_style_code,
            image_url=v.image_url,
        ))
        snap_repo.insert(PriceSnapshot(
            shoe_variant_id=variant.id,  # type: ignore[arg-type]
            retailer=retailer,
            price_usd=v.price_usd,
            in_stock=v.in_stock,
            scraped_at=now,
            source_url=v.product_url,
        ))


def _current_min(
    db: Database, entry: WatchlistEntry, mappings: list[RetailerMapping],
) -> tuple[float, str, str] | None:
    """Return (price, retailer, colorway_name) for the cheapest in-stock variant
    matching the watchlist entry's size + width across all mapped retailers.

    Colorway policy is *not* applied here — that's the evaluator's job in
    chunk 5. Status just shows the cheapest matching variant so you can see
    what's out there.
    """
    conn = db._conn
    row = conn.execute(
        """
        SELECT ps.price_usd, ps.retailer, v.colorway_name
        FROM price_snapshots ps
        JOIN shoe_variants v ON v.id = ps.shoe_variant_id
        JOIN (
            SELECT shoe_variant_id, retailer, MAX(scraped_at) AS last_at
            FROM price_snapshots
            GROUP BY shoe_variant_id, retailer
        ) latest
          ON latest.shoe_variant_id = ps.shoe_variant_id
         AND latest.retailer = ps.retailer
         AND latest.last_at = ps.scraped_at
        WHERE v.canonical_shoe_id = ?
          AND v.size = ?
          AND v.width = ?
          AND ps.in_stock = 1
          AND ps.retailer IN ({placeholders})
        ORDER BY ps.price_usd ASC
        LIMIT 1
        """.format(placeholders=",".join("?" * len(mappings))),
        (entry.canonical_shoe_id, entry.size, entry.width, *[m.retailer for m in mappings]),
    ).fetchone()
    if not row:
        return None
    return float(row["price_usd"]), row["retailer"], row["colorway_name"]


def _write_mapping_review(
    path: Path,
    flagged: list[tuple[CanonicalShoe, str, MappingOutcome]],
) -> None:
    """Write `docs/mapping_review.md` so humans can audit the 0.6–0.9 band.

    In v2 this becomes a DB-backed admin UI (see plan.md). For v1 a plain
    markdown file checked into the repo is enough.
    """
    if not flagged:
        # No flagged entries: leave an empty file so the artifact still exists
        # (makes the "what needs review?" question trivially answerable).
        content = "# Mapping review\n\nNo mappings currently need review.\n"
    else:
        lines = ["# Mapping review", "", "Confidence 0.6–0.9 — eyeball these.", ""]
        for shoe, retailer, outcome in flagged:
            assert outcome.best is not None
            lines.append(
                f"- **{shoe.display_name}** → {retailer} "
                f"({outcome.confidence:.2f}) — {outcome.best.title}"
            )
            lines.append(f"  - {outcome.best.product_url}")
            for note in outcome.notes:
                lines.append(f"  - {note}")
        content = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _policy_label(policy: str, colorways: list[str]) -> str:
    if policy == "any":
        return "any colorway"
    if colorways:
        return f"{policy} {colorways}"
    return policy


if __name__ == "__main__":
    main()
