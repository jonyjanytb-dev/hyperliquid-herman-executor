# Security Policy / 安全说明

本项目会连接真实交易账户，因此请把凭证安全视为第一优先级。

## 不要公开的内容

请不要在 Issue、Pull Request、日志、截图或聊天中提交：

- `.env`
- Hyperliquid API Wallet Private Key
- 主钱包私钥 / 助记词
- OKX API Secret / Passphrase
- Gateway Token
- 任何具备提现权限的 API 凭证

仓库已经通过 `.gitignore` 排除 `.env`。如果凭证曾经被提交到 Git 历史，即使随后删除文件，也应当立即撤销/轮换该凭证。

## 推荐权限

### Hyperliquid

使用单独授权的 API Wallet 进行交易，不要把主钱包私钥放入机器人。

### OKX

API Key 只授予 `Read + Trade`；不要授予 `Withdraw`。条件允许时绑定固定 IP。

### Gateway

- 本机访问可以保持 `GATEWAY_HOST=127.0.0.1`
- LAN/VPS 监听非 loopback 地址时必须设置强随机 `GATEWAY_TOKEN`
- 不要直接把 HTTP `8787` 暴露到公网
- 公网访问使用 HTTPS、VPN/Tailscale、Cloudflare Tunnel 或受控反向代理

## LIVE 风险

LIVE 模式会发送真实交易订单。首次部署或更新后，应优先使用 DRY RUN / OKX DEMO，并人工验证：

- 交易账户和市场是否正确
- 名义仓位与杠杆是否正确
- 开仓后 SL 是否成功存在
- TP 是否成功存在并按预期更新
- 重启恢复逻辑是否能识别已有仓位和保护单

如果检测到真实持仓但无法恢复 Stop Loss，本项目的设计目标是拒绝继续自动管理/开新仓，而不是假设保护状态正常。

## 报告安全问题

如果发现可能泄露凭证、绕过 Gateway Token、错误扩大真实仓位、删除保护单或导致重复下单的缺陷，请不要在公开 Issue 中附带真实密钥或账户机密。

可以创建不包含秘密信息的最小复现 Issue；敏感细节请先撤销相关凭证，再通过仓库所有者认可的私密渠道提供。
