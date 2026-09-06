# DigitalPlat 免费域名自动续期脚本

这是一个使用 Python 和 Playwright 编写的脚本，旨在自动续期您在 [DigitalPlat](https://dashboard.digitalplat.org/) 上的免费域名。脚本通过 GitHub Actions 实现云端定时运行，无需您自己准备服务器。

## ✨ 工作原理

脚本会模拟真人操作：
1. 启动一个真实 Chrome 浏览器（headless，`channel="chrome"`）。
2. 访问 DigitalPlat 登录页面。若出现 Cloudflare 人机验证，脚本会通过 **2captcha** 自动解决（拦截 `turnstile.render` 提取参数 → `TurnstileTaskProxyless` 求解 → 执行回调）。
3. 解决登录表单自带的 Turnstile 组件，直接调用面板 API 完成登录（携带 `panel_csrf_token`）。
4. 通过 `/_panel_api/api/domains` 获取域名列表。
5. 对每个可续期的域名调用 `/_panel_api/api/domains/{域名}/renew` 完成免费续期（`renewal_type=free, years=1`）。

## 🚀 如何使用

1.  **Fork 本项目**: ...

2.  **获取 Bark Key**: ...

3.  **设置 Secrets**:
    * 在您 Fork 后的仓库中，点击 `Settings` (设置) > `Secrets and variables` > `Actions`。
    * 点击 `New repository secret` 创建以下 Secret：

    **必须的 Secrets:**
    * **`DP_EMAIL`**: 您的 DigitalPlat 登录邮箱。
    * **`DP_PASSWORD`**: 您的 DigitalPlat 登录密码。
    * **`BARK_KEY`**: 您在第2步中获取的 Bark Key。

    **可选的 Secrets:**
    * **`CAPTCHA_API_KEY`**: 您的 [2captcha](https://2captcha.com) API Key。DigitalPlat 登录页有 Cloudflare 人机验证，配置后脚本会自动通过 2captcha 解决该验证（每次约 $0.00145）。
    * **`BARK_SERVER`**: 您自建的 Bark 服务器地址，例如 `https://your.bark.server.com`。**如果您使用的是官方公共服务，请不要创建此 Secret。**

4.  **启用并运行 GitHub Actions**:
    * 进入仓库的 `Actions` 标签页。
    * 在左侧找到 `Renew DigitalPlat Free Domains` 工作流。
    * 该工作流会根据计划（默认每15天）自动运行。
    * 如果您想立即测试，可以点击 `Run workflow` 按钮手动触发一次。运行结束后，您的手机应会收到一条推送通知。

## ⚠️ 注意事项

* **安全性**: 您的账号密码存储在 GitHub 的加密 Secrets 中，脚本通过环境变量读取，不会暴露在代码或日志里，非常安全。
* **续期限制**: DigitalPlat 规定域名距到期 **超过 120 天**时不允许续期。脚本会跳过这类域名并正常结束。
* **脚本健壮性**: 本脚本依赖 DigitalPlat 的 API 结构。如果未来网站大幅改版，可能会导致脚本失效。届时需要根据新的接口更新脚本。
