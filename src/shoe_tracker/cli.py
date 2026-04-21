"""Command-line entry point. Stubs for later chunks live here too."""
from __future__ import annotations

from pathlib import Path

import click

from . import config as config_mod
from .adapters import ADAPTERS, VariantPrice, get_adapter
from .db import (
    Database,
    RetailerMappingRepo,
    ShoeRepo,
    UserRepo,
    WatchlistRepo,
    init_db as run_init_db,
    DEFAULT_DB_PATH,
)
from .models import CanonicalShoe, User, WatchlistEntry, RotationConfig


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
@click.option("--retailer")
@click.option("--all", "all_retailers", is_flag=True)
def rotation_map(retailer: str | None, all_retailers: bool) -> None:
    """Resolve per-retailer product URLs. (implemented in chunk 3)"""
    raise click.ClickException("not implemented yet — see chunk 3 in plan.md")


@rotation.command("status")
def rotation_status() -> None:
    """Show current minimum price per watchlist entry. (implemented in chunk 3)"""
    raise click.ClickException("not implemented yet — see chunk 3 in plan.md")


@rotation.command("set-threshold")
@click.argument("shoe")
@click.argument("threshold", type=float)
def rotation_set_threshold(shoe: str, threshold: float) -> None:
    """Update threshold on a watchlist entry. (implemented in chunk 5)"""
    raise click.ClickException("not implemented yet — see chunk 5 in plan.md")


@rotation.command("evaluate")
def rotation_evaluate() -> None:
    """Run the evaluator + notifier. (implemented in chunk 5)"""
    raise click.ClickException("not implemented yet — see chunk 5 in plan.md")


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


def _policy_label(policy: str, colorways: list[str]) -> str:
    if policy == "any":
        return "any colorway"
    if colorways:
        return f"{policy} {colorways}"
    return policy


if __name__ == "__main__":
    main()
