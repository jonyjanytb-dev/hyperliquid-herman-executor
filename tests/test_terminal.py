import terminal
from terminal import credentials_ready, execution_mode, market_symbol, quote_currency


def test_terminal_mode_distinguishes_okx_demo_from_live():
    base = {"EXCHANGE": "okx", "OKX_INST_ID": "BTC-USDT-SWAP"}

    assert execution_mode(dict(base, DRY_RUN="true", OKX_DEMO="true")) == "dry_run"
    assert execution_mode(dict(base, DRY_RUN="false", OKX_DEMO="true")) == "demo"
    assert execution_mode(dict(base, DRY_RUN="false", OKX_DEMO="false")) == "live"


def test_terminal_okx_credentials_require_key_secret_and_passphrase():
    incomplete = {"EXCHANGE": "okx", "OKX_API_KEY": "key", "OKX_SECRET_KEY": "secret"}
    complete = dict(incomplete, OKX_PASSPHRASE="pass")

    assert credentials_ready(incomplete) is False
    assert credentials_ready(complete) is True


def test_terminal_market_and_quote_currency_follow_selected_exchange():
    okx = {"EXCHANGE": "okx", "OKX_INST_ID": "BTC-USDT-SWAP"}
    hyperliquid = {"EXCHANGE": "hyperliquid", "COIN": "xyz:XYZ100"}

    assert market_symbol(okx) == "BTC-USDT-SWAP"
    assert quote_currency(okx) == "USDT"
    assert market_symbol(hyperliquid) == "xyz:XYZ100"
    assert quote_currency(hyperliquid) == "USDC"


def test_terminal_defaults_okx_target_to_us100_perpetual():
    assert market_symbol({"EXCHANGE": "okx"}) == "US100-USDT-SWAP"
    assert quote_currency({"EXCHANGE": "okx"}) == "USDT"


def test_switching_exchange_forces_dry_run_before_collecting_new_credentials(monkeypatch):
    values = {
        "EXCHANGE": "hyperliquid",
        "DRY_RUN": "false",
        "STATE_PATH": "runtime/state-hyperliquid.json",
        "OKX_INST_ID": "",
    }
    writes = []

    def set_value(key, value):
        values[key] = value
        writes.append((key, value))

    answers = iter(["2", "", "", ""])
    monkeypatch.setattr(terminal, "read_env", lambda: dict(values))
    monkeypatch.setattr(terminal, "set_env", set_value)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(terminal.getpass, "getpass", lambda _: "")

    terminal.configure_credentials()

    assert ("DRY_RUN", "true") in writes
    assert ("OKX_INST_ID", "US100-USDT-SWAP") in writes
