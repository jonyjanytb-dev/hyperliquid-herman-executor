from __future__ import annotations

import time
import requests
from .models import Candle


MAINNET_INFO = "https://api.hyperliquid.xyz/info"
TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"


class HyperliquidMarketData:
    def __init__(self, network: str, coin: str, interval: str = "1m", timeout: float = 10.0):
        self.url = MAINNET_INFO if network == "mainnet" else TESTNET_INFO
        self.coin = coin
        self.interval = interval
        self.timeout = timeout
        self.session = requests.Session()

    def fetch_recent(self, count: int = 260) -> list[Candle]:
        now = int(time.time() * 1000)
        # Normal case: only request a modest window. If the market has a long
        # pause (overnight/weekend) and that does not contain enough actual
        # candles, widen progressively only as needed.
        windows = [max(count + 30, 360) * 60_000, 24 * 60 * 60_000, 72 * 60 * 60_000, 5 * 24 * 60 * 60_000]
        best: list[Candle] = []
        for window in windows:
            start = now - window
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": self.coin,
                    "interval": self.interval,
                    "startTime": start,
                    "endTime": now,
                },
            }
            r = self.session.post(self.url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            raw = r.json()
            candles = [
                Candle(
                    t=int(x["t"]), T=int(x["T"]),
                    o=float(x["o"]), h=float(x["h"]), l=float(x["l"]),
                    c=float(x["c"]), v=float(x.get("v", 0.0)),
                )
                for x in raw
            ]
            candles.sort(key=lambda x: x.t)
            best = [c for c in candles if c.T < now]
            if len(best) >= count:
                break
        return best[-count:]
