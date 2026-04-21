"""Retailer adapters. Add new adapters by subclassing `RetailerAdapter`."""
from __future__ import annotations

from .base import RetailerAdapter, SearchResult, VariantPrice
from .running_warehouse import RunningWarehouseAdapter

ADAPTERS: dict[str, type[RetailerAdapter]] = {
    "running_warehouse": RunningWarehouseAdapter,
}


def get_adapter(name: str) -> RetailerAdapter:
    try:
        cls = ADAPTERS[name]
    except KeyError as e:
        raise KeyError(f"unknown retailer: {name!r}. known: {sorted(ADAPTERS)}") from e
    return cls()


__all__ = [
    "ADAPTERS",
    "RetailerAdapter",
    "RunningWarehouseAdapter",
    "SearchResult",
    "VariantPrice",
    "get_adapter",
]
