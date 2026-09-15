from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from .config import GatewayConfig
from .data import build_analytics, build_provider


class TTLCache:
    def __init__(self):
        self._items: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: str, ttl: float, loader: Callable[[], object]):
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item and item[0] > now:
                return item[1]

        value = loader()
        with self._lock:
            self._items[key] = (time.monotonic() + ttl, value)
        return value


def read_runtime_state(path: Path, stale_seconds: int) -> dict:
    if not path.exists():
        return {
            "exists": False,
            "health": "unknown",
            "last_processed_bar": None,
            "age_seconds": None,
            "managed_side": "FLAT",
        }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "exists": True,
            "health": "invalid",
            "error": str(exc),
            "last_processed_bar": None,
            "age_seconds": None,
            "managed_side": "UNKNOWN",
        }

    last_bar = raw.get("last_processed_bar")
    age = None
    if last_bar:
        age = max(0.0, time.time() - float(last_bar) / 1000.0)

    active_side = int(raw.get("active_side") or 0)
    managed_side = "LONG" if active_side > 0 else "SHORT" if active_side < 0 else "FLAT"
    if age is None:
        health = "unknown"
    elif age <= stale_seconds:
        health = "fresh"
    else:
        health = "stale"

    return {
        "exists": True,
        "health": health,
        "last_processed_bar": last_bar,
        "age_seconds": age,
        "last_entry_bar": raw.get("last_entry_bar"),
        "last_exit_bar": raw.get("last_exit_bar"),
        "managed_side": managed_side,
        "active_entry": raw.get("active_entry"),
        "active_sl": raw.get("active_sl"),
        "active_tp_at_entry": raw.get("active_tp_at_entry"),
        "tp_order_oid": raw.get("tp_order_oid"),
        "sl_order_oid": raw.get("sl_order_oid"),
    }


class GatewayService:
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.provider = build_provider(cfg)
        self.cache = TTLCache()

    def runtime(self) -> dict:
        return read_runtime_state(self.cfg.state_file, self.cfg.bot_stale_seconds)

    def overview(self) -> dict:
        runtime = self.runtime()
        try:
            exchange = self.cache.get_or_load(
                "overview",
                self.cfg.refresh_seconds,
                self.provider.overview,
            )
            exchange_error = None
        except Exception as exc:
            exchange = None
            exchange_error = str(exc)

        return {
            "server_time": int(time.time() * 1000),
            "config": self.cfg.public_config(),
            "runtime": runtime,
            "exchange": exchange,
            "exchange_error": exchange_error,
            "read_only": True,
        }

    def fills(self, days: int, limit: int = 200) -> list[dict]:
        days = max(1, min(days, 90))
        limit = max(1, min(limit, 500))
        key = f"fills:{days}:{limit}"
        return self.cache.get_or_load(
            key,
            max(30.0, self.cfg.refresh_seconds * 4),
            lambda: self.provider.fills(days, limit),
        )

    def analytics(self, days: int) -> dict:
        days = max(1, min(days, 90))
        key = f"analytics:{days}"
        return self.cache.get_or_load(
            key,
            max(45.0, self.cfg.refresh_seconds * 5),
            lambda: build_analytics(self.provider.fills(days, 500)),
        )
