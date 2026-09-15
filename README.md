# Herman Executor · Trend Rebalance Map 自动交易落地版

> 将 HermanTrading / @RHerman 公开的 **Trend Rebalance Map [Herman] v1.1** 从 TradingView Pine Script 落地为可本地/VPS运行的 Python 自动交易系统，并扩展了 Hyperliquid、OKX、原生保护单、运行状态恢复和只读手机监控面板。

[![License: MPL 2.0](https://img.shields.io/badge/License-MPL%202.0-brightgreen.svg)](LICENSE)

## 中文简介

这个项目不是一个新的交易策略，而是对 HermanTrading 公开策略的**工程化落地实现**。

原策略在 TradingView/Pine Script 中定义信号、方向过滤、止盈止损等规则；本项目把这些规则翻译为 Python 执行引擎，让策略可以脱离 TradingView Webhook，在自己的 Mac、Linux 或 VPS 上持续读取 1 分钟收盘 K 线，并连接交易所执行。

目前主要能力：

- **Hyperliquid HIP-3**：默认市场 `xyz:XYZ100`
- **OKX**：线性 USDT/USDC 永续合约适配
- 1 分钟已收盘 K 线运行，避免未收盘信号重绘
- 保留 SMA50 / SMA200、方向过滤、最小均线分离距离等原始逻辑
- 一次只管理一个仓位，持仓期间忽略反向信号
- 默认 `200 SMA / Dynamic` 动态止盈
- 默认 `125 points` 固定止损
- 交易所原生 TP / SL 保护单
- Hyperliquid / OKX 本地交互式终端
- 重启后恢复已有持仓和保护单状态
- **Herman Gateway v1**：手机/浏览器只读监控资金、持仓、TP/SL、成交和收益分析
- DRY RUN / OKX DEMO / LIVE 三种运行方式

## 界面预览

本项目提供两套互补的使用界面：一套用于本地执行与配置，一套用于手机 / 浏览器只读监控。

### 1. 本地终端交互页面

用于在本机或 VPS 上直接管理策略运行状态，支持启动机器人、设置每笔仓位与杠杆、配置 Hyperliquid / OKX API 凭证、切换 DRY RUN / DEMO / LIVE、设置做多/做空方向，以及查询资金和当前持仓。

![Herman Executor 本地终端交互页面](docs/images/terminal-console.jpg)

### 2. 手机 / Web Gateway

用于手机或浏览器实时查看策略运行情况。可查看账户资金、当前持仓、交易所原生 Dynamic TP / Stop Loss、最近成交、未实现 PnL，以及 24H / 7D / 30D 收益、胜率、Profit Factor、手续费和平均盈亏等统计数据。

![Herman Gateway 手机 / Web 监控面板](docs/images/web-gateway.jpg)

### 策略来源与致谢

策略原作者：**HermanTrading / @RHerman**。

- 原策略 X 帖子：<https://x.com/RHerman/status/2098093808286093742>
- HermanTrading 官方 GitHub：<https://github.com/HermanTrading/Trend-Rebalance-Map-Herman->
- 本仓库保留的原始 Pine 参考源码：[`reference/Trend_Rebalance_Map_Herman.pine`](reference/Trend_Rebalance_Map_Herman.pine)

原始 Pine 文件头明确标注 `© HermanTrading`，并采用 **Mozilla Public License 2.0 (MPL-2.0)**。本项目保留原版权和许可证声明，并以同一 MPL-2.0 许可证开放源码。

**本项目与 HermanTrading 无官方隶属或合作关系。策略思想和原始 Pine 源码归原作者；本仓库的工作重点是交易所执行、风控保护、状态管理、终端和监控面板等工程实现。**

## 策略逻辑

实现有意保留原 Pine v1.1 的默认规则：

- `SMA50` / `SMA200`
- LONG：收盘价上穿 SMA50，且 `SMA50 < SMA200`
- SHORT：收盘价下穿 SMA50，且 `SMA50 > SMA200`
- 两条均线分离距离必须大于默认 `30 points`
- 只使用已确认收盘 K 线
- 同时只允许一个仓位
- 持仓中忽略相反方向的新信号
- 默认 TP：`200 SMA / Dynamic`
- 默认 SL：`Fixed Points / 125`
- 平仓同一根 K 线禁止重新进场

> Dynamic TP 的含义是：持仓期间目标价随每根已收盘 K 线计算出的 SMA200 更新；它不是传统意义上只能单向移动的 trailing stop。

## 架构

```text
交易所 1m K线
   │
   ▼
Herman Pine 逻辑 Python 化
   │
   ▼
交易所执行适配层
   ├── Hyperliquid HIP-3
   └── OKX Perpetual Swap
   │
   ├── Market Entry
   ├── Native Stop Loss
   └── Dynamic / Fixed Take Profit
   │
   ▼
本地 Runtime State
   │
   └── Herman Gateway（只读）
        ├── 资金
        ├── 当前持仓
        ├── TP / SL
        ├── 最近成交
        └── PnL / 胜率 / Profit Factor
```

不要求 TradingView 付费套餐，也不依赖 TradingView Webhook。

## 快速开始

macOS / Linux：

```bash
git clone https://github.com/jonyjanytb-dev/hyperliquid-herman-executor.git
cd hyperliquid-herman-executor
bash run_local.sh
```

`run_local.sh` 会自动：

1. 创建 `.venv`
2. 安装依赖
3. 首次运行时从 `.env.example` 创建本地 `.env`
4. 打开交互式终端

`.env` 已被 `.gitignore` 排除，**不要把 API Key、API Wallet 私钥或 Passphrase 提交到 GitHub**。

## 本地交互终端

```text
Herman Executor · Hyperliquid / OKX 本地交互终端

1) 启动机器人
2) 设置每笔仓位 / 杠杆
3) 设置交易所 / API 凭证
4) 切换 DRY RUN / DEMO / LIVE
5) 设置做多 / 做空方向
6) 刷新状态
7) 查询资金 / 当前持仓 / HYPE
0) 退出
```

默认 `DRY_RUN=true`，不会真实下单。切换 LIVE 需要明确确认。

## Hyperliquid

建议使用单独创建、仅具备交易权限的 **API Wallet**：

```env
EXCHANGE=hyperliquid
NETWORK=mainnet
DEX=xyz
COIN=xyz:XYZ100
ACCOUNT_ADDRESS=0x...
API_PRIVATE_KEY=0x...
ORDER_NOTIONAL_USDC=100
```

不要把主钱包私钥放进机器人。API Wallet 不应具备提现能力。

HIP-3 默认使用 `perp_dexs=["xyz"]`，目标市场为 `xyz:XYZ100`。

## OKX

当前适配器面向线性 USDT/USDC 永续合约（`*-SWAP`）。默认配置：

```env
EXCHANGE=okx
OKX_INST_ID=US100-USDT-SWAP
OKX_MARGIN_MODE=cross
OKX_DEMO=true
DRY_RUN=false
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_PASSPHRASE=
```

上面的组合是 **OKX DEMO**。只有同时满足：

```env
DRY_RUN=false
OKX_DEMO=false
```

才会向 OKX 生产环境发送真实订单。

API Key 建议只授予 **Read + Trade**，不要授予 Withdraw，并在可行时绑定固定 IP。

## TP / SL 与状态恢复

### Hyperliquid

- 入场后优先创建 SL，再创建 TP
- Trigger price 会按交易所允许的价格精度归一化
- Dynamic TP 优先原地修改已有 TP
- 修改失败时先创建替代 TP，再取消旧 TP，减少保护空窗
- 重启发现已有仓位时会尝试恢复交易所上的 TP / SL
- 如果无法恢复 SL，机器人拒绝继续管理/新开仓，避免在未知保护状态下继续交易

### OKX

- 初始 TP / SL 使用交易所原生 Algo/OCO 保护
- Dynamic 200-SMA TP 随已确认 K 线更新
- 重启后恢复等待中的交易所保护单再继续管理仓位

## Herman Gateway v1 · 手机/网页只读面板

网关与交易进程分离。即使网页关闭或 Gateway 崩溃，也不会主动修改策略仓位。

它可以查看：

- 当前交易所 / 市场 / LIVE-DEMO-DRY RUN
- 账户可用资金
- 当前 LONG / SHORT、Entry、Mark/Last、杠杆、未实现 PnL
- 交易所原生 TP / SL
- 24H / 7D / 30D 已实现 PnL
- 手续费、胜率、Profit Factor
- 最近成交
- Bot 本地 heartbeat / managed position 状态

第二个终端运行：

```bash
cd ~/hyperliquid-herman-executor
bash run_gateway.sh
```

本机访问：

```text
http://127.0.0.1:8787
```

局域网/手机访问时：

```env
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8787
GATEWAY_TOKEN=至少16位随机密码
```

**不要把 8787 端口以明文 HTTP 直接暴露到公网。** 公网部署请使用 HTTPS、Tailscale/VPN、Cloudflare Tunnel 或反向代理，并设置强随机 Token。

Gateway v1 是只读的，没有远程开仓、平仓、改杠杆或修改策略参数的 API。

## Docker / VPS

直接运行：

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Docker：

```bash
cp .env.example .env
docker build -t herman-executor .
docker run --env-file .env herman-executor
```

长期 VPS 运行建议配合 `systemd` / Docker restart policy，并把 runtime state 放到持久化磁盘。

## 测试

```bash
python -m pytest -q
```

仓库包含策略、交易所适配、保护单、配置和 Gateway 分析相关测试；GitHub Actions 也会在 push / pull request 时自动执行测试。

## 执行差异说明

Pine 使用 `process_orders_on_close=true`，回测把进场建模在信号 K 线收盘价。

实盘程序是在发现该 K 线已经收盘后立即发送市价/主动订单，因此真实成交价可能与 Pine 的 signal-bar close 有滑点。为了保持规则一致，策略的 TP / SL 参考仍按原 Pine 的信号 K 线逻辑计算。

## 安全说明

- 永远不要提交 `.env`
- 永远不要把钱包私钥/API Secret 写进 Issue、日志或截图
- Hyperliquid 使用独立 API Wallet，不使用主钱包私钥
- OKX API 禁止 Withdraw 权限
- 第一次部署优先使用 DRY RUN / DEMO
- 切换 LIVE 前人工确认仓位、杠杆和保护单逻辑
- Gateway 公网访问必须使用 HTTPS + Token

更详细的安全披露方式见 [`SECURITY.md`](SECURITY.md)。

## 开源许可

本项目采用 **Mozilla Public License 2.0 (MPL-2.0)**。

原因是上游 Trend Rebalance Map [Herman] Pine 源码本身采用 MPL-2.0；本仓库继续使用同一许可证，保留 `© HermanTrading` 原始声明，并明确区分策略原创与工程实现。

详见 [`LICENSE`](LICENSE) 和 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 风险声明

本项目仅用于研究、工程实现和自动化交易实验，不构成投资建议、收益承诺或任何形式的资金管理服务。

历史回测、胜率、Profit Factor 或历史 PnL 不代表未来结果。LIVE 模式可以发送真实订单，可能产生部分成交、滑点、手续费、API 故障、网络故障、保护单失败、强平以及本金全部损失。使用者需要自行理解代码、验证交易所规则并承担全部交易风险。
