from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from .config import Config

log = logging.getLogger(__name__)


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
        self._tp_oid, self._sl_oid = self._next_oid(), self._next_oid()
        log.info("DRY RUN protection TP=%.4f SL=%.4f", tp, sl)
        return self._tp_oid, self._sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        self._tp = tp
        self._tp_oid = self._next_oid()
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

    def _trigger_order(self, position_size: float, trigger_px: float, kind: str) -> Optional[int]:
        is_buy = position_size < 0
        size = self._round_size(abs(position_size))
        order_type = {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": kind}}
        resp = self.exchange.order(
            self.cfg.coin,
            is_buy,
            size,
            trigger_px,
            order_type,
            reduce_only=True,
        )
        oid = self._extract_oid(resp)
        if oid is None:
            raise RuntimeError(f"Failed to create {kind.upper()} trigger order: {resp}")
        return oid

    def place_protection(self, position_size: float, tp: float, sl: float):
        tp_oid = self._trigger_order(position_size, tp, "tp")
        try:
            sl_oid = self._trigger_order(position_size, sl, "sl")
        except Exception:
            self.cancel_oid(tp_oid)
            raise
        return tp_oid, sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        if old_tp_oid is not None:
            self.cancel_oid(old_tp_oid)
        return self._trigger_order(position_size, tp, "tp")

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
