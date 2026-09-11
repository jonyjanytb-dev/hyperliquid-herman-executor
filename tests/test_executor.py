from types import SimpleNamespace

from app.executor import HyperliquidExecutor


class RecordingExchange:
    def __init__(self):
        self.calls = []

    def order(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        oid = 122 + len(self.calls)
        return {"response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}}

    def modify_order(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"response": {"data": {"statuses": [{"resting": {"oid": args[0]}}]}}}


def executor_with_recording_exchange(sz_decimals: int = 4):
    executor = object.__new__(HyperliquidExecutor)
    executor.cfg = SimpleNamespace(coin="xyz:XYZ100")
    executor.sz_decimals = sz_decimals
    executor.exchange = RecordingExchange()
    return executor


def test_place_protection_rounds_reconstructed_xyz100_levels_and_places_both_orders():
    executor = executor_with_recording_exchange(sz_decimals=4)

    tp_oid, sl_oid = executor.place_protection(-0.0342, 29115.1, 29324.0)

    calls = {args[4]["trigger"]["tpsl"]: (args, kwargs) for args, kwargs in executor.exchange.calls}
    tp_args, tp_kwargs = calls["tp"]
    sl_args, sl_kwargs = calls["sl"]
    assert (tp_oid, sl_oid) == (124, 123)
    assert tp_args[3] == 29115.0
    assert tp_args[4]["trigger"]["triggerPx"] == 29115.0
    assert sl_args[3] == 29324.0
    assert sl_args[4]["trigger"]["triggerPx"] == 29324.0
    assert tp_kwargs["reduce_only"] is True
    assert sl_kwargs["reduce_only"] is True


def test_trigger_order_keeps_integer_stop_price_valid():
    executor = executor_with_recording_exchange(sz_decimals=4)

    executor._trigger_order(-0.0342, 29324.0, "sl")

    args, _ = executor.exchange.calls[0]
    assert args[3] == 29324.0
    assert args[4]["trigger"]["triggerPx"] == 29324.0


def test_dynamic_tp_update_normalizes_price_before_modifying_order():
    executor = executor_with_recording_exchange(sz_decimals=4)

    oid = executor.update_tp(-0.0342, 456, 29115.1)

    args, kwargs = executor.exchange.calls[0]
    assert oid == 456
    assert args[0] == 456
    assert args[4] == 29115.0
    assert args[5]["trigger"]["triggerPx"] == 29115.0
    assert kwargs["reduce_only"] is True
