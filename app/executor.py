from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Callable, Optional

from .config import Config
from .okx_client import OKXAPIError, OKXClient

log = logging.getLogger(__name__)

PERP_MAX_DECIMALS = 6
PRICE_SIGNIFICANT_FIGURES = 5


@dataclass(frozen=True)
class Position:
    size: float
    entry_px: Optional[float]

    @property
    def flat(self) -> bool:
        return abs(self.size) < 1e-15


class BaseExecutor:
    def position(self) -> Position:
        raise NotImplementedError

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        raise NotImplementedError

    def place_protection(self, position_size: float, tp: float, sl: float) -> tuple[Optional[int], Optional[int]]:
        raise NotImplementedError

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float) -> Optional[int]:
        raise NotImplementedError

    def cancel_oid(self, oid: Optional[int]) -> None:
        raise NotImplementedError

    def cancel_all_protection(self) -> None:
        raise NotImplementedError

    def recover_protection(self) -> tuple[Optional[int], Optional[float], Optional[int], Optional[float]]:
        return None, None, None, None


class DryRunExecutor(BaseExecutor):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._position = Position(0.0, None)
        self._oid = 1000
        self._tp = None
        self._sl = None
        self._tp_oid = None
        self._sl_oid = None
        self.last_mid = 25_000.0

    def set_mid(self, px: float) -> None:
        self.last_mid = px

    def position(self) -> Position:
        return self._position

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        size = notional_usdc / self.last_mid
        self._position = Position(size if is_buy else -size, self.last_mid)
        log.info("DRY RUN open %s size=%.8f px=%.4f", "LONG" if is_buy else "SHORT", abs(size), self.last_mid)
        return {"status": "ok", "dry_run": True}

    def _next_oid(self) -> int:
        self._oid += 1
        return self._oid

    def place_protection(self, position_size: float, tp: float, sl: float):
        self._tp, self._sl = tp, sl
        self._sl_oid, self._tp_oid = self._next_oid(), self._next_oid()
        log.info("DRY RUN protection SL=%.4f TP=%.4f", sl, tp)
        return self._tp_oid, self._sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        self._tp = tp
        if old_tp_oid is None:
            self._tp_oid = self._next_oid()
        else:
            self._tp_oid = old_tp_oid
        log.info("DRY RUN update TP=%.4f", tp)
        return self._tp_oid

    def cancel_oid(self, oid: Optional[int]) -> None:
        return

    def cancel_all_protection(self) -> None:
        self._tp_oid = self._sl_oid = None
        self._tp = self._sl = None


class HyperliquidExecutor(BaseExecutor):
    def __init__(self, cfg: Config):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        self.cfg = cfg
        base_url = constants.MAINNET_API_URL if cfg.network == "mainnet" else constants.TESTNET_API_URL
        wallet = Account.from_key(cfg.api_private_key)
        self.info = Info(base_url, skip_ws=True, perp_dexs=[cfg.dex])
        self.exchange = Exchange(
            wallet,
            base_url,
            account_address=cfg.account_address,
            perp_dexs=[cfg.dex],
        )
        self.market_meta = self._validate_market()
        self.sz_decimals = int(self.market_meta["szDecimals"])
        if cfg.leverage > 0:
            max_lev = int(self.market_meta.get("maxLeverage", cfg.leverage))
            if cfg.leverage > max_lev:
                raise RuntimeError(f"LEVERAGE={cfg.leverage} exceeds market maxLeverage={max_lev}")
            margin_mode = str(self.market_meta.get("marginMode", ""))
            only_isolated = bool(self.market_meta.get("onlyIsolated", False)) or margin_mode in {"strictIsolated", "noCross"}
            self.exchange.update_leverage(cfg.leverage, cfg.coin, is_cross=not only_isolated)

    def _validate_market(self) -> dict:
        meta = self.info.meta(dex=self.cfg.dex)
        for item in meta.get("universe", []):
            if item.get("name") == self.cfg.coin:
                return item
        raise RuntimeError(f"{self.cfg.coin} not found in HIP-3 dex {self.cfg.dex}")

    def _round_size(self, size: float) -> float:
        q = Decimal("1").scaleb(-self.sz_decimals)
        rounded = Decimal(str(size)).quantize(q, rounding=ROUND_DOWN)
        return float(rounded)

    def _round_price(self, price: float) -> float:
        """Normalize a perp price to Hyperliquid's accepted tick precision."""
        significant = float(f"{price:.{PRICE_SIGNIFICANT_FIGURES}g}")
        return round(significant, PERP_MAX_DECIMALS - self.sz_decimals)

    def _mids(self) -> dict:
        return self.info.all_mids(self.cfg.dex)

    def mid(self) -> float:
        mids = self._mids()
        return float(mids[self.cfg.coin])

    def position(self) -> Position:
        state = self.info.user_state(self.cfg.account_address, self.cfg.dex)
        for wrapper in state.get("assetPositions", []):
            p = wrapper.get("position", {})
            if p.get("coin") == self.cfg.coin:
                szi = float(p.get("szi", 0.0))
                entry = p.get("entryPx")
                return Position(szi, float(entry) if entry not in (None, "") else None)
        return Position(0.0, None)

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        mid = self.mid()
        size = self._round_size(notional_usdc / mid)
        if size <= 0:
            raise RuntimeError("Calculated order size rounded to zero; increase ORDER_NOTIONAL_USDC")
        return self.exchange.market_open(
            self.cfg.coin,
            is_buy,
            size,
            slippage=self.cfg.max_slippage,
        )

    @staticmethod
    def _extract_oid(resp: dict) -> Optional[int]:
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "resting" in status:
                return int(status["resting"]["oid"])
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_error(resp: dict) -> Optional[str]:
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "error" in status:
                return str(status["error"])
        except Exception:
            pass
        return None

    def _trigger_payload(self, position_size: float, trigger_px: float, kind: str):
        is_buy = position_size < 0
        size = self._round_size(abs(position_size))
        if size <= 0:
            raise RuntimeError("Position size rounded to zero while creating protection")
        trigger_px = self._round_price(trigger_px)
        order_type = {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": kind}}
        return is_buy, size, trigger_px, order_type

    def _trigger_order(self, position_size: float, trigger_px: float, kind: str) -> Optional[int]:
        is_buy, size, trigger_px, order_type = self._trigger_payload(position_size, trigger_px, kind)
        resp = self.exchange.order(
            self.cfg.coin,
            is_buy,
            size,
            trigger_px,
            order_type,
            reduce_only=True,
        )
        error = self._extract_error(resp)
        if error:
            raise RuntimeError(f"Failed to create {kind.upper()} trigger order: {error}")
        oid = self._extract_oid(resp)
        if oid is None:
            raise RuntimeError(f"Failed to create {kind.upper()} trigger order: {resp}")
        log.info("%s trigger placed oid=%s px=%s size=%s", kind.upper(), oid, trigger_px, size)
        return oid

    def place_protection(self, position_size: float, tp: float, sl: float):
        # Protect downside first. If TP placement fails, keep the SL alive so the
        # live position is never deliberately left without a stop.
        sl_oid = self._trigger_order(position_size, sl, "sl")
        try:
            tp_oid = self._trigger_order(position_size, tp, "tp")
        except Exception:
            log.exception("TP placement failed after SL was placed; keeping SL oid=%s", sl_oid)
            raise
        return tp_oid, sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        """Move Dynamic TP without first deleting the working TP.

        Preferred path is an in-place Hyperliquid order modification. If the
        previous TP no longer exists, create the replacement first and only then
        try to cancel the stale oid. This avoids the old cancel-then-create gap.
        """
        if old_tp_oid is None:
            oid = self._trigger_order(position_size, tp, "tp")
            log.info("Dynamic TP created oid=%s px=%.4f", oid, tp)
            return oid

        is_buy, size, tp, order_type = self._trigger_payload(position_size, tp, "tp")
        try:
            resp = self.exchange.modify_order(
                int(old_tp_oid),
                self.cfg.coin,
                is_buy,
                size,
                tp,
                order_type,
                reduce_only=True,
            )
            error = self._extract_error(resp)
            if error:
                raise RuntimeError(error)
            new_oid = self._extract_oid(resp) or int(old_tp_oid)
            log.info("Dynamic TP modified oid=%s px=%.4f", new_oid, tp)
            return new_oid
        except Exception as exc:
            log.warning(
                "Dynamic TP modify failed for oid=%s (%s); creating replacement before cancel",
                old_tp_oid,
                exc,
            )

        # Fallback: create the new protection first, so a failed replacement
        # cannot leave the position without any TP.
        new_oid = self._trigger_order(position_size, tp, "tp")
        if new_oid != old_tp_oid:
            self.cancel_oid(old_tp_oid)
        log.info("Dynamic TP replaced old_oid=%s new_oid=%s px=%.4f", old_tp_oid, new_oid, tp)
        return new_oid

    def cancel_oid(self, oid: Optional[int]) -> None:
        if oid is None:
            return
        try:
            self.exchange.cancel(self.cfg.coin, int(oid))
        except Exception as exc:
            log.warning("cancel oid=%s failed: %s", oid, exc)

    def cancel_all_protection(self) -> None:
        for order in self.info.frontend_open_orders(self.cfg.account_address, self.cfg.dex):
            if order.get("coin") == self.cfg.coin and order.get("reduceOnly") and order.get("isTrigger"):
                self.cancel_oid(order.get("oid"))

    def recover_protection(self):
        tp_oid = tp_px = sl_oid = sl_px = None
        for order in self.info.frontend_open_orders(self.cfg.account_address, self.cfg.dex):
            if order.get("coin") != self.cfg.coin or not order.get("reduceOnly") or not order.get("isTrigger"):
                continue
            typ = str(order.get("orderType", ""))
            trigger = order.get("triggerPx")
            if "Take Profit" in typ:
                tp_oid = int(order["oid"])
                tp_px = float(trigger)
            elif "Stop" in typ:
                sl_oid = int(order["oid"])
                sl_px = float(trigger)
        return tp_oid, tp_px, sl_oid, sl_px


class OKXExecutor(BaseExecutor):
    """OKX linear perpetual adapter using contract-aware sizing and algo TP/SL."""

    def __init__(
        self,
        cfg: Config,
        client: Optional[OKXClient] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.client = client or OKXClient(
            base_url=cfg.okx_base_url,
            api_key=cfg.okx_api_key,
            secret_key=cfg.okx_secret_key,
            passphrase=cfg.okx_passphrase,
            demo=cfg.okx_demo,
            timeout=cfg.request_timeout,
            retry_attempts=cfg.request_retry_attempts,
        )
        self._sleep = sleep
        self.market_meta = self._validate_market()
        self.ct_val = Decimal(str(self.market_meta["ctVal"]))
        self.lot_size = Decimal(str(self.market_meta["lotSz"]))
        self.min_size = Decimal(str(self.market_meta["minSz"]))
        self.tick_size = Decimal(str(self.market_meta["tickSz"]))
        account = self.client.get_private("/api/v5/account/config")
        if not account:
            raise RuntimeError("OKX account configuration was empty")
        self.position_mode = str(account[0].get("posMode", "net_mode"))
        if self.position_mode not in {"net_mode", "long_short_mode"}:
            raise RuntimeError(f"Unsupported OKX position mode: {self.position_mode}")
        if cfg.leverage > 0:
            self._set_leverage(cfg.leverage)

    @staticmethod
    def _text(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _result(data: list, operation: str) -> dict:
        if not data:
            raise RuntimeError(f"OKX {operation} returned no result")
        result = data[0]
        if str(result.get("sCode", "0")) not in {"", "0"}:
            raise RuntimeError(
                f"OKX {operation} failed ({result.get('sCode')}): {result.get('sMsg', '')}"
            )
        return result

    def _validate_market(self) -> dict:
        data = self.client.get_public(
            "/api/v5/public/instruments",
            {"instType": "SWAP", "instId": self.cfg.okx_inst_id},
        )
        if not data:
            raise RuntimeError(f"OKX instrument not found: {self.cfg.okx_inst_id}")
        item = data[0]
        if item.get("instId") != self.cfg.okx_inst_id or item.get("instType") != "SWAP":
            raise RuntimeError(f"Unexpected OKX instrument metadata for {self.cfg.okx_inst_id}")
        if item.get("state") != "live":
            raise RuntimeError(f"OKX instrument is not live: {self.cfg.okx_inst_id}")
        if item.get("ctType") != "linear" or item.get("settleCcy") not in {"USDT", "USDC"}:
            raise RuntimeError("This OKX adapter supports only linear USDT/USDC perpetual swaps")
        for key in ("ctVal", "lotSz", "minSz", "tickSz"):
            if Decimal(str(item.get(key, "0"))) <= 0:
                raise RuntimeError(f"Invalid OKX instrument {key}: {item.get(key)}")
        return item

    def _set_leverage(self, leverage: int) -> None:
        base = {
            "instId": self.cfg.okx_inst_id,
            "lever": str(leverage),
            "mgnMode": self.cfg.okx_margin_mode,
        }
        if self.position_mode == "long_short_mode" and self.cfg.okx_margin_mode == "isolated":
            sides = []
            if self.cfg.enable_longs:
                sides.append("long")
            if self.cfg.enable_shorts:
                sides.append("short")
            for side in sides:
                self._result(
                    self.client.post_private(
                        "/api/v5/account/set-leverage",
                        dict(base, posSide=side),
                    ),
                    f"set {side} leverage",
                )
            return
        self._result(
            self.client.post_private("/api/v5/account/set-leverage", base),
            "set leverage",
        )

    def _round_step(self, value: Decimal, step: Decimal, rounding) -> Decimal:
        units = (value / step).to_integral_value(rounding=rounding)
        return units * step

    def _round_size(self, size: float) -> Decimal:
        return self._round_step(Decimal(str(abs(size))), self.lot_size, ROUND_DOWN)

    def _round_price(self, price: float) -> Decimal:
        return self._round_step(Decimal(str(price)), self.tick_size, ROUND_HALF_UP)

    def _entry_limit_price(self, is_buy: bool, reference: Decimal) -> Decimal:
        slippage = Decimal(str(self.cfg.max_slippage))
        raw = reference * (Decimal("1") + slippage if is_buy else Decimal("1") - slippage)
        rounding = ROUND_UP if is_buy else ROUND_DOWN
        return self._round_step(raw, self.tick_size, rounding)

    @staticmethod
    def _client_id(kind: str) -> str:
        return f"herman{kind}{uuid.uuid4().hex[:20]}"[:32]

    def _recover_by_client_id(
        self,
        path: str,
        key: str,
        client_id: str,
        include_inst_id: bool,
    ) -> Optional[dict]:
        """Resolve an order whose POST result was ambiguous after a network error."""
        params = {key: client_id}
        if include_inst_id:
            params["instId"] = self.cfg.okx_inst_id
        for attempt in range(3):
            try:
                rows = self.client.get_private(path, params)
                if rows:
                    return rows[0]
            except OKXAPIError:
                pass
            if attempt < 2:
                self._sleep(0.2)
        return None

    def _post_order(self, body: dict) -> dict:
        try:
            return self._result(
                self.client.post_private("/api/v5/trade/order", body),
                "entry",
            )
        except OKXAPIError as original:
            recovered = self._recover_by_client_id(
                "/api/v5/trade/order",
                "clOrdId",
                body["clOrdId"],
                include_inst_id=True,
            )
            if recovered is None:
                raise original
            log.warning(
                "Recovered OKX entry by clOrdId after an ambiguous POST result: %s",
                body["clOrdId"],
            )
            return recovered

    def _post_algo(self, body: dict, operation: str) -> dict:
        try:
            return self._result(
                self.client.post_private("/api/v5/trade/order-algo", body),
                operation,
            )
        except OKXAPIError as original:
            recovered = self._recover_by_client_id(
                "/api/v5/trade/order-algo",
                "algoClOrdId",
                body["algoClOrdId"],
                include_inst_id=False,
            )
            if recovered is None:
                raise original
            log.warning(
                "Recovered OKX algo order by algoClOrdId after an ambiguous POST result: %s",
                body["algoClOrdId"],
            )
            return recovered

    def mid(self) -> float:
        data = self.client.get_public(
            "/api/v5/market/ticker",
            {"instId": self.cfg.okx_inst_id},
        )
        if not data:
            raise RuntimeError(f"No OKX ticker for {self.cfg.okx_inst_id}")
        ticker = data[0]
        bid = Decimal(str(ticker.get("bidPx") or "0"))
        ask = Decimal(str(ticker.get("askPx") or "0"))
        if bid > 0 and ask > 0:
            return float((bid + ask) / 2)
        last = Decimal(str(ticker.get("last") or "0"))
        if last <= 0:
            raise RuntimeError(f"Invalid OKX ticker for {self.cfg.okx_inst_id}")
        return float(last)

    def position(self) -> Position:
        rows = self.client.get_private(
            "/api/v5/account/positions",
            {"instId": self.cfg.okx_inst_id},
        )
        active = []
        for row in rows:
            if row.get("instId") != self.cfg.okx_inst_id:
                continue
            amount = Decimal(str(row.get("pos") or "0"))
            if amount == 0:
                continue
            side = str(row.get("posSide", "net"))
            signed = amount
            if side == "short":
                signed = -abs(amount)
            elif side == "long":
                signed = abs(amount)
            entry = row.get("avgPx") or row.get("openAvgPx")
            active.append(Position(float(signed), float(entry) if entry not in (None, "") else None))
        if len(active) > 1:
            raise RuntimeError(
                "OKX account has both LONG and SHORT positions for this instrument; "
                "the Herman executor manages only one side at a time"
            )
        return active[0] if active else Position(0.0, None)

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        reference = Decimal(str(self.mid()))
        contracts = self._round_step(
            Decimal(str(notional_usdc)) / (reference * self.ct_val),
            self.lot_size,
            ROUND_DOWN,
        )
        if contracts < self.min_size:
            minimum_notional = self.min_size * self.ct_val * reference
            raise RuntimeError(
                "Calculated OKX order size is below minSz; "
                f"increase ORDER_NOTIONAL_USDC to at least {self._text(minimum_notional)}"
            )
        body = {
            "instId": self.cfg.okx_inst_id,
            "tdMode": self.cfg.okx_margin_mode,
            "side": "buy" if is_buy else "sell",
            "ordType": "ioc",
            "sz": self._text(contracts),
            "px": self._text(self._entry_limit_price(is_buy, reference)),
            "clOrdId": self._client_id("entry"),
        }
        if self.position_mode == "long_short_mode":
            body["posSide"] = "long" if is_buy else "short"
        else:
            body["reduceOnly"] = False

        result = self._post_order(body)
        order_id = result.get("ordId")
        if not order_id:
            raise RuntimeError(f"OKX entry returned no ordId: {result}")

        deadline = time.monotonic() + self.cfg.okx_fill_timeout
        while time.monotonic() < deadline:
            details = self.client.get_private(
                "/api/v5/trade/order",
                {"instId": self.cfg.okx_inst_id, "ordId": order_id},
            )
            if details and details[0].get("state") in {"filled", "canceled"}:
                break
            self._sleep(0.2)
        return result

    def _protection_base(self, position_size: float) -> dict:
        size = self._round_size(position_size)
        if size < self.min_size:
            raise RuntimeError("OKX position size is below minSz while creating protection")
        body = {
            "instId": self.cfg.okx_inst_id,
            "tdMode": self.cfg.okx_margin_mode,
            "side": "sell" if position_size > 0 else "buy",
            "sz": self._text(size),
        }
        if self.position_mode == "long_short_mode":
            body["posSide"] = "long" if position_size > 0 else "short"
        else:
            body["reduceOnly"] = True
        return body

    def _place_single_protection(self, position_size: float, price: float, kind: str) -> int:
        body = self._protection_base(position_size)
        rounded = self._text(self._round_price(price))
        body.update({"ordType": "conditional", "algoClOrdId": self._client_id(kind)})
        if kind == "tp":
            body.update(
                tpTriggerPx=rounded,
                tpOrdPx="-1",
                tpTriggerPxType=self.cfg.okx_trigger_price_type,
            )
        else:
            body.update(
                slTriggerPx=rounded,
                slOrdPx="-1",
                slTriggerPxType=self.cfg.okx_trigger_price_type,
            )
        result = self._post_algo(body, f"{kind.upper()} protection")
        algo_id = result.get("algoId")
        if not algo_id:
            raise RuntimeError(f"OKX {kind.upper()} returned no algoId: {result}")
        return int(algo_id)

    def place_protection(self, position_size: float, tp: float, sl: float):
        body = self._protection_base(position_size)
        body.update(
            ordType="oco",
            algoClOrdId=self._client_id("protect"),
            tpTriggerPx=self._text(self._round_price(tp)),
            tpOrdPx="-1",
            tpTriggerPxType=self.cfg.okx_trigger_price_type,
            slTriggerPx=self._text(self._round_price(sl)),
            slOrdPx="-1",
            slTriggerPxType=self.cfg.okx_trigger_price_type,
        )
        result = self._post_algo(body, "OCO protection")
        algo_id = result.get("algoId")
        if not algo_id:
            raise RuntimeError(f"OKX protection returned no algoId: {result}")
        oid = int(algo_id)
        log.info(
            "OKX OCO protection placed algoId=%s TP=%s SL=%s size=%s",
            oid,
            body["tpTriggerPx"],
            body["slTriggerPx"],
            body["sz"],
        )
        return oid, oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        if old_tp_oid is None:
            return self._place_single_protection(position_size, tp, "tp")
        body = {
            "instId": self.cfg.okx_inst_id,
            "algoId": str(old_tp_oid),
            "newTpTriggerPx": self._text(self._round_price(tp)),
            "newTpOrdPx": "-1",
            "newTpTriggerPxType": self.cfg.okx_trigger_price_type,
            "cxlOnFail": False,
        }
        try:
            self._result(
                self.client.post_private("/api/v5/trade/amend-algos", body),
                "dynamic TP amendment",
            )
        except (OKXAPIError, RuntimeError) as exc:
            # The existing OCO still contains both its previous TP and SL. Keep
            # it intact and retry on the next closed bar instead of creating a
            # duplicate stop or introducing a protection gap.
            log.warning("OKX Dynamic TP amendment failed for algoId=%s: %s", old_tp_oid, exc)
            return int(old_tp_oid)
        log.info("OKX Dynamic TP amended algoId=%s px=%s", old_tp_oid, body["newTpTriggerPx"])
        return int(old_tp_oid)

    def cancel_oid(self, oid: Optional[int]) -> None:
        if oid is None:
            return
        try:
            self._result(
                self.client.post_private(
                    "/api/v5/trade/cancel-algos",
                    [{"instId": self.cfg.okx_inst_id, "algoId": str(oid)}],
                ),
                "cancel protection",
            )
        except (OKXAPIError, RuntimeError) as exc:
            log.warning("OKX cancel algoId=%s failed: %s", oid, exc)

    def _pending_protection(self) -> list[dict]:
        rows = self.client.get_private(
            "/api/v5/trade/orders-algo-pending",
            {"ordType": "conditional", "instId": self.cfg.okx_inst_id},
        )
        rows += self.client.get_private(
            "/api/v5/trade/orders-algo-pending",
            {"ordType": "oco", "instId": self.cfg.okx_inst_id},
        )
        return [row for row in rows if row.get("instId") == self.cfg.okx_inst_id]

    def cancel_all_protection(self) -> None:
        orders = [
            {"instId": self.cfg.okx_inst_id, "algoId": str(row["algoId"])}
            for row in self._pending_protection()
            if row.get("algoId") and (row.get("tpTriggerPx") or row.get("slTriggerPx"))
        ]
        for start in range(0, len(orders), 10):
            try:
                data = self.client.post_private(
                    "/api/v5/trade/cancel-algos",
                    orders[start:start + 10],
                )
                for result in data:
                    if str(result.get("sCode", "0")) not in {"", "0"}:
                        log.warning(
                            "OKX protection cancel failed algoId=%s: %s",
                            result.get("algoId"),
                            result.get("sMsg"),
                        )
            except (OKXAPIError, RuntimeError) as exc:
                log.warning("OKX batch protection cancel failed: %s", exc)

    def recover_protection(self):
        tp_oid = tp_px = sl_oid = sl_px = None
        rows = sorted(
            self._pending_protection(),
            key=lambda row: int(row.get("cTime") or 0),
            reverse=True,
        )
        for row in rows:
            algo_id = row.get("algoId")
            if not algo_id:
                continue
            if tp_px is None and row.get("tpTriggerPx") not in (None, ""):
                tp_oid = int(algo_id)
                tp_px = float(row["tpTriggerPx"])
            if sl_px is None and row.get("slTriggerPx") not in (None, ""):
                sl_oid = int(algo_id)
                sl_px = float(row["slTriggerPx"])
            if tp_px is not None and sl_px is not None:
                break
        return tp_oid, tp_px, sl_oid, sl_px
