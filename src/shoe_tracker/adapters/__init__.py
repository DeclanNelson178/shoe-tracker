"""Retailer adapters. Add new adapters by subclassing `RetailerAdapter`."""
from __future__ import annotations

from .base import RetailerAdapter, SearchResult, VariantPrice
from .holabird import HolabirdAdapter
from .jackrabbit import JackrabbitAdapter
from .road_runner_sports import RoadRunnerSportsAdapter
from .running_warehouse import RunningWarehouseAdapter

ADAPTERS: dict[str, type[RetailerAdapter]] = {
    "running_warehouse": RunningWarehouseAdapter,
    "road_runner_sports": RoadRunnerSportsAdapter,
    "holabird": HolabirdAdapter,
    "jackrabbit": JackrabbitAdapter,
}


def get_adapter(name: str) -> RetailerAdapter:
    try:
        cls = ADAPTERS[name]
    except KeyError as e:
        raise KeyError(f"unknown retailer: {name!r}. known: {sorted(ADAPTERS)}") from e
    return cls()


__all__ = [
    "ADAPTERS",
    "HolabirdAdapter",
    "JackrabbitAdapter",
    "RetailerAdapter",
    "RoadRunnerSportsAdapter",
    "RunningWarehouseAdapter",
    "SearchResult",
    "VariantPrice",
    "get_adapter",
]
