# LPT Daily Stock Analysis 部署说明

本仓库用于每天自动分析自选股，并把结果推送到微信提醒和飞书群。

## 定时推送

GitHub Actions 已调整为每天北京时间：

- 08:00
- 15:00

对应 UTC：

- `0 0 * * *`
- `0 7 * * *`

仓库仍会执行交易日检查；非交易日通常不会生成正式交易日报。需要测试时，可在 GitHub Actions 手动运行并勾选 `force_run`。

## 必填配置

在 GitHub 仓库：

`Settings -> Secrets and variables -> Actions`

至少配置：

### Secrets

- `PUSHPLUS_TOKEN`：微信 PushPlus 推送 token
- `FEISHU_WEBHOOK_URL`：飞书群机器人 webhook
- 至少一个 AI Key：
  - `GEMINI_API_KEY`
  - `AIHUBMIX_KEY`
  - `OPENAI_API_KEY`
  - `DEEPSEEK_API_KEY`
  - `ANSPIRE_API_KEYS`

### Variables

- `STOCK_LIST`：自选股代码，例如 `600118,300750,002594`
- `REPORT_TYPE`：建议 `simple`
- `MARKET_REVIEW_ENABLED`：建议 `true`
- `REPORT_LANGUAGE`：建议留空或填 `zh`

## 手动测试

进入 GitHub 仓库：

`Actions -> 每日股票分析 -> Run workflow`

推荐测试：

- `mode`: `full`
- `force_run`: 勾选

如果配置正确，运行结束后会收到 PushPlus 微信提醒和飞书群消息。

## 修改股票列表

修改 GitHub Actions Variables 里的：

`STOCK_LIST`

多个股票用英文逗号分隔。

示例：

```text
600118,300750,002594
```

## 注意

本项目输出是 AI 辅助分析，不构成投资建议。正式交易前仍需结合公告、财报、市场环境、仓位纪律和个人风险承受能力人工确认。
