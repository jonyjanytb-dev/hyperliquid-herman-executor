from app.market_data import OKXMarketData


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_public(self, path, params):
        self.calls.append((path, params))
        return self.rows


def test_okx_market_data_keeps_only_confirmed_candles_and_sorts_oldest_first():
    client = FakeClient([
        ["120000", "12", "13", "11", "12.5", "3", "0", "0", "0"],
        ["60000", "10", "12", "9", "11", "2", "0", "0", "1"],
        ["0", "9", "10", "8", "10", "1", "0", "0", "1"],
    ])
    market = OKXMarketData(
        "BTC-USDT-SWAP",
        client=client,
        clock_ms=lambda: 130_000,
    )

    candles = market.fetch_recent(260)
    cached = market.fetch_recent(260)

    assert [c.t for c in candles] == [0, 60_000]
    assert candles[-1].T == 119_999
    assert candles[-1].c == 11.0
    assert cached == candles
    assert client.calls == [
        ("/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "1m", "limit": "260"})
    ]


def test_okx_market_data_retries_when_the_just_closed_candle_is_not_confirmed_yet():
    client = FakeClient([
        ["120000", "12", "13", "11", "12.5", "3", "0", "0", "0"],
        ["60000", "10", "12", "9", "11", "2", "0", "0", "1"],
    ])
    market = OKXMarketData(
        "BTC-USDT-SWAP",
        client=client,
        clock_ms=lambda: 180_000,
    )

    market.fetch_recent(260)
    market.fetch_recent(260)

    assert len(client.calls) == 2
