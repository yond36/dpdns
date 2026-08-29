# DigitalPlat 域名自动续期

一个基于 GitHub Actions 的自动化脚本，每月自动检查 **DigitalPlat** 账号下的域名，当剩余有效期 **少于 120 天** 时自动发起续期请求。默认自动获取账号下 **所有免费域名** 并续期；也支持通过 `DIGITALPLAT_DOMAINS` Variable 自定义只续期指定域名。

## 工作原理

每月定时（每次月度第 1 天 04:17 UTC）触发一次工作流：

1. 通过 DigitalPlat Domain API 拉取域名清单：`GET /api/v1/domains`
2. 默认选取所有 `slot_type = free` 的免费域名；若配置了 `DIGITALPLAT_DOMAINS`，则只处理指定域名
3. 计算每个目标域名的剩余有效天数
4. 如果剩余天数 ≤ 阈值（默认 `120`），调用 `POST /api/v1/domains/{domain}/renew` 进行续期（默认 `renewal_type=free`, `years=1`）
5. 打印检查与续期结果，供 Actions 日志查看

不需要任何第三方依赖，仅使用 Python 标准库。

## 如何 Fork 并使用

### 第 1 步：创建 DigitalPlat API Key

打开 [DigitalPlat Dashboard → API Keys](https://dash.domain.digitalplat.org/dashboard/api/keys)，创建一个 `dp_live_...` 开头的生产 API Key。

### 第 2 步：Fork 本仓库

点击页面右上角 **Fork**，把本仓库复制到你的账号下。

### 第 3 步：配置 Secret 和 Variable

进入 `Settings → Secrets and variables → Actions`：

**Secret（必填）**

| 名称 | 说明 |
| --- | --- |
| `DIGITALPLAT_API_TOKEN` | DigitalPlat 的 `dp_live_...` API Key |

**Variable（均可选，默认值已可用）**

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `DIGITALPLAT_DOMAINS` | 空 | 指定要续期的域名，一行一个，可用逗号分隔；**留空则自动续期所有免费域名** |
| `DIGITALPLAT_RENEW_BEFORE_DAYS` | `120` | 剩余天数小于等于该值才续期 |
| `DIGITALPLAT_RENEWAL_TYPE` | `free` | 续期类型 |
| `DIGITALPLAT_RENEWAL_YEARS` | `1` | 续期年数 |
| `DRY_RUN` | 空 | 设为 `1` / `true` 时只检查不续期 |

### 第 4 步：手动跑一次验证

打开 `Actions` 页，选中 **DigitalPlat Domain Auto-Renew** → **Run workflow**，确认日志输出：

```
MODE: auto-renew all free domains (1 eligible)
[CHECK] example.dpdns.org expires=2027-06-04 days_left=279 status=ok slot=free
[SKIP] example.dpdns.org not yet within renewal window
```

若域名已进入续期窗口，则会输出：

```
[RENEWED] example.dpdns.org new_expires=...
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
├── scripts/renew_domain.py                   # 核心续期脚本
└── .gitignore
```

## API 说明

- Base URL：`https://domain-api.digitalplat.org/api/v1`（可用 `DIGITALPLAT_API_BASE` 环境变量覆盖）
- 鉴权：`Authorization: Bearer <API Key>`
- 使用接口：
  - `GET /domains` — 拉取域名清单
  - `POST /domains/{domain}/renew` — 续期

## 注意事项

- **安全**：API Key 请放在 GitHub **Secret** 中，切勿写入源码或提交到仓库。
- **User-Agent**：DigitalPlat 网关（Cloudflare）会拦截类似机器人的自定义 User-Agent（返回 403 Challenge）。脚本默认使用浏览器风格的 UA，如需自定义可设置 `DIGITALPLAT_USER_AGENT`。
- **免费续期**：免费域名默认通过 `renewal_type=free` 续期，无需支付费用。
- 若账号下已有免费续期窗口或冷却限制，重复请求可能被平台拒绝，脚本会将其记录为错误并继续处理其他域名。
