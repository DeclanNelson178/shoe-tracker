"""SQLite storage + repository classes.

All SQL lives here so v2's Postgres migration is a driver swap. Do NOT write
raw SQL elsewhere in the codebase.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterable, Iterator

from ..models import (
    CanonicalShoe,
    NotificationRecord,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
    User,
    WatchlistEntry,
)

DEFAULT_DB_PATH = Path("shoe_tracker.db")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migration_files() -> list[tuple[int, str]]:
    migrations_pkg = resources.files("shoe_tracker.db").joinpath("migrations")
    out: list[tuple[int, str]] = []
    for entry in migrations_pkg.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version = int(name.split("_", 1)[0])
        out.append((version, entry.read_text()))
    out.sort(key=lambda x: x[0])
    return out


def init_db(db_path: Path = DEFAULT_DB_PATH) -> Path:
    """Create the DB file and apply any pending migrations. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_version").fetchall()
        }
        for version, sql in _migration_files():
            if version in applied:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _utcnow_iso()),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


class Database:
    """Connection holder. Repositories take a Database instance."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(db_path)
        self._conn = _connect(self.path)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class UserRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, user: User) -> User:
        created = user.created_at.isoformat() if user.created_at else _utcnow_iso()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET email=excluded.email",
                (user.id, user.email, created),
            )
        return self.get(user.id)  # type: ignore[return-value]

    def get(self, user_id: str) -> User | None:
        row = self.db._conn.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return User(id=row["id"], email=row["email"], created_at=_parse_dt(row["created_at"]))


class ShoeRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert_canonical(self, shoe: CanonicalShoe) -> CanonicalShoe:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO canonical_shoes (brand, model, version, gender, variant_type, mfr_style_prefix) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(brand, model, version, gender, variant_type) DO UPDATE SET "
                "mfr_style_prefix=COALESCE(excluded.mfr_style_prefix, canonical_shoes.mfr_style_prefix)",
                (
                    shoe.brand, shoe.model, shoe.version, shoe.gender,
                    shoe.variant_type, shoe.mfr_style_prefix,
                ),
            )
        found = self.find_canonical(
            brand=shoe.brand, model=shoe.model, version=shoe.version,
            gender=shoe.gender, variant_type=shoe.variant_type,
        )
        assert found is not None
        return found

    def find_canonical(
        self, *, brand: str, model: str, version: str | None,
        gender: str, variant_type: str | None,
    ) -> CanonicalShoe | None:
        row = self.db._conn.execute(
            "SELECT * FROM canonical_shoes "
            "WHERE brand=? AND model=? AND gender=? "
            "AND (version IS ? OR version=?) "
            "AND (variant_type IS ? OR variant_type=?)",
            (brand, model, gender, version, version, variant_type, variant_type),
        ).fetchone()
        if not row:
            return None
        return _row_to_canonical(row)

    def list_canonical(self) -> list[CanonicalShoe]:
        rows = self.db._conn.execute(
            "SELECT * FROM canonical_shoes ORDER BY brand, model, version"
        ).fetchall()
        return [_row_to_canonical(r) for r in rows]

    def list_variants_by_ids(self, ids: Iterable[int]) -> list[ShoeVariant]:
        """Bulk-fetch variants by id. Used by the dashboard's alert-history
        section, which needs a variant per notification record. Returns rows
        in arbitrary order; callers re-key by id."""
        ids = list(ids)
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.db._conn.execute(
            f"SELECT * FROM shoe_variants WHERE id IN ({placeholders})", tuple(ids),
        ).fetchall()
        return [_row_to_variant(r) for r in rows]

    def upsert_variant(self, v: ShoeVariant) -> ShoeVariant:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO shoe_variants (canonical_shoe_id, size, width, colorway_name, "
                "colorway_code, mfr_style_code, image_url) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_shoe_id, size, width, colorway_name) DO UPDATE SET "
                "colorway_code=COALESCE(excluded.colorway_code, shoe_variants.colorway_code), "
                "mfr_style_code=COALESCE(excluded.mfr_style_code, shoe_variants.mfr_style_code), "
                "image_url=COALESCE(excluded.image_url, shoe_variants.image_url)",
                (
                    v.canonical_shoe_id, v.size, v.width, v.colorway_name,
                    v.colorway_code, v.mfr_style_code, v.image_url,
                ),
            )
        row = self.db._conn.execute(
            "SELECT * FROM shoe_variants "
            "WHERE canonical_shoe_id=? AND size=? AND width=? AND colorway_name=?",
            (v.canonical_shoe_id, v.size, v.width, v.colorway_name),
        ).fetchone()
        return _row_to_variant(row)


class WatchlistRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, entry: WatchlistEntry) -> WatchlistEntry:
        created = entry.created_at.isoformat() if entry.created_at else _utcnow_iso()
        colorway_list_json = json.dumps(entry.colorway_list)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO watchlist (user_id, canonical_shoe_id, size, width, "
                "colorway_policy, colorway_list, threshold_usd, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, canonical_shoe_id, size, width) DO UPDATE SET "
                "colorway_policy=excluded.colorway_policy, "
                "colorway_list=excluded.colorway_list, "
                "threshold_usd=excluded.threshold_usd, "
                "active=excluded.active",
                (
                    entry.user_id, entry.canonical_shoe_id, entry.size, entry.width,
                    entry.colorway_policy, colorway_list_json, entry.threshold_usd,
                    1 if entry.active else 0, created,
                ),
            )
        row = self.db._conn.execute(
            "SELECT * FROM watchlist WHERE user_id=? AND canonical_shoe_id=? AND size=? AND width=?",
            (entry.user_id, entry.canonical_shoe_id, entry.size, entry.width),
        ).fetchone()
        return _row_to_watchlist(row)

    def list_for_user(self, user_id: str = "me", only_active: bool = True) -> list[WatchlistEntry]:
        sql = "SELECT * FROM watchlist WHERE user_id=?"
        if only_active:
            sql += " AND active=1"
        sql += " ORDER BY id"
        rows = self.db._conn.execute(sql, (user_id,)).fetchall()
        return [_row_to_watchlist(r) for r in rows]


class RetailerMappingRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, m: RetailerMapping) -> RetailerMapping:
        verified = m.last_verified_at.isoformat() if m.last_verified_at else _utcnow_iso()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO retailer_mappings (canonical_shoe_id, retailer, product_url, "
                "product_id, confidence, last_verified_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_shoe_id, retailer) DO UPDATE SET "
                "product_url=excluded.product_url, product_id=excluded.product_id, "
                "confidence=excluded.confidence, last_verified_at=excluded.last_verified_at",
                (
                    m.canonical_shoe_id, m.retailer, m.product_url,
                    m.product_id, m.confidence, verified,
                ),
            )
        return self.get(m.canonical_shoe_id, m.retailer)  # type: ignore[return-value]

    def get(self, canonical_shoe_id: int, retailer: str) -> RetailerMapping | None:
        row = self.db._conn.execute(
            "SELECT * FROM retailer_mappings WHERE canonical_shoe_id=? AND retailer=?",
            (canonical_shoe_id, retailer),
        ).fetchone()
        if not row:
            return None
        return _row_to_mapping(row)

    def list_for_shoe(self, canonical_shoe_id: int) -> list[RetailerMapping]:
        rows = self.db._conn.execute(
            "SELECT * FROM retailer_mappings WHERE canonical_shoe_id=? ORDER BY retailer",
            (canonical_shoe_id,),
        ).fetchall()
        return [_row_to_mapping(r) for r in rows]


class PriceSnapshotRepo:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, s: PriceSnapshot) -> PriceSnapshot:
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO price_snapshots (shoe_variant_id, retailer, price_usd, "
                "in_stock, scraped_at, source_url) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    s.shoe_variant_id, s.retailer, s.price_usd,
                    1 if s.in_stock else 0, s.scraped_at.isoformat(), s.source_url,
                ),
            )
            new_id = cur.lastrowid
        return s.model_copy(update={"id": new_id})

    def insert_many(self, snapshots: Iterable[PriceSnapshot]) -> int:
        count = 0
        with self.db.tx() as c:
            for s in snapshots:
                c.execute(
                    "INSERT INTO price_snapshots (shoe_variant_id, retailer, price_usd, "
                    "in_stock, scraped_at, source_url) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        s.shoe_variant_id, s.retailer, s.price_usd,
                        1 if s.in_stock else 0, s.scraped_at.isoformat(), s.source_url,
                    ),
                )
                count += 1
        return count

    def latest_for_variant(self, shoe_variant_id: int) -> PriceSnapshot | None:
        row = self.db._conn.execute(
            "SELECT * FROM price_snapshots WHERE shoe_variant_id=? ORDER BY scraped_at DESC LIMIT 1",
            (shoe_variant_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(row)

    def latest_variants_with_prices(
        self, *, canonical_shoe_id: int, size: float, width: str,
        retailers: list[str],
    ) -> list[tuple[ShoeVariant, PriceSnapshot]]:
        """Latest snapshot per (variant, retailer) for one size+width of a shoe.

        Returns one row per (variant, retailer) pair that has any snapshot in the
        supplied retailers, picking the most recent snapshot for that pair.
        Callers (the evaluator) apply in-stock, colorway, and threshold filters.
        """
        if not retailers:
            return []
        placeholders = ",".join("?" * len(retailers))
        rows = self.db._conn.execute(
            f"""
            SELECT
              v.id AS v_id,
              v.canonical_shoe_id AS v_canonical_shoe_id,
              v.size AS v_size, v.width AS v_width,
              v.colorway_name AS v_colorway_name,
              v.colorway_code AS v_colorway_code,
              v.mfr_style_code AS v_mfr_style_code,
              v.image_url AS v_image_url,
              ps.id AS ps_id, ps.retailer AS ps_retailer,
              ps.price_usd AS ps_price_usd, ps.in_stock AS ps_in_stock,
              ps.scraped_at AS ps_scraped_at, ps.source_url AS ps_source_url
            FROM shoe_variants v
            JOIN price_snapshots ps ON ps.shoe_variant_id = v.id
            JOIN (
                SELECT shoe_variant_id, retailer, MAX(scraped_at) AS last_at
                FROM price_snapshots
                WHERE retailer IN ({placeholders})
                GROUP BY shoe_variant_id, retailer
            ) latest
              ON latest.shoe_variant_id = ps.shoe_variant_id
             AND latest.retailer = ps.retailer
             AND latest.last_at = ps.scraped_at
            WHERE v.canonical_shoe_id = ?
              AND v.size = ?
              AND v.width = ?
            """,
            (*retailers, canonical_shoe_id, size, width),
        ).fetchall()
        out: list[tuple[ShoeVariant, PriceSnapshot]] = []
        for row in rows:
            variant = ShoeVariant(
                id=row["v_id"],
                canonical_shoe_id=row["v_canonical_shoe_id"],
                size=row["v_size"], width=row["v_width"],
                colorway_name=row["v_colorway_name"],
                colorway_code=row["v_colorway_code"],
                mfr_style_code=row["v_mfr_style_code"],
                image_url=row["v_image_url"],
            )
            snap = PriceSnapshot(
                id=row["ps_id"],
                shoe_variant_id=row["v_id"],
                retailer=row["ps_retailer"],
                price_usd=row["ps_price_usd"],
                in_stock=bool(row["ps_in_stock"]),
                scraped_at=_parse_dt(row["ps_scraped_at"]),
                source_url=row["ps_source_url"],
            )
            out.append((variant, snap))
        return out


class NotificationRepo:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, n: NotificationRecord) -> NotificationRecord:
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO notifications_sent (user_id, shoe_variant_id, retailer, "
                "triggering_price, sent_at, channel) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    n.user_id, n.shoe_variant_id, n.retailer,
                    n.triggering_price, n.sent_at.isoformat(), n.channel,
                ),
            )
            new_id = cur.lastrowid
        return n.model_copy(update={"id": new_id})

    def last_sent_at(
        self, *, user_id: str, shoe_variant_id: int, retailer: str
    ) -> datetime | None:
        row = self.db._conn.execute(
            "SELECT sent_at FROM notifications_sent "
            "WHERE user_id=? AND shoe_variant_id=? AND retailer=? "
            "ORDER BY sent_at DESC LIMIT 1",
            (user_id, shoe_variant_id, retailer),
        ).fetchone()
        if not row:
            return None
        return _parse_dt(row["sent_at"])

    def list_recent_for_user(
        self, *, user_id: str, since: datetime,
    ) -> list[NotificationRecord]:
        """Notifications a user received at or after `since`, newest first.

        Used by the dashboard to render the last 30 days of alert history.
        """
        rows = self.db._conn.execute(
            "SELECT id, user_id, shoe_variant_id, retailer, triggering_price, "
            "sent_at, channel FROM notifications_sent "
            "WHERE user_id=? AND sent_at >= ? "
            "ORDER BY sent_at DESC",
            (user_id, since.isoformat()),
        ).fetchall()
        return [
            NotificationRecord(
                id=r["id"],
                user_id=r["user_id"],
                shoe_variant_id=r["shoe_variant_id"],
                retailer=r["retailer"],
                triggering_price=r["triggering_price"],
                sent_at=_parse_dt(r["sent_at"]),
                channel=r["channel"],
            )
            for r in rows
        ]


# --- row adapters ---

def _row_to_canonical(row: sqlite3.Row) -> CanonicalShoe:
    return CanonicalShoe(
        id=row["id"],
        brand=row["brand"],
        model=row["model"],
        version=row["version"],
        gender=row["gender"],
        variant_type=row["variant_type"],
        mfr_style_prefix=row["mfr_style_prefix"],
    )


def _row_to_variant(row: sqlite3.Row) -> ShoeVariant:
    return ShoeVariant(
        id=row["id"],
        canonical_shoe_id=row["canonical_shoe_id"],
        size=row["size"],
        width=row["width"],
        colorway_name=row["colorway_name"],
        colorway_code=row["colorway_code"],
        mfr_style_code=row["mfr_style_code"],
        image_url=row["image_url"],
    )


def _row_to_watchlist(row: sqlite3.Row) -> WatchlistEntry:
    return WatchlistEntry(
        id=row["id"],
        user_id=row["user_id"],
        canonical_shoe_id=row["canonical_shoe_id"],
        size=row["size"],
        width=row["width"],
        colorway_policy=row["colorway_policy"],
        colorway_list=json.loads(row["colorway_list"] or "[]"),
        threshold_usd=row["threshold_usd"],
        active=bool(row["active"]),
        created_at=_parse_dt(row["created_at"]),
    )


def _row_to_mapping(row: sqlite3.Row) -> RetailerMapping:
    return RetailerMapping(
        canonical_shoe_id=row["canonical_shoe_id"],
        retailer=row["retailer"],
        product_url=row["product_url"],
        product_id=row["product_id"],
        confidence=row["confidence"],
        last_verified_at=_parse_dt(row["last_verified_at"]),
    )


def _row_to_snapshot(row: sqlite3.Row) -> PriceSnapshot:
    return PriceSnapshot(
        id=row["id"],
        shoe_variant_id=row["shoe_variant_id"],
        retailer=row["retailer"],
        price_usd=row["price_usd"],
        in_stock=bool(row["in_stock"]),
        scraped_at=_parse_dt(row["scraped_at"]),
        source_url=row["source_url"],
    )
