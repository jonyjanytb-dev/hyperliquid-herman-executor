from gateway.data import build_analytics


def test_build_analytics_includes_opening_fees_and_closing_pnl():
    fills = [
        {
            "time": 1,
            "fee": 0.5,
            "realized_pnl": 0.0,
            "net_pnl": -0.5,
            "is_close": False,
        },
        {
            "time": 2,
            "fee": 0.5,
            "realized_pnl": 10.0,
            "net_pnl": 9.5,
            "is_close": True,
        },
        {
            "time": 3,
            "fee": 0.5,
            "realized_pnl": -4.0,
            "net_pnl": -4.5,
            "is_close": True,
        },
    ]
    result = build_analytics(fills)
    assert result["gross_realized_pnl"] == 6.0
    assert result["fees"] == 1.5
    assert result["net_realized_pnl"] == 4.5
    assert result["closed_fill_count"] == 2
    assert result["wins"] == 1
    assert result["losses"] == 1
    assert result["win_rate"] == 50.0
    assert round(result["profit_factor"], 6) == round(9.5 / 4.5, 6)
