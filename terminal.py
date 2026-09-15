from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

from app.config import DEFAULT_OKX_INST_ID
from app.okx_client import OKXClient

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env() -> None:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE.exists():
            raise FileNotFoundError(".env.example not found")
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        print("\n已创建本地 .env（默认 DRY_RUN=true，不会真实下单）。")


def read_env() -> dict[str, str]:
    ensure_env()
    raw = dotenv_values(ENV_PATH)
    return {k: str(v or "") for k, v in raw.items()}


def selected_exchange(cfg: dict[str, str]) -> str:
    return cfg.get("EXCHANGE", "hyperliquid").strip().lower() or "hyperliquid"


def execution_mode(cfg: dict[str, str]) -> str:
    if cfg.get("DRY_RUN", "true").strip().lower() == "true":
        return "dry_run"
    if selected_exchange(cfg) == "okx" and cfg.get("OKX_DEMO", "true").strip().lower() == "true":
        return "demo"
    return "live"


def market_symbol(cfg: dict[str, str]) -> str:
    if selected_exchange(cfg) == "okx":
        return cfg.get("OKX_INST_ID", DEFAULT_OKX_INST_ID).strip().upper() or DEFAULT_OKX_INST_ID
    return cfg.get("COIN", "xyz:XYZ100").strip()


def quote_currency(cfg: dict[str, str]) -> str:
    if selected_exchange(cfg) != "okx":
        return "USDC"
    parts = market_symbol(cfg).split("-")
    return parts[-2] if len(parts) >= 3 else "USDT/USDC"


def credentials_ready(cfg: dict[str, str]) -> bool:
    if selected_exchange(cfg) == "okx":
        return all(
            cfg.get(key, "").strip()
            for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        )
    return bool(cfg.get("ACCOUNT_ADDRESS", "").strip() and cfg.get("API_PRIVATE_KEY", "").strip())


def set_env(key: str, value: str) -> None:
    ensure_env()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(prefix + value)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def yn(value: str) -> str:
    return "是" if value.strip().lower() in {"1", "true", "yes", "on"} else "否"


def masked_address(value: str) -> str:
    if not value:
        return "未设置"
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def show_status() -> None:
    cfg = read_env()
    exchange = selected_exchange(cfg)
    mode = execution_mode(cfg)
    notional = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    leverage = int(cfg.get("LEVERAGE", "0") or 0)
    currency = quote_currency(cfg)
    if leverage > 0:
        margin = f"约 {notional / leverage:.2f} {currency}"
        lev_text = f"{leverage}x"
    else:
        margin = "由账户当前杠杆决定"
        lev_text = "不自动修改"

    print("\n" + "=" * 58)
    print(" Herman Executor · Hyperliquid / OKX 本地交互终端")
    print("=" * 58)
    mode_text = {
        "dry_run": "DRY RUN（本地空跑，不发送订单）",
        "demo": "OKX DEMO（交易所模拟盘）",
        "live": "LIVE（真实下单）",
    }[mode]
    print(f" 交易所      : {'OKX' if exchange == 'okx' else 'Hyperliquid'}")
    print(f" 模式        : {mode_text}")
    print(f" 市场        : {market_symbol(cfg) or '未设置'}")
    print(f" 周期        : {cfg.get('INTERVAL', '1m')}")
    print(f" 每笔名义仓位: {notional:.2f} {currency}")
    print(f" 杠杆        : {lev_text}")
    print(f" 预计保证金  : {margin}")
    if exchange == "okx":
        print(f" 保证金模式  : {cfg.get('OKX_MARGIN_MODE', 'cross')}")
        print(f" OKX API     : {'已设置' if credentials_ready(cfg) else '未设置'}")
    else:
        print(f" 主账户      : {masked_address(cfg.get('ACCOUNT_ADDRESS', ''))}")
        print(f" API Wallet  : {'已设置' if cfg.get('API_PRIVATE_KEY') else '未设置'}")
    print(f" 做多        : {yn(cfg.get('ENABLE_LONGS', 'true'))}")
    print(f" 做空        : {yn(cfg.get('ENABLE_SHORTS', 'true'))}")
    print(f" TP          : {cfg.get('TP_MODE', '200 SMA')} / {cfg.get('SMA_TARGET_BEHAVIOUR', 'Dynamic')}")
    print(f" SL          : {cfg.get('SL_MODE', 'Fixed Points')} / {cfg.get('SL_FIXED_POINTS', '125')} points")
    print("=" * 58)


def ask_float(prompt: str, current: float, minimum: float = 0.0) -> float:
    raw = input(f"{prompt} [{current}]: ").strip()
    if not raw:
        return current
    value = float(raw)
    if value <= minimum:
        raise ValueError(f"必须大于 {minimum}")
    return value


def ask_int(prompt: str, current: int, minimum: int = 0) -> int:
    raw = input(f"{prompt} [{current}]: ").strip()
    if not raw:
        return current
    value = int(raw)
    if value < minimum:
        raise ValueError(f"不能小于 {minimum}")
    return value


def configure_position() -> None:
    cfg = read_env()
    notional = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    leverage = int(cfg.get("LEVERAGE", "0") or 0)
    currency = quote_currency(cfg)
    print("\n说明：名义仓位=实际合约持仓价值。例：250U仓位 + 5x ≈ 50U保证金。")
    notional = ask_float(f"每笔名义仓位 {currency}", notional)
    leverage = ask_int("杠杆（0=不自动修改账户杠杆）", leverage, 0)
    set_env("ORDER_NOTIONAL_USDC", str(notional))
    set_env("LEVERAGE", str(leverage))
    print("已保存。")


def configure_credentials() -> None:
    cfg = read_env()
    current_exchange = selected_exchange(cfg)
    print(f"\n当前交易所：{'OKX' if current_exchange == 'okx' else 'Hyperliquid'}")
    choice = input("选择交易所 [1=Hyperliquid, 2=OKX, Enter=保持]: ").strip()
    exchange = {"1": "hyperliquid", "2": "okx"}.get(choice, current_exchange)
    if choice not in {"", "1", "2"}:
        raise ValueError("交易所选项只能是 1 或 2")
    if exchange != current_exchange:
        set_env("EXCHANGE", exchange)
        # Never inherit another exchange's live/demo state while changing
        # account credentials or instrument configuration.
        set_env("DRY_RUN", "true")
        current_state_path = cfg.get("STATE_PATH", "").strip()
        default_paths = {"", "runtime/state.json", "runtime/state-hyperliquid.json", "runtime/state-okx.json"}
        if current_state_path in default_paths:
            set_env("STATE_PATH", f"runtime/state-{exchange}.json")
        cfg = read_env()

    if exchange == "okx":
        current_inst = cfg.get("OKX_INST_ID", DEFAULT_OKX_INST_ID).strip().upper() or DEFAULT_OKX_INST_ID
        prompt = f"OKX 永续合约 ID [{current_inst}]: " if current_inst else "OKX 永续合约 ID（例如 BTC-USDT-SWAP）: "
        inst_id = input(prompt).strip().upper() or current_inst
        if not inst_id.endswith("-SWAP"):
            raise ValueError("目前只支持 OKX 永续合约，ID 必须以 -SWAP 结尾")
        set_env("OKX_INST_ID", inst_id)

        margin_mode = input(
            f"保证金模式 cross/isolated [{cfg.get('OKX_MARGIN_MODE', 'cross') or 'cross'}]: "
        ).strip().lower() or cfg.get("OKX_MARGIN_MODE", "cross").strip().lower() or "cross"
        if margin_mode not in {"cross", "isolated"}:
            raise ValueError("保证金模式只能是 cross 或 isolated")
        set_env("OKX_MARGIN_MODE", margin_mode)

        site = input("OKX 站点 [1=Global, 2=EEA, 3=US, Enter=保持]: ").strip()
        if site:
            urls = {"1": "https://www.okx.com", "2": "https://my.okx.com", "3": "https://app.okx.com"}
            if site not in urls:
                raise ValueError("站点选项只能是 1、2 或 3")
            set_env("OKX_BASE_URL", urls[site])

        print("请输入只具备 Read/Trade 权限、禁止 Withdraw 的 OKX API 凭证；终端不会显示字符。")
        for key, label in (
            ("OKX_API_KEY", "OKX API Key"),
            ("OKX_SECRET_KEY", "OKX Secret Key"),
            ("OKX_PASSPHRASE", "OKX Passphrase"),
        ):
            secret = getpass.getpass(f"{label}（留空保持原值）: ").strip()
            if secret:
                set_env(key, secret)
        print("OKX 配置已保存到本机 .env，不会上传 GitHub。建议先使用 OKX DEMO。")
        return

    current = cfg.get("ACCOUNT_ADDRESS", "")
    prompt = f"主 Hyperliquid 账户地址 [{masked_address(current)}]: " if current else "主 Hyperliquid 账户地址 (0x...): "
    address = input(prompt).strip()
    if address:
        if not address.startswith("0x"):
            raise ValueError("账户地址应以 0x 开头")
        set_env("ACCOUNT_ADDRESS", address)

    print("输入 API Wallet 私钥时终端不会显示字符。留空则保持原值。")
    secret = getpass.getpass("API Wallet Private Key: ").strip()
    if secret:
        if not secret.startswith("0x"):
            raise ValueError("API Wallet 私钥通常以 0x 开头")
        set_env("API_PRIVATE_KEY", secret)
    print("凭证只写入本机 .env，.gitignore 会阻止它上传 GitHub。")


def query_okx_funds(cfg: dict[str, str]) -> None:
    if not credentials_ready(cfg):
        print("请先在 3) 设置完整的 OKX API Key、Secret Key 和 Passphrase。")
        return
    client = OKXClient(
        base_url=cfg.get("OKX_BASE_URL", "https://www.okx.com") or "https://www.okx.com",
        api_key=cfg.get("OKX_API_KEY", ""),
        secret_key=cfg.get("OKX_SECRET_KEY", ""),
        passphrase=cfg.get("OKX_PASSPHRASE", ""),
        demo=cfg.get("OKX_DEMO", "true").lower() == "true",
        timeout=float(cfg.get("REQUEST_TIMEOUT", "15") or 15),
        retry_attempts=int(cfg.get("REQUEST_RETRY_ATTEMPTS", "3") or 3),
    )
    balances = client.get_private("/api/v5/account/balance")
    positions = client.get_private(
        "/api/v5/account/positions",
        {"instId": market_symbol(cfg)},
    )
    account = balances[0] if balances else {}
    currency = quote_currency(cfg)
    detail = next((item for item in account.get("details", []) if item.get("ccy") == currency), {})

    print("\n" + "-" * 58)
    print(f" OKX 资金查询 · {execution_mode(cfg).upper()}")
    print("-" * 58)
    print(f" 账户总权益    : {account.get('totalEq', '0')} USD")
    print(f" {currency} 权益     : {detail.get('eq', detail.get('cashBal', '0'))} {currency}")
    print(f" {currency} 可用     : {detail.get('availEq', detail.get('availBal', '0'))} {currency}")
    active = [row for row in positions if abs(float(row.get("pos") or 0)) > 0]
    if active:
        print(" 当前持仓:")
        for row in active:
            pos = float(row.get("pos") or 0)
            pos_side = row.get("posSide", "net")
            side = "SHORT" if pos_side == "short" or (pos_side == "net" and pos < 0) else "LONG"
            print(
                f"   {row.get('instId')} | {side} | contracts={abs(pos)} | "
                f"entry={row.get('avgPx')} | uPnL={row.get('upl')}"
            )
    else:
        print(" 当前持仓      : 无")
    print("-" * 58)


def query_funds() -> None:
    cfg = read_env()
    if selected_exchange(cfg) == "okx":
        query_okx_funds(cfg)
        return
    address = cfg.get("ACCOUNT_ADDRESS", "").strip()
    if not address:
        print("请先在 3) 设置 Hyperliquid 主账户地址。")
        return

    network = cfg.get("NETWORK", "mainnet").strip().lower()
    url = "https://api.hyperliquid.xyz/info" if network == "mainnet" else "https://api.hyperliquid-testnet.xyz/info"
    dex = cfg.get("DEX", "xyz").strip()

    def post_info(payload: dict) -> dict:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 429:
            raise RuntimeError("查询被 Hyperliquid 限流（429），稍后再试。")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Hyperliquid 返回了异常数据：{data}")
        return data

    def safe_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def print_perp_state(title: str, data: dict) -> None:
        summary = data.get("marginSummary") or {}
        account_value = safe_float(summary.get("accountValue"))
        margin_used = safe_float(summary.get("totalMarginUsed"))
        total_ntl = safe_float(summary.get("totalNtlPos"))
        withdrawable = safe_float(data.get("withdrawable"))

        positions = []
        for wrapper in data.get("assetPositions", []):
            p = wrapper.get("position", {})
            szi = safe_float(p.get("szi"))
            if abs(szi) > 1e-15:
                positions.append((p.get("coin", "?"), szi, p.get("entryPx"), p.get("unrealizedPnl")))

        print(f"\n {title}")
        print(f" 账户权益      : {account_value:.4f} USDC")
        print(f" 已用保证金    : {margin_used:.4f} USDC")
        print(f" 持仓名义价值  : {total_ntl:.4f} USDC")
        print(f" 可提/可用金额 : {withdrawable:.4f} USDC")
        if positions:
            print(" 当前持仓:")
            for coin, szi, entry, pnl in positions:
                side = "LONG" if szi > 0 else "SHORT"
                print(f"   {coin} | {side} | size={abs(szi)} | entry={entry} | uPnL={pnl}")
        else:
            print(" 当前持仓      : 无")

    # HyperCore / first perp DEX account. This is separate from HIP-3 xyz.
    core_state = post_info({
        "type": "clearinghouseState",
        "user": address,
    })

    # HIP-3 DEX used by this bot, e.g. xyz:XYZ100.
    hip3_state = post_info({
        "type": "clearinghouseState",
        "user": address,
        "dex": dex,
    })

    # Spot balances, including HYPE and spot USDC.
    spot_state = post_info({
        "type": "spotClearinghouseState",
        "user": address,
    })

    spot_balances = []
    for item in spot_state.get("balances", []):
        total = safe_float(item.get("total"))
        hold = safe_float(item.get("hold"))
        if abs(total) > 1e-15 or abs(hold) > 1e-15:
            spot_balances.append((str(item.get("coin", "?")), total, hold))

    print("\n" + "-" * 58)
    print(f" Hyperliquid 资金查询 · {masked_address(address)}")
    print("-" * 58)
    print_perp_state("HyperCore 永续账户", core_state)
    print_perp_state(f"HIP-3 永续账户 · DEX={dex}", hip3_state)

    print("\n Spot 现货余额")
    if spot_balances:
        # Put HYPE and USDC first for easier inspection.
        spot_balances.sort(key=lambda x: (0 if x[0] == "HYPE" else 1 if x[0] == "USDC" else 2, x[0]))
        for coin, total, hold in spot_balances:
            available = max(0.0, total - hold)
            print(f"   {coin:<10} total={total:.8f} | hold={hold:.8f} | available={available:.8f}")
    else:
        print("   无非零现货余额")
    print("-" * 58)


def switch_mode() -> None:
    cfg = read_env()
    if selected_exchange(cfg) == "okx":
        print("\n选择运行模式：")
        print(" 1) DRY RUN（本地空跑，不发送任何订单）")
        print(" 2) OKX DEMO（交易所模拟盘，会发送模拟订单）")
        print(" 3) LIVE（OKX 实盘，真实资金）")
        choice = input("请选择 [1/2/3]: ").strip()
        if choice == "1":
            set_env("DRY_RUN", "true")
            print("已切换为 DRY RUN。")
            return
        if choice not in {"2", "3"}:
            print("已取消。")
            return
        latest = read_env()
        if not credentials_ready(latest):
            print("无法切换：请先设置完整的 OKX API 凭证。")
            return
        if choice == "3":
            if input("即将启用 OKX 真实交易。如确认，请输入 LIVE: ").strip() != "LIVE":
                print("已取消。")
                return
            set_env("OKX_DEMO", "false")
            set_env("DRY_RUN", "false")
            print("已切换为 OKX LIVE。")
            return
        set_env("OKX_DEMO", "true")
        set_env("DRY_RUN", "false")
        print("已切换为 OKX DEMO 模拟盘。")
        return

    dry_run = cfg.get("DRY_RUN", "true").lower() == "true"
    if dry_run:
        print("\n你正在从 DRY RUN 切换到真实交易。")
        print("LIVE 模式会根据策略自动发送真实合约订单。")
        confirmation = input("如确认，请输入 LIVE: ").strip()
        if confirmation != "LIVE":
            print("已取消，仍保持 DRY RUN。")
            return
        latest = read_env()
        if not latest.get("ACCOUNT_ADDRESS") or not latest.get("API_PRIVATE_KEY"):
            print("无法切换：请先设置 ACCOUNT_ADDRESS 和 API_PRIVATE_KEY。")
            return
        set_env("NETWORK", "mainnet")
        set_env("DRY_RUN", "false")
        print("已切换为 LIVE。")
    else:
        set_env("DRY_RUN", "true")
        print("已切换为 DRY RUN，不会发送真实订单。")


def configure_direction() -> None:
    cfg = read_env()
    print(f"\n当前：做多={yn(cfg.get('ENABLE_LONGS', 'true'))}，做空={yn(cfg.get('ENABLE_SHORTS', 'true'))}")
    longs = input("允许做多? [Y/n]: ").strip().lower()
    shorts = input("允许做空? [Y/n]: ").strip().lower()
    set_env("ENABLE_LONGS", "false" if longs == "n" else "true")
    set_env("ENABLE_SHORTS", "false" if shorts == "n" else "true")
    print("已保存。")


def start_bot() -> None:
    cfg = read_env()
    mode = execution_mode(cfg)
    if mode != "dry_run":
        if not credentials_ready(cfg):
            print("当前模式缺少完整交易凭证，拒绝启动。")
            return
        mode_name = "OKX DEMO 模拟交易" if mode == "demo" else "LIVE 自动交易"
        print(f"\n即将启动 {mode_name}。按 Ctrl+C 可停止机器人并返回菜单。")
        if input("输入 START 确认: ").strip() != "START":
            print("已取消。")
            return
    else:
        print("\n启动 DRY RUN。按 Ctrl+C 可停止机器人并返回菜单。")

    try:
        subprocess.run([sys.executable, str(ROOT / "main.py")], cwd=str(ROOT), check=False)
    except KeyboardInterrupt:
        pass
    print("\n机器人已停止。")


def main() -> None:
    os.chdir(ROOT)
    ensure_env()
    while True:
        show_status()
        print(" 1) 启动机器人")
        print(" 2) 设置每笔仓位 / 杠杆")
        print(" 3) 设置交易所 / API 凭证")
        print(" 4) 切换 DRY RUN / DEMO / LIVE")
        print(" 5) 设置做多 / 做空方向")
        print(" 6) 刷新状态")
        print(" 7) 查询资金 / 当前持仓 / HYPE")
        print(" 0) 退出")
        choice = input("\n请选择: ").strip()
        try:
            if choice == "1":
                start_bot()
            elif choice == "2":
                configure_position()
            elif choice == "3":
                configure_credentials()
            elif choice == "4":
                switch_mode()
            elif choice == "5":
                configure_direction()
            elif choice == "6":
                continue
            elif choice == "7":
                query_funds()
            elif choice == "0":
                print("已退出。")
                return
            else:
                print("无效选项。")
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            print(f"操作失败：{exc}")
        input("\n按 Enter 返回菜单...")


if __name__ == "__main__":
    main()
