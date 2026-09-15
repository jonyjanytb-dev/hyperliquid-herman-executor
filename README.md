# Herman Executor · Hyperliquid / OKX

Cloud/local executor for **Trend Rebalance Map [Herman] v1.1**. It supports Hyperliquid HIP-3 and OKX linear USDT/USDC perpetual swaps using one-minute closed candles.

## Architecture

`Selected exchange 1m candles -> Pine logic translated 1:1 -> exchange adapter -> entry + exchange-native TP/SL`

No TradingView subscription or webhook is required.

## Strategy logic preserved

The implementation intentionally preserves the uploaded Pine defaults and rules:

- SMA50 / SMA200
- long: close crosses above SMA50, SMA50 < SMA200, separation > 30 points
- short: close crosses below SMA50, SMA50 > SMA200, separation > 30 points
- closed bars only
- one position at a time
- opposite signals ignored while a trade is open
- default TP: 200 SMA, Dynamic
- default SL: 125 points
- same-bar re-entry blocked after an exit

The original Pine source is included under `reference/`.

## Safety default

`DRY_RUN=true` and `EXCHANGE=hyperliquid` remain the defaults. Upgrading does not switch exchanges or place orders. OKX also has a separate DEMO mode that sends orders only to OKX simulated trading.

## Local interactive terminal (recommended)

On macOS/Linux, after cloning the repository run:

```bash
git clone https://github.com/jonyjanytb-dev/hyperliquid-herman-executor.git
cd hyperliquid-herman-executor
bash run_local.sh
```

`run_local.sh` automatically creates `.venv`, installs dependencies, creates a local `.env` from `.env.example` when needed, and opens the interactive terminal.

The terminal can:

- start/stop the bot in the foreground
- select Hyperliquid or OKX and show the active exchange, mode and market
- show position notional, leverage and estimated margin
- edit `ORDER_NOTIONAL_USDC` and `LEVERAGE`
- enter Hyperliquid or OKX credentials locally (secret input is hidden)
- switch between DRY RUN, OKX DEMO and LIVE with explicit confirmation
- enable/disable long or short execution
- query the selected exchange's balance and current position

The local `.env` is ignored by Git and should never be committed.

## Run locally without the interactive terminal

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Or Docker:

```bash
cp .env.example .env
docker build -t herman-hl .
docker run --env-file .env herman-hl
```

## Hyperliquid credentials

Use an **API Wallet** authorized for trading. Put:

- `ACCOUNT_ADDRESS`: your main Hyperliquid account address
- `API_PRIVATE_KEY`: the API Wallet private key

Do not use a withdrawal-capable secret in the bot.

For HIP-3 the SDK is initialized with `perp_dexs=["xyz"]` and the coin is `xyz:XYZ100`.

## OKX configuration

The first OKX release supports linear USDT/USDC perpetual swaps (`*-SWAP`). The bot checks the selected instrument's live status and reads `ctVal`, `lotSz`, `minSz` and `tickSz` before trading, so quote-currency notional is converted into valid contract quantities and prices.

The configured OKX counterpart for this strategy is the USDT-settled US100 Index Perpetual, `US100-USDT-SWAP`. OKX product availability is region-dependent; startup stops safely if the selected account/site cannot access the instrument.

Recommended local setup:

1. Run `bash run_local.sh`.
2. Choose `3) 设置交易所 / API 凭证`, then select OKX. This strategy defaults to the US100 perpetual instrument ID `US100-USDT-SWAP`.
3. Use an API key with **Read + Trade** permissions only. Do not grant **Withdraw** permission. Bind the key to your IP where practical.
4. Choose `4) 切换 DRY RUN / DEMO / LIVE` and test in **OKX DEMO** first.
5. Query funds/positions, then start the bot and inspect its logs before considering LIVE.

The terminal supports the Global (`www.okx.com`), EEA (`my.okx.com`) and US (`app.okx.com`) OKX origins. API key, secret and passphrase stay in the ignored local `.env` file.

For OKX positions, initial TP and SL are submitted together as an exchange-native OCO algo order. In Dynamic 200-SMA mode, the TP trigger is amended in place after each confirmed closed candle; `cxlOnFail=false` keeps the previous TP and SL working if an amendment fails. After restart, the bot retrieves pending conditional/OCO protection before managing an existing position.

OKX spot, dated futures, options, inverse swaps and simultaneous long+short positions on the same instrument are intentionally not supported in this release.

## Switch to Hyperliquid live trading

After dry-run logs look correct:

```env
DRY_RUN=false
NETWORK=mainnet
DEX=xyz
COIN=xyz:XYZ100
ACCOUNT_ADDRESS=0x...
API_PRIVATE_KEY=0x...
ORDER_NOTIONAL_USDC=100
```

For OKX, use the interactive terminal so DEMO and LIVE cannot be confused. The equivalent configuration is:

```env
EXCHANGE=okx
OKX_INST_ID=US100-USDT-SWAP
OKX_MARGIN_MODE=cross
OKX_DEMO=true
DRY_RUN=false
OKX_API_KEY=...
OKX_SECRET_KEY=...
OKX_PASSPHRASE=...
```

This example is **OKX DEMO**, not live. Only `OKX_DEMO=false` together with `DRY_RUN=false` enables OKX production orders.

## Herman Gateway v1 · mobile read-only dashboard

The repository includes a separate **read-only** mobile dashboard. It does not modify the Herman strategy and it has no endpoint for opening/closing trades, changing leverage, changing TP/SL, or changing strategy parameters.

It reads current account/position/protection data from the selected exchange and reads the bot's local runtime state file. Historical PnL uses exchange fill history:

- Hyperliquid: `clearinghouseState`, `spotClearinghouseState`, `frontendOpenOrders`, `allMids`, and `userFillsByTime`
- OKX: account balance/positions, pending algo protection, ticker, and `fills-history`

The dashboard shows:

- exchange / market / LIVE-DEMO-DRY RUN mode
- account availability and selected balances
- current LONG / SHORT position, entry, mark/last price, leverage and unrealized PnL
- exchange-native TP / SL trigger orders
- 24H / 7D / 30D realized PnL, fees, closing-fill win rate and Profit Factor
- recent exchange fills
- local bot state heartbeat and managed position state

Run it in a second terminal:

```bash
cd ~/hyperliquid-herman-executor
bash run_gateway.sh
```

Local-only defaults:

```env
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=8787
GATEWAY_TOKEN=
```

Then open `http://127.0.0.1:8787`.

For a VPS, LAN or phone-accessible deployment, bind to all interfaces and set a strong token:

```env
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8787
GATEWAY_TOKEN=replace-with-a-long-random-secret
```

When `GATEWAY_HOST` is not loopback, startup refuses to run unless `GATEWAY_TOKEN` is at least 16 characters. The browser sends this token only to the gateway API and stores it in that browser's local storage.

**Do not expose port 8787 directly to the public internet over plain HTTP.** Put the gateway behind HTTPS (for example a reverse proxy, VPN/Tailscale, or Cloudflare Tunnel) and restrict firewall access where possible.

The dashboard is intentionally independent of the trading process: stopping/restarting the web gateway does not stop the strategy bot, and a dashboard failure does not alter exchange orders.

## Railway deployment

1. Put this folder in a GitHub repo.
2. Create a Railway service from the repo.
3. Add the `.env` values as Railway Variables. Do **not** upload `.env` to GitHub.
4. Deploy. `railway.toml` and `Dockerfile` are included.
5. Keep `DRY_RUN=true` for the first deployment; only switch it off after checking logs.

If you want state to persist across container replacement, attach a Railway volume and set `STATE_PATH` to a path on that volume (for example `/data/state-okx.json`). When `STATE_PATH` is blank, the bot uses a separate local state file for each exchange. The bot also attempts to recover an existing position and its exchange-native protection after a restart.

## Notes on execution parity

Pine models the entry at the signal-bar close (`process_orders_on_close=true`). A live API market order is sent immediately after the closed candle is observed, so the actual fill can differ from that close. The strategy's TP/SL reference remains based on the signal-bar close to preserve the Pine calculation.
