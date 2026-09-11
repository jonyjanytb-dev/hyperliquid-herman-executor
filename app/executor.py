from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from .config import Config

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
