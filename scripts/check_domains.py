#!/usr/bin/env python3
"""DigitalPlat DNS domain expiry checker with Telegram/Bark notifications.

Fetches the domain list via the DigitalPlat Domain API, identifies free
domains whose remaining validity is inside the renewal window, and sends a
summary via Telegram and/or Bark. DigitalPlat's public API does not expose a
renewal endpoint, so renewal must be done manually in the Dashboard.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = os.getenv("DIGITALPLAT_API_BASE", "https://domain-api.digitalplat.org/api/v1").rstrip("/")
DEFAULT_THRESHOLD = 120
DATE_FORMAT = "%Y-%m-%d"
DASHBOARD_URL = "https://dash.domain.digitalplat.org/dashboard"
# Cloudflare's bot detection blocks custom binary-looking User-Agents, so default
# to a realistic browser agent to keep the scheduled automation from being challenged.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _request(path, method="GET", payload=None, token=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": os.getenv("DIGITALPLAT_USER_AGENT", DEFAULT_UA),
    }
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{API_BASE}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc}") from exc
    if not text:
        return {}
    return json.loads(text)


def _unwrap(data):
    if not isinstance(data, dict):
        return data
    if data.get("success") is False:
        raise RuntimeError(data.get("error") or data.get("message") or str(data))
    if "data" in data:
        return data["data"]
    return data


def _parse_date(value):
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
    if len(text) == 10:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pick(record, keys):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _extract_domains(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        domains = payload.get("domains")
        if isinstance(domains, list):
            return [item for item in domains if isinstance(item, dict)]
        domain = payload.get("domain")
        if isinstance(domain, dict):
            return [domain]
    return []


def list_domains(token):
    return _extract_domains(_unwrap(_request("/domains", token=token)))


def _is_free_domain(raw):
    slot = _pick(raw, ("slot_type",))
    if slot is not None and str(slot).strip().lower() not in ("free", ""):
        return False
    renewable = _pick(raw, ("can_free_renew", "can_renew", "renewable"))
    if isinstance(renewable, str):
        renewable = renewable.strip().lower() in ("1", "true", "yes", "y")
    elif renewable is not None:
        renewable = bool(renewable)
    if renewable is False:
        return False
    return True


def _expiry(raw):
    raw_val = _pick(raw, ("expiry_date", "expires_at", "expiryDate", "expiresAt", "expiration_date"))
    if not raw_val:
        return None
    text = str(raw_val).strip().lower()
    if text in ("permanent", "null", "none", "0"):
        return None
    try:
        return _parse_date(raw_val)
    except ValueError:
        return None


def _send_telegram(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _send_bark(bark_key, text, server):
    url = f"{server.rstrip('/')}/{bark_key}"
    payload = {"title": "DigitalPlat 域名到期检查", "body": text, "group": "DigitalPlat Renew"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def build_message(now, threshold, results):
    lines = ["DigitalPlat 域名到期检查", f"时间: {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]

    needing = [r for r in results if r["needs_renewal"]]
    if needing:
        lines.append(f"⚠️ 以下 {len(needing)} 个域名将在 {threshold} 天内到期，需续期:")
        for r in needing:
            lines.append(f"- {r['domain']} (到期 {r['expiry'].strftime(DATE_FORMAT)}, 剩余 {r['days_left']} 天)")
    else:
        lines.append("✅ 没有域名需要续期")

    lines.append("")
    lines.append(f"检查域名: {len(results)} 个")
    lines.append("DigitalPlat API 未开放续期接口，续期请前往 Dashboard 手动操作")
    lines.append(DASHBOARD_URL)
    return "\n".join(lines)


def main():
    token = os.getenv("DIGITALPLAT_API_TOKEN")
    if not token:
        print("[ERROR] Missing DIGITALPLAT_API_TOKEN", file=sys.stderr)
        return 1

    threshold = int(os.getenv("DIGITALPLAT_RENEW_BEFORE_DAYS", str(DEFAULT_THRESHOLD)))
    explicit_raw = os.getenv("DIGITALPLAT_DOMAINS", "")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    bark_key = os.getenv("BARK_KEY", "")
    bark_server = os.getenv("BARK_SERVER", "https://api.day.app")

    try:
        domains = list_domains(token)
    except Exception as exc:
        print(f"[ERROR] Failed to list domains: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    print(f"UTC now: {now.isoformat(timespec='seconds')}")
    print(f"Renewal window: notify within {threshold} day(s) before expiry")

    if explicit_raw.strip():
        targets = [d.strip().lower() for d in explicit_raw.replace(",", "\n").splitlines() if d.strip()]
        if not targets:
            print("[ERROR] DIGITALPLAT_DOMAINS is set but contains no domains", file=sys.stderr)
            return 1
        domain_map = {}
        for raw in domains:
            name = _pick(raw, ("domain", "name", "full_domain"))
            if name:
                domain_map[str(name).strip().lower()] = raw
        candidates = [(domain, domain_map.get(domain)) for domain in targets]
        print(f"MODE: check only DIGITALPLAT_DOMAINS ({len(candidates)} target(s))")
    else:
        candidates = [
            (str(_pick(raw, ("domain", "name", "full_domain")) or "").strip().lower(), raw)
            for raw in domains
            if _is_free_domain(raw)
        ]
        candidates = [(domain, raw) for domain, raw in candidates if domain]
        print(f"MODE: check all free domains ({len(candidates)} eligible)")

    results = []
    for domain, raw in candidates:
        if raw is None:
            print(f"[ERROR] {domain}: not found in DigitalPlat account", file=sys.stderr)
            continue

        expiry = _expiry(raw)
        status = str(_pick(raw, ("status",)) or "-")
        slot = str(_pick(raw, ("slot_type",)) or "-")
        if expiry is None:
            print(f"[CHECK] {domain} expires=permanent/unknown status={status} slot={slot} renewal=no")
            results.append({"domain": domain, "expiry": None, "days_left": None, "needs_renewal": False})
            continue

        days_left = (expiry.date() - now.date()).days
        needs = days_left <= threshold
        print(
            f"[CHECK] {domain} expires={expiry.strftime(DATE_FORMAT)} "
            f"days_left={days_left} status={status} slot={slot} renewal={'yes' if needs else 'no'}"
        )
        results.append({"domain": domain, "expiry": expiry, "days_left": days_left, "needs_renewal": needs})

    needing = [r for r in results if r["needs_renewal"]]
    print(f"[SUMMARY] checked={len(results)} needing_renewal={len(needing)}")
    if not needing:
        print("[DONE] No domains need renewal")

    message = build_message(now, threshold, results)

    if telegram_token and telegram_chat_id:
        try:
            _send_telegram(telegram_token, telegram_chat_id, message)
            print("[NOTIFY] Telegram sent")
        except Exception as exc:
            print(f"[ERROR] Telegram notification failed: {exc}", file=sys.stderr)
    else:
        print("[NOTIFY] Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    if bark_key:
        try:
            _send_bark(bark_key, message, bark_server)
            print("[NOTIFY] Bark sent")
        except Exception as exc:
            print(f"[ERROR] Bark notification failed: {exc}", file=sys.stderr)
    else:
        print("[NOTIFY] Bark not configured (set BARK_KEY)")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(1)