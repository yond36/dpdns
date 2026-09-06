# DigitalPlat 域名到期检查与通知

一个基于 GitHub Actions 的自动化脚本，每月自动检查 **DigitalPlat** 账号下的免费域名有效期，当有域名剩余有效期 **少于 120 天** 时，通过 **Telegram** 或 **Bark** 推送提醒。

> **注意**：DigitalPlat 的公开 API 并未开放续期接口（`POST /domains/{domain}/renew` 会返回 `404 registered_domain_not_found`），因此本脚本只负责**检查与提醒**，续期需前往 [Dashboard](https://dash.domain.digitalplat.org/dashboard) 手动操作。

## 工作原理

每月定时（月度第 1 天 04:17 UTC）触发一次工作流：

1. 通过 DigitalPlat Domain API 拉取域名清单：`GET /api/v1/domains`
2. 默认选取所有 `slot_type = free` 的免费域名；若配置了 `DIGITALPLAT_DOMAINS`，则只检查指定域名
3. 计算每个域名的剩余有效天数
4. 将「剩余天数 ≤ 阈值（默认 `120`）」的域名汇总为提醒消息
5. 通过 Telegram Bot 和/或 Bark 推送通知；同时在 Actions 日志打印检查结果

不需要任何第三方依赖，仅使用 Python 标准库。

## 如何 Fork 并使用

### 第 1 步：创建 DigitalPlat API Key

打开 [DigitalPlat Dashboard → API Keys](https://dash.domain.digitalplat.org/dashboard/api/keys)，创建一个 `dp_live_...` 开头的生产 API Key。

### 第 2 步：Fork 本仓库

点击页面右上角 **Fork**，把本仓库复制到你的账号下。

### 第 3 步：配置 Secret 和 Variable

进入 `Settings → Secrets and variables → Actions`：

**Secret**

| 名称 | 必填 | 说明 |
| --- | --- | --- |
| `DIGITALPLAT_API_TOKEN` | ✅ | DigitalPlat 的 `dp_live_...` API Key |
| `TELEGRAM_BOT_TOKEN` | 可选 | Telegram Bot Token（来自 @BotFather） |
| `TELEGRAM_CHAT_ID` | 可选 | 接收通知的 Telegram Chat ID |
| `BARK_KEY` | 可选 | Bark 推送 Key |

**Variable（均可选，默认值已可用）**

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `DIGITALPLAT_DOMAINS` | 空 | 只检查指定域名，一行一个，可用逗号分隔；**留空则检查所有免费域名** |
| `DIGITALPLAT_RENEW_BEFORE_DAYS` | `120` | 剩余天数小于等于该值则标记为需续期 |
| `BARK_SERVER` | `https://api.day.app` | Bark 自建服务器地址（可选） |

> Telegram 和 Bark 二选一或同时配置均可；都不配置时脚本只打印日志，不发通知。

### 第 4 步：手动跑一次验证

打开 `Actions` 页，选中 **DigitalPlat Domain Check & Notify** → **Run workflow**，确认日志输出：

```
MODE: check all free domains (1 eligible)
[CHECK] example.dpdns.org expires=2027-06-04 days_left=279 status=ok slot=free renewal=no
[SUMMARY] checked=1 needing_renewal=0
[NOTIFY] Telegram sent
[NOTIFY] Bark sent
```

## 定时说明

工作流由 `.github/workflows/digitalplat-renew.yml` 中的 cron 控制：

```yaml
on:
  schedule:
    - cron: "17 4 1 * *"   # 每月第 1 天 04:17 UTC
  workflow_dispatch:        # 支持手动触发
```

如需调整频率，修改该文件的 `cron` 表达式即可。

## 目录结构

```
├── .github/workflows/digitalplat-renew.yml   # 月度定时工作流
├── scripts/check_domains.py                  # 检查与通知脚本
└── .gitignore
```

## API 说明

- Base URL：`https://domain-api.digitalplat.org/api/v1`（可用 `DIGITALPLAT_API_BASE` 环境变量覆盖）
- 鉴权：`Authorization: Bearer <API Key>`
- 使用接口：
  - `GET /domains` — 拉取域名清单

## 通知渠道

- **Telegram**：调用 Bot API `sendMessage`，需要 `TELEGRAM_BOT_TOKEN` 与 `TELEGRAM_CHAT_ID`
- **Bark**：调用 Bark 推送服务，需要 `BARK_KEY`（可用 `BARK_SERVER` 指定自建服务器）

## 注意事项

- **安全**：API Key、Telegram Token、Bark Key 请放在 GitHub **Secret** 中，切勿写入源码或提交到仓库。
- **User-Agent**：DigitalPlat 网关（Cloudflare）会拦截类似机器人的自定义 User-Agent（返回 403 Challenge）。脚本默认使用浏览器风格的 UA，如需自定义可设置 `DIGITALPLAT_USER_AGENT`。
- **续期窗口**：平台通常只在剩余有效期低于约 180 天时才允许续期，且 API 未开放续期接口，请在收到提醒后前往 Dashboard 手动续期。