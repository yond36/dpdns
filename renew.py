# renew.py
# 最后更新时间: 2025-07-17
# 这是一个集成了所有功能的完整版本脚本

import os
import sys
import time
import asyncio
import requests
import random
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- 1. 从环境变量中读取配置 ---
# DigitalPlat 账号信息
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")

# Bark 通知配置 (支持官方及自建服务器)
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER")  # 可选, 您的自建 Bark 服务器地址

# 2captcha 配置 (可选, 用于自动解决 Cloudflare 人机验证)
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")

# --- 2. 网站固定 URL ---
# DigitalPlat 面板已迁移到 dashboard.digitalplat.org
PANEL_BASE = "https://dashboard.digitalplat.org"
LOGIN_URL = f"{PANEL_BASE}/auth/login"
DOMAINS_API_URL = f"{PANEL_BASE}/_panel_api/api/domains"

# 登录表单自带的 Turnstile 组件 sitekey
FORM_TURNSTILE_SITEKEY = "0x4AAAAAAAxuMrGCYFcOwd1N"

# 2captcha API 地址
TWOCAPTCHA_API_URL = "https://api.2captcha.com"

# 拦截 Cloudflare 托管挑战页的 turnstile.render 调用以提取参数
# 参考: https://2captcha.com/api-docs/cloudflare-turnstile#cloudflare-challenge-page
TURNSTILE_INTERCEPT_SCRIPT = """
const __tsHook = setInterval(() => {
    if (window.turnstile) {
        clearInterval(__tsHook);
        window.turnstile.render = function(a, b) {
            const isChallenge = typeof window._cf_chl_opt !== 'undefined';
            if (isChallenge) {
                window.__cfParams = {
                    sitekey: b && b.sitekey,
                    data: b && b.cData,
                    pagedata: b && b.chlPageData,
                    action: b && b.action,
                    userAgent: navigator.userAgent
                };
                window.tsCallback = b && b.callback;
                return 'foo';
            }
            return (window.__origTurnstileRender || function(){ return ''; }).apply(this, arguments);
        };
    }
}, 10);
"""

# --- 3. 超时配置 ---
TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 30000,
    "navigation": 60000,
    "login_wait": 180000
}

def validate_config():
    """验证必需的环境变量是否已设置"""
    required_vars = {
        "DP_EMAIL": DP_EMAIL,
        "DP_PASSWORD": DP_PASSWORD
    }

    missing = [var for var, value in required_vars.items() if not value]
    if missing:
        error_msg = f"错误：缺少必需的环境变量: {', '.join(missing)}。请在 GitHub Secrets 中配置。"
        logger.error(error_msg)
        send_bark_notification("DigitalPlat 脚本配置错误", error_msg, level="timeSensitive")
        sys.exit(1)

def send_bark_notification(title, body, level="active", badge=None):
    """
    发送 Bark 推送通知。
    支持自建服务器地址。

    Args:
        title: 通知标题
        body: 通知内容
        level: 通知级别 (active, timeSensitive, passive)
        badge: 应用图标上显示的数字
    """
    if not BARK_KEY:
        logger.info("BARK_KEY 未设置，跳过发送通知。")
        return

    # 如果用户设置了 BARK_SERVER，则使用该地址，否则使用官方公共地址
    server_url = BARK_SERVER if BARK_SERVER else "https://api.day.app"

    # 使用 rstrip('/') 清理末尾可能存在的斜杠，让地址拼接更健壮
    api_url = f"{server_url.rstrip('/')}/{BARK_KEY}"

    logger.info(f"正在向 Bark 服务器 {server_url} 发送通知: {title}")

    try:
        payload = {
            "title": title,
            "body": body,
            "group": "DigitalPlat Renew",
            "level": level
        }
        if badge is not None:
            payload["badge"] = badge

        response = requests.post(api_url, json=payload, timeout=10)
        response.raise_for_status()  # 如果请求失败 (例如 4xx, 5xx 错误) 则抛出异常
        logger.info("Bark 通知已成功发送。")
    except requests.exceptions.RequestException as e:
        logger.error(f"发送 Bark 通知时发生网络错误: {e}")
    except Exception as e:
        logger.error(f"发送 Bark 通知时发生未知错误: {e}")

def save_results(renewed_domains, failed_domains):
    """保存处理结果到JSON文件"""
    results = {
        "timestamp": datetime.now().isoformat(),
        "renewed_count": len(renewed_domains),
        "failed_count": len(failed_domains),
        "renewed_domains": renewed_domains,
        "failed_domains": failed_domains
    }

    try:
        with open("renewal_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("处理结果已保存到 renewal_results.json")
    except Exception as e:
        logger.error(f"保存结果时发生错误: {e}")

async def retry_operation(operation, max_retries=3, delay=2):
    """
    重试操作的通用函数

    Args:
        operation: 要执行的异步操作
        max_retries: 最大重试次数
        delay: 重试之间的延迟（秒）
    """
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"操作失败，{delay}秒后重试... (尝试 {attempt + 1}/{max_retries})")
            await asyncio.sleep(delay)

def create_turnstile_task(params):
    """提交 Cloudflare Turnstile 任务到 2captcha (支持挑战页与独立组件)"""
    task = {
        "type": "TurnstileTaskProxyless",
        "websiteURL": LOGIN_URL,
        "websiteKey": params["sitekey"],
    }
    if params.get("action"):
        task["action"] = params["action"]
    if params.get("data"):
        task["data"] = params["data"]
    if params.get("pagedata"):
        task["pagedata"] = params["pagedata"]
    if params.get("userAgent"):
        task["userAgent"] = params["userAgent"]

    payload = {
        "clientKey": CAPTCHA_API_KEY,
        "task": task
    }
    response = requests.post(f"{TWOCAPTCHA_API_URL}/createTask", json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get("errorId") != 0:
        raise Exception(f"2captcha createTask 失败: {result.get('errorDescription')}")
    return result["taskId"]

def get_turnstile_result(task_id, timeout=180):
    """轮询 2captcha 获取 Turnstile 求解结果"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = requests.post(
            f"{TWOCAPTCHA_API_URL}/getTaskResult",
            json={"clientKey": CAPTCHA_API_KEY, "taskId": task_id},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errorId") != 0:
            raise Exception(f"2captcha getTaskResult 失败: {result.get('errorDescription')}")
        if result.get("status") == "ready":
            solution = result["solution"]
            return solution.get("token")
        logger.info("等待 2captcha 求解中... (5 秒后重试)")
        time.sleep(5)
    raise Exception("2captcha 求解超时")

async def wait_for_cf_params(page, timeout=30000):
    """等待挑战页渲染 turnstile 并捕获其参数"""
    try:
        await page.wait_for_function("window.__cfParams !== undefined", timeout=timeout)
        return await page.evaluate("window.__cfParams")
    except PlaywrightTimeoutError:
        return None

async def solve_turnstile(params):
    """提交 Turnstile 任务到 2captcha 并等待求解结果"""
    task_id = await asyncio.to_thread(create_turnstile_task, params)
    return await asyncio.to_thread(get_turnstile_result, task_id)

async def solve_cloudflare_challenge(page):
    """
    通过 2captcha 解决 Cloudflare 托管挑战页
    参考: https://2captcha.com/api-docs/cloudflare-turnstile#cloudflare-challenge-page
    """
    logger.info("正在注入 turnstile 拦截脚本并重新加载挑战页...")
    await page.add_init_script(TURNSTILE_INTERCEPT_SCRIPT)

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])

            params = await wait_for_cf_params(page)
            if not params or not params.get("sitekey"):
                raise Exception("无法捕获 Cloudflare turnstile 参数")

            logger.info(f"已捕获 turnstile 参数: sitekey={params['sitekey']}, action={params.get('action')}")
            logger.info("正在提交任务到 2captcha...")
            task_id = await asyncio.to_thread(create_turnstile_task, params)
            token = await asyncio.to_thread(get_turnstile_result, task_id)
            logger.info("2captcha 已返回 token，正在执行回调...")

            await page.wait_for_function("window.tsCallback !== undefined", timeout=TIMEOUTS["element_wait"])
            await page.evaluate("(tok) => { window.tsCallback(tok); }", token)

            logger.info("正在等待挑战自动跳转到登录表单...")
            try:
                await page.wait_for_selector("input[name='email']", timeout=60000)
                logger.info("已通过人机验证，进入登录表单。")
                return
            except PlaywrightTimeoutError:
                raise Exception("token 提交后未出现登录表单，可能被 Cloudflare 重新验证")
        except Exception as e:
            if attempt == max_attempts - 1:
                await page.screenshot(path="captcha_solve_failed.png")
                title = await page.title()
                url = page.url
                body = ""
                try:
                    body = (await page.inner_text("body"))[:300]
                except Exception:
                    pass
                logger.error(f"验证未通过. 当前 title={title!r} url={url!r}")
                logger.error(f"页面正文前300字符: {body!r}")
                with open("captcha_solve_failed_source.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                raise
            logger.warning(f"2captcha 挑战解决失败 (尝试 {attempt + 1}/{max_attempts}): {e}，3 秒后重试...")
            await asyncio.sleep(3)

    raise Exception("多次尝试后仍无法通过 Cloudflare 验证")

async def simulate_human_behavior(page):
    """模拟人类行为"""
    # 随机鼠标移动
    await page.mouse.move(
        random.randint(100, 500),
        random.randint(100, 500)
    )
    # 随机延迟
    await asyncio.sleep(random.uniform(0.5, 2))

async def setup_browser_context(playwright):
    """设置浏览器上下文"""
    # 使用真实 Chrome (channel="chrome") 以通过 Cloudflare 人机验证
    # 捆绑版 headless Chromium 指纹会被 Cloudflare 拦截并反复触发验证
    browser = await playwright.chromium.launch(
        headless=True,
        channel="chrome",
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-gpu',
            '--window-size=1920,1080',
        ]
    )

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080}
    )

    return browser, context

async def add_anti_detection_scripts(page):
    """添加反检测脚本"""
    scripts = [
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
        "window.navigator.chrome = { runtime: {} };",
        "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});",
        "Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});"
    ]

    for script in scripts:
        await page.add_init_script(script)

async def login(page):
    """执行登录流程 (直接调用面板 API, 绕过表单 UI 提交)"""
    logger.info("正在导航到登录页面...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])

    # 模拟人类行为
    await simulate_human_behavior(page)

    if not CAPTCHA_API_KEY:
        raise Exception("未配置 CAPTCHA_API_KEY，无法通过 DigitalPlat 的 Cloudflare 人机验证")

    # 等待人机验证自动跳转
    logger.info("等待人机验证页自动跳转到登录表单...")
    try:
        await page.wait_for_selector("input[name='email']", timeout=15000)
        logger.info("未触发人机验证，直接进入登录表单。")
    except PlaywrightTimeoutError:
        logger.info("检测到 Cloudflare 人机验证，使用 2captcha 自动解决...")
        await solve_cloudflare_challenge(page)

    # 确保登录表单已加载
    await page.wait_for_selector("input[name='email']", timeout=TIMEOUTS["login_wait"])
    logger.info("已进入登录表单页面。")

    # 解决登录表单自带的 Turnstile 组件 (独立组件, 仅需 sitekey)
    logger.info("正在解决登录表单的 Turnstile 组件...")
    token = await solve_turnstile({"sitekey": FORM_TURNSTILE_SITEKEY})

    # 直接调用登录 API (携带 CSRF token)
    logger.info("正在调用登录 API...")
    result = await page.evaluate("""async ({ email, password, ftoken }) => {
        const csrf = document.cookie.match(/panel_csrf_token=([^;]+)/);
        const res = await fetch('/_panel_api/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-csrf-token': csrf ? csrf[1] : ''
            },
            body: JSON.stringify({ email: email, password: password, turnstile_token: ftoken })
        });
        const body = await res.text();
        return { status: res.status, body: body };
    }""", {"email": DP_EMAIL, "password": DP_PASSWORD, "ftoken": token})

    if result["status"] != 200:
        logger.error(f"登录 API 失败: status={result['status']} body={result['body'][:300]}")
        await page.screenshot(path="login_failed_error.png")
        send_bark_notification(
            "DigitalPlat 登录失败",
            f"登录 API 返回状态码 {result['status']}",
            level="timeSensitive"
        )
        raise Exception(f"登录失败: API 返回 {result['status']}")

    logger.info("登录成功！已进入用户仪表盘。")

async def get_domains(page):
    """通过面板 API 获取域名列表"""
    result = await page.evaluate("""async () => {
        const res = await fetch('/_panel_api/api/domains');
        return await res.text();
    }""")
    try:
        data = json.loads(result)
    except Exception:
        logger.error(f"解析域名列表失败: {result[:300]}")
        raise
    return data.get("domains", [])

async def renew_domain(page, domain_name):
    """通过面板 API 续期免费域名 (free 1年)"""
    result = await page.evaluate("""async ({ name }) => {
        const csrf = document.cookie.match(/panel_csrf_token=([^;]+)/);
        const res = await fetch('/_panel_api/api/domains/' + encodeURIComponent(name) + '/renew', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-csrf-token': csrf ? csrf[1] : '',
                'Idempotency-Key': crypto.randomUUID()
            },
            body: JSON.stringify({ renewal_type: 'free', years: 1 })
        });
        const body = await res.text();
        return { status: res.status, body: body.slice(0, 500) };
    }""", {"name": domain_name})
    return result

async def process_domains(page):
    """获取域名列表并通过 API 完成续期"""
    logger.info("正在获取域名列表...")
    domains = await get_domains(page)
    if not domains:
        logger.info("未找到任何域名。")
        return [], []

    logger.info(f"共找到 {len(domains)} 个域名，开始逐一检查...")
    renewed_domains = []
    failed_domains = []

    for i, d in enumerate(domains):
        name = d.get("name") or d.get("domain")
        expiry = d.get("expiry_date_display") or d.get("expiry_date")
        can_renew = d.get("can_manual_renew", False)
        status = d.get("status")
        logger.info(f"\n[{i+1}/{len(domains)}] 检查域名: {name} (到期: {expiry}, 状态: {status}, 可续期: {can_renew})")

        if not name:
            continue

        if not can_renew:
            logger.info(f"{name} 当前不可手动续期，跳过。")
            continue

        logger.info(f"正在为 {name} 提交续期请求...")
        result = await renew_domain(page, name)
        if result["status"] == 200:
            logger.info(f"成功！域名 {name} 续期订单已提交。")
            renewed_domains.append(name)
        else:
            msg = result["body"]
            # 域名距到期超过120天才可续期, 属正常情况而非失败
            if "more than 120 days" in msg or "cannot renew" in msg.lower():
                logger.info(f"{name} 距到期超过120天，暂时无需续期，跳过。")
            else:
                error_msg = f"{name} (续期失败: {msg[:120]})"
                logger.warning(error_msg)
                failed_domains.append(error_msg)

    return renewed_domains, failed_domains

async def run_renewal():
    """主执行函数，运行完整的登录和续期流程。"""
    # 验证配置
    validate_config()

    # 初始化变量
    browser = None
    page = None
    renewed_domains = []
    failed_domains = []

    async with async_playwright() as p:
        try:
            # 步骤 1: 启动浏览器
            logger.info("正在启动浏览器...")
            browser, context = await setup_browser_context(p)
            page = await context.new_page()

            # 添加反检测措施
            await add_anti_detection_scripts(page)

            # 步骤 2: 登录
            await login(page)

            # 步骤 3: 获取域名列表并续期
            renewed_domains, failed_domains = await process_domains(page)

            # 步骤 4: 发送最终执行结果通知
            logger.info("\n--- 所有域名检查完成 ---")
            if not renewed_domains and not failed_domains:
                title = "DigitalPlat 续期检查完成"
                body = "所有域名均检查完毕，本次没有需要续期或处理失败的域名。"
            else:
                title = f"DigitalPlat 续期报告"
                body = ""
                if renewed_domains:
                    body += f"✅ 成功续期 {len(renewed_domains)} 个域名:\n" + "\n".join(renewed_domains) + "\n\n"
                if failed_domains:
                    body += f"❌ 处理失败 {len(failed_domains)} 个域名:\n" + "\n".join(failed_domains)
            send_bark_notification(title, body.strip())

            # 保存结果
            save_results(renewed_domains, failed_domains)

        except Exception as e:
            # 步骤 5: 统一错误处理
            error_message = f"脚本执行时发生严重错误: {type(e).__name__} - {e}"
            logger.error(f"错误: {error_message}")
            if 'page' in locals():
                await page.screenshot(path="fatal_error_screenshot.png")
                logger.info("已保存截图 'fatal_error_screenshot.png' 以供调试。")
            send_bark_notification("DigitalPlat 脚本严重错误", f"{error_message}\n请检查 GitHub Actions 日志获取详情。")
            sys.exit(1)  # 以错误码退出，让 Actions 知道任务失败了
        finally:
            # 步骤 6: 确保浏览器被关闭
            if 'browser' in locals() and browser.is_connected():
                logger.info("关闭浏览器...")
                await browser.close()

if __name__ == "__main__":
    asyncio.run(run_renewal())
