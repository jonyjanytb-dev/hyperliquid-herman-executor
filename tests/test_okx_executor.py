from dataclasses import replace

import pytest

from app.executor import OKXExecutor
from app.okx_client import OKXAPIError
from tests.test_strategy import cfg as base_cfg


class FakeOKXClient:
    def __init__(self, pos_mode="net_mode"):
        self.pos_mode = pos_mode
        self.public_calls = []
        self.private_get_calls = []
        self.private_post_calls = []
        self.positions = []
        self.pending_algos = []

    def get_public(self, path, params):
        self.public_calls.append((path, params))
        if path == "/api/v5/public/instruments":
            return [{
                "instId": "BTC-USDT-SWAP",
                "instType": "SWAP",
                "state": "live",
                "ctType": "linear",
                "ctVal": "0.01",
                "ctValCcy": "BTC",
                "settleCcy": "USDT",
                "lotSz": "1",
                "minSz": "1",
                "tickSz": "0.1",
            }]
        if path == "/api/v5/market/ticker":
            return [{"bidPx": "49999", "askPx": "50001", "last": "50000"}]
        raise AssertionError(path)

    def get_private(self, path, params=None):
        self.private_get_calls.append((path, params or {}))
        if path == "/api/v5/account/config":
            return [{"posMode": self.pos_mode, "acctLv": "2"}]
        if path == "/api/v5/account/positions":
            return self.positions
        if path == "/api/v5/trade/order":
            return [{"state": "filled", "accFillSz": "2"}]
        if path == "/api/v5/trade/orders-algo-pending":
            return self.pending_algos
        raise AssertionError(path)

    def post_private(self, path, body):
        self.private_post_calls.append((path, body))
        if path == "/api/v5/trade/order":
            return [{"ordId": "600", "clOrdId": body["clOrdId"], "sCode": "0", "sMsg": ""}]
        if path == "/api/v5/trade/order-algo":
            return [{"algoId": "700", "algoClOrdId": body["algoClOrdId"], "sCode": "0", "sMsg": ""}]
        if path == "/api/v5/trade/amend-algos":
            return [{"algoId": body["algoId"], "sCode": "0", "sMsg": ""}]
        if path in {"/api/v5/trade/cancel-algos", "/api/v5/account/set-leverage"}:
            return [{"sCode": "0", "sMsg": ""}]
        raise AssertionError(path)


def okx_cfg(**changes):
    return replace(
        base_cfg(),
        exchange="okx",
        okx_inst_id="BTC-USDT-SWAP",
        okx_api_key="key",
        okx_secret_key="secret",
        okx_passphrase="pass",
        okx_margin_mode="cross",
        okx_trigger_price_type="last",
        **changes,
    )


def test_open_market_converts_quote_notional_to_contracts_and_uses_slippage_bounded_ioc():
    client = FakeOKXClient()
    client.positions = [{"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "net", "avgPx": "50010"}]
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    response = executor.open_market(True, 1000)

    path, body = client.private_post_calls[0]
    assert path == "/api/v5/trade/order"
    assert body["side"] == "buy"
    assert body["ordType"] == "ioc"
    assert body["sz"] == "2"
    assert body["px"] == "50250.0"
    assert body["tdMode"] == "cross"
    assert body["reduceOnly"] is False
    assert "posSide" not in body
    assert response["ordId"] == "600"


def test_place_protection_uses_one_oco_and_dynamic_tp_amends_it_in_place():
    client = FakeOKXClient()
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    tp_oid, sl_oid = executor.place_protection(2.0, 51000.04, 49000.06)
    updated_oid = executor.update_tp(2.0, tp_oid, 50950.04)

    place_path, place = client.private_post_calls[0]
    amend_path, amend = client.private_post_calls[1]
    assert place_path == "/api/v5/trade/order-algo"
    assert place["ordType"] == "oco"
    assert place["side"] == "sell"
    assert place["sz"] == "2"
    assert place["tpTriggerPx"] == "51000.0"
    assert place["tpOrdPx"] == "-1"
    assert place["slTriggerPx"] == "49000.1"
    assert place["slOrdPx"] == "-1"
    assert place["reduceOnly"] is True
    assert (tp_oid, sl_oid) == (700, 700)
    assert amend_path == "/api/v5/trade/amend-algos"
    assert amend == {
        "instId": "BTC-USDT-SWAP",
        "algoId": "700",
        "newTpTriggerPx": "50950.0",
        "newTpOrdPx": "-1",
        "newTpTriggerPxType": "last",
        "cxlOnFail": False,
    }
    assert updated_oid == 700


def test_hedge_mode_short_position_and_exit_orders_use_short_pos_side():
    client = FakeOKXClient(pos_mode="long_short_mode")
    client.positions = [{"instId": "BTC-USDT-SWAP", "pos": "3", "posSide": "short", "avgPx": "50100"}]
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    position = executor.position()
    executor.place_protection(position.size, 49000, 51000)

    _, body = client.private_post_calls[0]
    assert position.size == -3.0
    assert body["side"] == "buy"
    assert body["posSide"] == "short"
    assert "reduceOnly" not in body


def test_recover_protection_reads_oco_algo_id_and_trigger_prices():
    client = FakeOKXClient()
    client.pending_algos = [{
        "instId": "BTC-USDT-SWAP",
        "algoId": "888",
        "ordType": "oco",
        "reduceOnly": "true",
        "tpTriggerPx": "51000",
        "slTriggerPx": "49000",
        "cTime": "100",
    }]
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    assert executor.recover_protection() == (888, 51000.0, 888, 49000.0)


def test_position_refuses_to_manage_both_hedge_sides_at_once():
    client = FakeOKXClient(pos_mode="long_short_mode")
    client.positions = [
        {"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "long", "avgPx": "50000"},
        {"instId": "BTC-USDT-SWAP", "pos": "1", "posSide": "short", "avgPx": "50100"},
    ]
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="both LONG and SHORT"):
        executor.position()


def test_open_market_recovers_an_order_accepted_before_a_network_error():
    client = FakeOKXClient()
    original_get = client.get_private

    def fail_after_accepting(path, body):
        client.private_post_calls.append((path, body))
        raise OKXAPIError("OKX request failed: connection reset")

    def recover_by_client_id(path, params=None):
        if path == "/api/v5/trade/order" and params and params.get("clOrdId"):
            return [{"ordId": "601", "clOrdId": params["clOrdId"], "state": "filled"}]
        return original_get(path, params)

    client.post_private = fail_after_accepting
    client.get_private = recover_by_client_id
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    response = executor.open_market(True, 1000)

    assert response["ordId"] == "601"
    assert client.private_get_calls[-1] == (
        "/api/v5/trade/order",
        {"instId": "BTC-USDT-SWAP", "ordId": "601"},
    )


def test_place_protection_recovers_oco_accepted_before_a_network_error():
    client = FakeOKXClient()
    original_get = client.get_private
    recovery_queries = []

    def fail_after_accepting(path, body):
        client.private_post_calls.append((path, body))
        raise OKXAPIError("OKX request failed: timeout")

    def recover_by_client_id(path, params=None):
        if path == "/api/v5/trade/order-algo" and params and params.get("algoClOrdId"):
            recovery_queries.append((path, params))
            return [{"algoId": "701", "algoClOrdId": params["algoClOrdId"], "state": "live"}]
        return original_get(path, params)

    client.post_private = fail_after_accepting
    client.get_private = recover_by_client_id
    executor = OKXExecutor(okx_cfg(), client=client, sleep=lambda _: None)

    assert executor.place_protection(2.0, 51000, 49000) == (701, 701)
    assert len(recovery_queries) == 1
    recovery_path, recovery_params = recovery_queries[0]
    assert recovery_path == "/api/v5/trade/order-algo"
    assert set(recovery_params) == {"algoClOrdId"}
