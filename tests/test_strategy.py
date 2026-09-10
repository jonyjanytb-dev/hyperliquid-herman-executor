from dataclasses import replace
from app.config import Config
from app.models import Candle, RuntimeState
from app.strategy import HermanTrendRebalanceStrategy


def cfg():
    return Config(
        dry_run=True, network="mainnet", dex="xyz", coin="xyz:XYZ100", interval="1m",
        account_address="", api_private_key="", order_notional_usdc=100, leverage=0,
        max_slippage=0.005,
        sma50_length=50, sma200_length=200, point_size=1.0, min_separation=30,
        enable_longs=True, enable_shorts=True, tp_mode="200 SMA",
        sma_target_behaviour="Dynamic", tp_fixed_points=100,
        sl_mode="Fixed Points", sl_fixed_points=125,
        poll_seconds=3, lookback_candles=260, state_path="runtime/state.json", log_level="INFO",
    )


def candles_from_closes(closes):
    out=[]
    for i,c in enumerate(closes):
        t=i*60_000
        out.append(Candle(t=t,T=t+59_999,o=c,h=c,l=c,c=c,v=1))
    return out


def test_no_signal_without_201_bars():
    s=HermanTrendRebalanceStrategy(cfg())
    assert s.evaluate(candles_from_closes([100.0]*200), RuntimeState(), True) is None


def test_fixed_point_long_levels_when_signal_forced_by_simple_lengths():
    c = replace(
        cfg(), sma50_length=2, sma200_length=3, min_separation=0.0,
        tp_mode="Fixed Points", tp_fixed_points=10,
        sl_mode="Fixed Points", sl_fixed_points=5,
    )
    s=HermanTrendRebalanceStrategy(c)
    # At final bar: prev close 90 <= prev sma2 95; current close 101 > current sma2 95.5.
    # current sma2=95.5 < sma3=97 => long direction.
    bars=candles_from_closes([100,100,90,101])
    sig=s.evaluate(bars, RuntimeState(), True)
    assert sig is not None and sig.side=="LONG"
    assert sig.initial_tp==111
    assert sig.sl==96


def test_opposite_signal_ignored_while_position_open():
    c = replace(cfg(), sma50_length=2, sma200_length=3, min_separation=0.0)
    s=HermanTrendRebalanceStrategy(c)
    bars=candles_from_closes([100,100,90,101])
    assert s.evaluate(bars, RuntimeState(active_side=1), False) is None
