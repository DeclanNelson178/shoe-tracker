"""Domain models. Mirror the v1 schema in plan.md so they carry into v2 unchanged."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Gender = Literal["mens", "womens", "unisex"]
VariantType = Literal["GTX", "Wide", "Trail"]
ColorwayPolicy = Literal["any", "allowlist", "denylist"]
Channel = Literal["email", "pushover", "ntfy"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class User(_Frozen):
    id: str = "me"
    email: str
    created_at: datetime | None = None


class CanonicalShoe(_Frozen):
    """A shoe as a model, regardless of size/colorway variant."""
    id: int | None = None
    brand: str
    model: str
    version: str | None = None
    gender: Gender
    variant_type: VariantType | None = None
    mfr_style_prefix: str | None = None

    @field_validator("brand", "model")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @property
    def display_name(self) -> str:
        parts = [self.brand, self.model]
        if self.version:
            parts.append(str(self.version))
        if self.variant_type:
            parts.append(self.variant_type)
        return " ".join(parts)


class ShoeVariant(_Frozen):
    """A specific size + colorway + width combination."""
    id: int | None = None
    canonical_shoe_id: int
    size: float
    width: str = "D"
    colorway_name: str
    colorway_code: str | None = None
    mfr_style_code: str | None = None
    image_url: str | None = None


class WatchlistEntry(_Frozen):
    id: int | None = None
    user_id: str = "me"
    canonical_shoe_id: int
    size: float
    width: str = "D"
    colorway_policy: ColorwayPolicy = "any"
    colorway_list: list[str] = Field(default_factory=list)
    threshold_usd: float
    active: bool = True
    created_at: datetime | None = None

    @field_validator("threshold_usd")
    @classmethod
    def _positive_threshold(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("threshold must be positive")
        return v

    @field_validator("colorway_list")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


class RetailerMapping(_Frozen):
    canonical_shoe_id: int
    retailer: str
    product_url: str
    product_id: str | None = None
    confidence: float
    last_verified_at: datetime | None = None


class PriceSnapshot(_Frozen):
    id: int | None = None
    shoe_variant_id: int
    retailer: str
    price_usd: float
    in_stock: bool
    scraped_at: datetime
    source_url: str


class NotificationRecord(_Frozen):
    id: int | None = None
    user_id: str = "me"
    shoe_variant_id: int
    retailer: str
    triggering_price: float
    sent_at: datetime
    channel: Channel = "email"


class RotationShoe(_Frozen):
    """One entry in rotation.yaml — before it gets split into canonical_shoe + watchlist rows."""
    brand: str
    model: str
    version: str | None = None
    gender: Gender
    variant_type: VariantType | None = None
    size: float
    width: str = "D"
    colorway_policy: ColorwayPolicy = "any"
    colorway_list: list[str] = Field(default_factory=list)
    threshold_usd: float
    mfr_style_prefix: str | None = None

    @field_validator("threshold_usd")
    @classmethod
    def _positive_threshold(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("threshold must be positive")
        return v


class RotationConfig(_Frozen):
    user_email: str
    shoes: list[RotationShoe]
