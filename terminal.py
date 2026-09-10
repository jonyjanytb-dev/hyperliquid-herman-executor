from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

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
    dry_run = cfg.get("DRY_RUN", "true").lower() == "true"
    notional = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    leverage = int(cfg.get("LEVERAGE", "0") or 0)
    if leverage > 0:
        margin = f"约 {notional / leverage:.2f} USDC"
        lev_text = f"{leverage}x"
    else:
        margin = "由账户当前杠杆决定"
        lev_text = "不自动修改"

    print("\n" + "=" * 58)
    print(" Hyperliquid Herman Executor · 本地交互终端")
    print("=" * 58)
    print(f" 模式        : {'DRY RUN（模拟，不下真钱）' if dry_run else 'LIVE（真实下单）'}")
    print(f" 市场        : {cfg.get('COIN', 'xyz:XYZ100')}")
    print(f" 周期        : {cfg.get('INTERVAL', '1m')}")
    print(f" 每笔名义仓位: {notional:.2f} USDC")
    print(f" 杠杆        : {lev_text}")
    print(f" 预计保证金  : {margin}")
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
    print("\n说明：名义仓位=实际合约持仓价值。例：250U仓位 + 5x ≈ 50U保证金。")
    notional = ask_float("每笔名义仓位 USDC", notional)
    leverage = ask_int("杠杆（0=不自动修改账户杠杆）", leverage, 0)
    set_env("ORDER_NOTIONAL_USDC", str(notional))
    set_env("LEVERAGE", str(leverage))
    print("已保存。")


def configure_credentials() -> None:
    cfg = read_env()
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


def switch_mode() -> None:
    cfg = read_env()
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
    dry_run = cfg.get("DRY_RUN", "true").lower() == "true"
    if not dry_run:
        if not cfg.get("ACCOUNT_ADDRESS") or not cfg.get("API_PRIVATE_KEY"):
            print("LIVE 模式缺少账户/API Wallet 凭证，拒绝启动。")
            return
        print("\n即将启动 LIVE 自动交易。按 Ctrl+C 可停止机器人并返回菜单。")
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
        print(" 3) 设置 Hyperliquid 账户 / API Wallet")
        print(" 4) 切换 DRY RUN / LIVE")
        print(" 5) 设置做多 / 做空方向")
        print(" 6) 刷新状态")
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
            elif choice == "0":
                print("已退出。")
                return
            else:
                print("无效选项。")
        except (ValueError, OSError) as exc:
            print(f"操作失败：{exc}")
        input("\n按 Enter 返回菜单...")


if __name__ == "__main__":
    main()
