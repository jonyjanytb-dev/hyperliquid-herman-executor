from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from app.okx_client import OKXClient

from .config import GatewayConfig


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _hl_market_key_candidates(coin: str) -> tuple[str, ...]:
    if ":" in coin:
        return coin, coin.split(":", 1)[1]
    return (coin,)


def _hl_mid(mids: dict, coin: str) -> float:
    for key in _hl_market_key_candidates(coin):
        if key in mids:
            return safe_float(mids.get(key))
    return 0.0


def _hl_coin_matches(value: Any, coin: str) -> bool:
    text = str(value or "")
    return text in _hl_market_key_candidates(coin)


def _masked_address(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _summary_from_hl_state(state: dict) -> dict:
    summary = state.get("marginSummary") or {}
    return {
        "account_value": safe_float(summary.get("accountValue")),
        "margin_used": safe_float(summary.get("totalMarginUsed")),
        "notional_position": safe_float(summary.get("totalNtlPos")),
        "withdrawable": safe_float(state.get("withdrawable")),
    }


def _spot_balances(state: dict) -> list[dict]:
    result = []
    for item in state.get("balances", []):
        total = safe_float(item.get("total"))
        hold = safe_float(item.get("hold"))
        if abs(total) < 1e-15 and abs(hold) < 1e-15:
            continue
        result.append(
            {
                "coin": str(item.get("coin", "?")),
                "total": total,
                "hold": hold,
                "available": max(0.0, total - hold),
            }
        )
    result.sort(key=lambda row: (0 if row["coin"] == "USDC" else 1 if row["coin"] == "HYPE" else 2, row["coin"]))
    return result


def _normalize_hl_fill(item: dict) -> dict:
    realized = safe_float(item.get("closedPnl"))
    fee_cost = safe_float(item.get("fee"))
    direction = str(item.get("dir", ""))
    return {
        "time": int(item.get("time") or 0),
        "id": str(item.get("tid") or item.get("oid") or ""),
        "order_id": str(item.get("oid") or ""),
        "direction": direction,
        "side": "BUY" if str(item.get("side")) == "B" else "SELL",
        "price": safe_float(item.get("px")),
        "size": safe_float(item.get("sz")),
        "realized_pnl": realized,
        "fee": fee_cost,
        "net_pnl": realized - fee_cost,
        "is_close": ("close" in direction.lower()) or abs(realized) > 1e-15,
        "fee_currency": str(item.get("feeToken", "USDC")),
    }


def _normalize_okx_fill(item: dict) -> dict:
    realized = safe_float(item.get("fillPnl"))
    raw_fee = safe_float(item.get("fee"))
    fee_cost = -raw_fee
    side = str(item.get("side", "")).upper()
    direction = str(item.get("posSide") or item.get("side") or "")
    return {
        "time": int(item.get("fillTime") or item.get("ts") or 0),
        "id": str(item.get("tradeId") or item.get("billId") or item.get("ordId") or ""),
        "order_id": str(item.get("ordId") or ""),
        "direction": direction,
        "side": side,
        "price": safe_float(item.get("fillPx")),
        "size": safe_float(item.get("fillSz")),
        "realized_pnl": realized,
        "fee": fee_cost,
        "net_pnl": realized - fee_cost,
        "is_close": abs(realized) > 1e-15,
        "fee_currency": str(item.get("feeCcy", "")),
    }


def build_analytics(fills: list[dict]) -> dict:
    ordered = sorted(fills, key=lambda row: int(row.get("time") or 0))
    total_fees = sum(safe_float(row.get("fee")) for row in ordered)
    gross_realized = sum(safe_float(row.get("realized_pnl")) for row in ordered)
    net_realized = sum(safe_float(row.get("net_pnl")) for row in ordered)

    closed = [row for row in ordered if bool(row.get("is_close"))]
    wins = [row for row in closed if safe_float(row.get("net_pnl")) > 0]
    losses = [row for row in closed if safe_float(row.get("net_pnl")) < 0]

    gross_profit = sum(safe_float(row.get("net_pnl")) for row in wins)
    gross_loss = -sum(safe_float(row.get("net_pnl")) for row in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-15 else None
    win_rate = (len(wins) / len(closed) * 100.0) if closed else None
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (-gross_loss / len(losses)) if losses else 0.0

    cumulative = 0.0
    curve = []
    daily: dict[str, float] = {}
    for row in ordered:
        pnl = safe_float(row.get("net_pnl"))
        cumulative += pnl
        ts = int(row.get("time") or 0)
        if ts > 0:
            curve.append({"time": ts, "value": cumulative})
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            daily[day] = daily.get(day, 0.0) + pnl

    if len(curve) > 500:
        stride = max(1, len(curve) // 500)
        sampled = curve[::stride]
        if sampled[-1] != curve[-1]:
            sampled.append(curve[-1])
        curve = sampled

    return {
        "gross_realized_pnl": gross_realized,
        "fees": total_fees,
        "net_realized_pnl": net_realized,
        "fill_count": len(ordered),
        "closed_fill_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "curve": curve,
        "daily": [{"date": key, "pnl": daily[key]} for key in sorted(daily)],
        "note": "Win rate and trade count are based on normalized closing fills; exchange fill history is the source of realized PnL.",
    }


class BaseReadOnlyProvider:
    def overview(self) -> dict:
        raise NotImplementedError

    def fills(self, days: int, limit: int = 500) -> list[dict]:
        raise NotImplementedError


class HyperliquidReadOnlyProvider(BaseReadOnlyProvider):
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.url = (
            "https://api.hyperliquid.xyz/info"
            if cfg.network == "mainnet"
            else "https://api.hyperliquid-testnet.xyz/info"
        )
        self.session = requests.Session()

    def _post(self, payload: dict):
        last_error: Optional[Exception] = None
        for attempt in range(self.cfg.retry_attempts):
            try:
                response = self.session.post(
                    self.url,
                    json=payload,
                    timeout=self.cfg.request_timeout,
                )
                if response.status_code == 429:
                    raise RuntimeError("Hyperliquid API rate limited (429)")
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 >= self.cfg.retry_attempts:
                    break
                time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"Hyperliquid read-only request failed: {last_error}") from last_error

    def overview(self) -> dict:
        address = self.cfg.account_address
        core_state = self._post({"type": "clearinghouseState", "user": address})
        hip3_state = self._post(
            {"type": "clearinghouseState", "user": address, "dex": self.cfg.dex}
        )
        spot_state = self._post({"type": "spotClearinghouseState", "user": address})
        open_orders = self._post(
            {"type": "frontendOpenOrders", "user": address, "dex": self.cfg.dex}
        )
        mids = self._post({"type": "allMids", "dex": self.cfg.dex})

        spot = _spot_balances(spot_state if isinstance(spot_state, dict) else {})
        spot_map = {row["coin"]: row for row in spot}

        selected_position = None
        for wrapper in (hip3_state or {}).get("assetPositions", []):
            p = wrapper.get("position", {})
            if not _hl_coin_matches(p.get("coin"), self.cfg.coin):
                continue
            size = safe_float(p.get("szi"))
            if abs(size) < 1e-15:
                continue
            leverage = p.get("leverage") or {}
            selected_position = {
                "side": "LONG" if size > 0 else "SHORT",
                "size": abs(size),
                "signed_size": size,
                "entry_price": safe_float(p.get("entryPx")),
                "mark_price": _hl_mid(mids or {}, self.cfg.coin),
                "unrealized_pnl": safe_float(p.get("unrealizedPnl")),
                "position_value": safe_float(p.get("positionValue")),
                "margin_used": safe_float(p.get("marginUsed")),
                "leverage": safe_float(leverage.get("value")) if isinstance(leverage, dict) else 0.0,
            }
            break

        protections = []
        for order in open_orders if isinstance(open_orders, list) else []:
            if (
                not _hl_coin_matches(order.get("coin"), self.cfg.coin)
                or not order.get("reduceOnly")
                or not order.get("isTrigger")
            ):
                continue
            order_type = str(order.get("orderType", ""))
            kind = "TP" if "Take Profit" in order_type else "SL" if "Stop" in order_type else "TRIGGER"
            protections.append(
                {
                    "kind": kind,
                    "trigger_price": safe_float(order.get("triggerPx")),
                    "order_id": str(order.get("oid") or ""),
                    "order_type": order_type,
                }
            )

        return {
            "exchange": "hyperliquid",
            "market": self.cfg.coin,
            "account": _masked_address(address),
            "price": _hl_mid(mids or {}, self.cfg.coin),
            "funds": {
                "core": _summary_from_hl_state(core_state if isinstance(core_state, dict) else {}),
                "hip3": _summary_from_hl_state(hip3_state if isinstance(hip3_state, dict) else {}),
                "spot_usdc": (spot_map.get("USDC") or {}).get("total", 0.0),
                "spot_usdc_available": (spot_map.get("USDC") or {}).get("available", 0.0),
                "spot_hype": (spot_map.get("HYPE") or {}).get("total", 0.0),
                "spot_balances": spot,
            },
            "position": selected_position,
            "protections": protections,
        }

    def fills(self, days: int, limit: int = 500) -> list[dict]:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - int(days * 86400 * 1000)
        raw = self._post(
            {
                "type": "userFillsByTime",
                "user": self.cfg.account_address,
                "startTime": start_ms,
                "endTime": now_ms,
                "aggregateByTime": True,
            }
        )
        rows = []
        for item in raw if isinstance(raw, list) else []:
            if not _hl_coin_matches(item.get("coin"), self.cfg.coin):
                continue
            rows.append(_normalize_hl_fill(item))
        rows.sort(key=lambda row: row["time"], reverse=True)
        return rows[: max(1, min(limit, 2000))]


class OKXReadOnlyProvider(BaseReadOnlyProvider):
    def __init__(self, cfg: GatewayConfig):
        self.cfg = cfg
        self.client = OKXClient(
            base_url=cfg.okx_base_url,
            api_key=cfg.okx_api_key,
            secret_key=cfg.okx_secret_key,
            passphrase=cfg.okx_passphrase,
            demo=cfg.okx_demo,
            timeout=cfg.request_timeout,
            retry_attempts=cfg.retry_attempts,
        )

    def overview(self) -> dict:
        balances = self.client.get_private("/api/v5/account/balance")
        positions = self.client.get_private(
            "/api/v5/account/positions",
            {"instId": self.cfg.okx_inst_id},
        )
        ticker = self.client.get_public(
            "/api/v5/market/ticker",
            {"instId": self.cfg.okx_inst_id},
        )

        protections = []
        for order_type in ("conditional", "oco"):
            try:
                rows = self.client.get_private(
                    "/api/v5/trade/orders-algo-pending",
                    {"ordType": order_type, "instId": self.cfg.okx_inst_id},
                )
            except Exception:
                rows = []
            for item in rows:
                tp = safe_float(item.get("tpTriggerPx"))
                sl = safe_float(item.get("slTriggerPx"))
                if tp:
                    protections.append(
                        {
                            "kind": "TP",
                            "trigger_price": tp,
                            "order_id": str(item.get("algoId") or ""),
                            "order_type": order_type,
                        }
                    )
                if sl:
                    protections.append(
                        {
                            "kind": "SL",
                            "trigger_price": sl,
                            "order_id": str(item.get("algoId") or ""),
                            "order_type": order_type,
                        }
                    )

        account = balances[0] if balances else {}
        quote = self.cfg.quote_currency
        detail = next(
            (item for item in account.get("details", []) if item.get("ccy") == quote),
            {},
        )

        active_positions = []
        for row in positions:
            size = safe_float(row.get("pos"))
            if abs(size) < 1e-15:
                continue
            pos_side = str(row.get("posSide", "net"))
            side = "SHORT" if pos_side == "short" or (pos_side == "net" and size < 0) else "LONG"
            active_positions.append(
                {
                    "side": side,
                    "size": abs(size),
                    "signed_size": size,
                    "entry_price": safe_float(row.get("avgPx")),
                    "mark_price": safe_float(row.get("markPx")),
                    "unrealized_pnl": safe_float(row.get("upl")),
                    "position_value": abs(safe_float(row.get("notionalUsd"))),
                    "margin_used": safe_float(row.get("margin")),
                    "leverage": safe_float(row.get("lever")),
                }
            )

        last_price = safe_float(ticker[0].get("last")) if ticker else 0.0
        position = active_positions[0] if active_positions else None
        if position and not position.get("mark_price"):
            position["mark_price"] = last_price

        return {
            "exchange": "okx",
            "market": self.cfg.okx_inst_id,
            "account": "OKX",
            "price": last_price,
            "funds": {
                "total_equity_usd": safe_float(account.get("totalEq")),
                "quote_currency": quote,
                "quote_equity": safe_float(detail.get("eq") or detail.get("cashBal")),
                "quote_available": safe_float(detail.get("availEq") or detail.get("availBal")),
            },
            "position": position,
            "positions": active_positions,
            "protections": protections,
        }

    def fills(self, days: int, limit: int = 500) -> list[dict]:
        cutoff = int((time.time() - days * 86400) * 1000)
        requested = max(1, min(limit, 500))
        rows: list[dict] = []
        after = ""
        while len(rows) < requested:
            params = {
                "instType": "SWAP",
                "instId": self.cfg.okx_inst_id,
                "limit": "100",
                "after": after,
            }
            page = self.client.get_private("/api/v5/trade/fills-history", params)
            if not page:
                break
            stop = False
            for item in page:
                ts = int(item.get("fillTime") or item.get("ts") or 0)
                if ts and ts < cutoff:
                    stop = True
                    continue
                rows.append(_normalize_okx_fill(item))
                if len(rows) >= requested:
                    break
            if stop or len(page) < 100 or len(rows) >= requested:
                break
            next_after = str(page[-1].get("billId") or "")
            if not next_after or next_after == after:
                break
            after = next_after

        rows.sort(key=lambda row: row["time"], reverse=True)
        return rows[:requested]


def build_provider(cfg: GatewayConfig) -> BaseReadOnlyProvider:
    if cfg.exchange == "okx":
        return OKXReadOnlyProvider(cfg)
    return HyperliquidReadOnlyProvider(cfg)
