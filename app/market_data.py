from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from .models import Candle


log = logging.getLogger(__name__)

MAINNET_INFO = "https://api.hyperliquid.xyz/info"
TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
MINUTE_MS = 60_000


class HyperliquidMarketData:
    """Fetch 1m candles without hammering Hyperliquid's weighted REST limits.

    The strategy only acts on CLOSED one-minute candles, so there is no reason to
    download hundreds of candles every few seconds. We bootstrap history once,
    then request only a small incremental window at most once per minute.
    """

    def __init__(self, network: str, coin: str, interval: str = "1m", timeout: float = 15.0):
        self.url = MAINNET_INFO if network == "mainnet" else TESTNET_INFO
        self.coin = coin
        self.interval = interval
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: list[Candle] = []
        self._last_success_minute: Optional[int] = None
        self._retry_after_ms: int = 0

    def _request(self, start: int, end: int) -> list[Candle]:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": self.coin,
                "interval": self.interval,
                "startTime": start,
                "endTime": end,
            },
        }
        r = self.session.post(self.url, json=payload, timeout=self.timeout)

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "")
            try:
                retry_seconds = max(30.0, float(retry_after))
            except (TypeError, ValueError):
                retry_seconds = 60.0
            self._retry_after_ms = int(time.time() * 1000 + retry_seconds * 1000)
            raise RuntimeError(f"Hyperliquid REST rate limited (429); cooling down for {retry_seconds:.0f}s")

        r.raise_for_status()
        raw = r.json()
        candles = [
            Candle(
                t=int(x["t"]),
                T=int(x["T"]),
                o=float(x["o"]),
                h=float(x["h"]),
                l=float(x["l"]),
                c=float(x["c"]),
                v=float(x.get("v", 0.0)),
            )
            for x in raw
        ]
        candles.sort(key=lambda x: x.t)
        return candles

    @staticmethod
    def _closed(candles: list[Candle], now: int) -> list[Candle]:
        return [c for c in candles if c.T < now]

    def _bootstrap(self, count: int, now: int) -> list[Candle]:
        # Try increasingly wider windows only during startup. A small pause
        # prevents several weighted candleSnapshot calls from landing at once.
        windows = [8 * 60 * MINUTE_MS, 24 * 60 * MINUTE_MS, 72 * 60 * MINUTE_MS, 7 * 24 * 60 * MINUTE_MS]
        best: list[Candle] = []

        for i, window in enumerate(windows):
            candles = self._closed(self._request(now - window, now), now)
            if len(candles) > len(best):
                best = candles
            if len(best) >= count:
                break
            if i < len(windows) - 1:
                time.sleep(1.0)

        return best[-count:]

    def _incremental(self, count: int, now: int) -> list[Candle]:
        # Only ask for a few recent candles and merge them into the local cache.
        # This keeps candleSnapshot response weight tiny after bootstrap.
        start = max(self._cache[-1].t - 3 * MINUTE_MS, now - 10 * MINUTE_MS)
        fresh = self._closed(self._request(start, now), now)

        merged = {c.t: c for c in self._cache}
        for candle in fresh:
            merged[candle.t] = candle
        candles = sorted(merged.values(), key=lambda x: x.t)
        return candles[-count:]

    def fetch_recent(self, count: int = 260) -> list[Candle]:
        now = int(time.time() * 1000)
        minute_bucket = now // MINUTE_MS

        # After a 429, keep serving the last known closed bars while waiting.
        if now < self._retry_after_ms:
            if self._cache:
                return self._cache[-count:]
            wait_s = max(1, int((self._retry_after_ms - now) / 1000))
            raise RuntimeError(f"Hyperliquid REST cooldown active; retry in ~{wait_s}s")

        # No new closed 1m candle can exist within the same minute, so avoid
        # another REST request entirely.
        if self._cache and self._last_success_minute == minute_bucket:
            return self._cache[-count:]

        try:
            if not self._cache:
                new_cache = self._bootstrap(count, now)
            else:
                new_cache = self._incremental(count, now)
        except RuntimeError as exc:
            if "429" in str(exc) and self._cache:
                log.warning("%s; using cached candles", exc)
                return self._cache[-count:]
            raise

        if new_cache:
            self._cache = new_cache
            self._last_success_minute = minute_bucket
            self._retry_after_ms = 0

        return self._cache[-count:]
