#!/usr/bin/env python3
"""DigitalPlat DNS domain auto-renewal script.

Checks target domains and renews them via the DigitalPlat Domain API when the
remaining validity drops below a threshold. Designed to be driven by a
scheduled GitHub Actions workflow.
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


def renew_domain(domain, token, renewal_type, years):
    encoded = urllib.parse.quote(domain, safe="")
    payload = {"renewal_type": renewal_type, "years": years}
    data = _unwrap(_request(f"/domains/{encoded}/renew", method="POST", payload=payload, token=token))
    records = _extract_domains(data)
    return records[0] if records else {"domain": domain}


def main():
    token = os.getenv("DIGITALPLAT_API_TOKEN")
    if not token:
        print("[ERROR] Missing DIGITALPLAT_API_TOKEN", file=sys.stderr)
        return 1

    raw_domains = os.getenv("DIGITALPLAT_DOMAINS", "example.dpdns.org").replace(",", "\n")
    targets = [d.strip().lower() for d in raw_domains.splitlines() if d.strip()]
    if not targets:
        print("[ERROR] No target domains configured", file=sys.stderr)
        return 1

    threshold = int(os.getenv("DIGITALPLAT_RENEW_BEFORE_DAYS", str(DEFAULT_THRESHOLD)))
    renewal_type = os.getenv("DIGITALPLAT_RENEWAL_TYPE", "free")
    years = int(os.getenv("DIGITALPLAT_RENEWAL_YEARS", "1"))
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

    try:
        domains = list_domains(token)
    except Exception as exc:
        print(f"[ERROR] Failed to list domains: {exc}", file=sys.stderr)
        return 1

    domain_map = {}
    for raw in domains:
        name = _pick(raw, ("domain", "name", "full_domain"))
        if name:
            domain_map[str(name).strip().lower()] = raw

    now = datetime.now(timezone.utc)
    print(f"UTC now: {now.isoformat(timespec='seconds')}")
    print(f"Renewal window: renew within {threshold} day(s) before expiry")

    changed = False
    errors = []
    for domain in targets:
        raw = domain_map.get(domain)
        if raw is None:
            errors.append(f"{domain}: not found in DigitalPlat account")
            continue

        expiry_raw = _pick(raw, ("expiry_date", "expires_at", "expiryDate", "expiresAt", "expiration_date"))
        if not expiry_raw:
            errors.append(f"{domain}: missing expiry date")
            continue

        expiry = _parse_date(expiry_raw)
        days_left = (expiry.date() - now.date()).days
        status = str(_pick(raw, ("status",)) or "-")
        print(f"[CHECK] {domain} expires={expiry.strftime(DATE_FORMAT)} days_left={days_left} status={status}")

        if days_left > threshold:
            print(f"[SKIP] {domain} not yet within renewal window")
            continue

        if dry_run:
            print(f"[DRY-RUN] would renew {domain} (renewal_type={renewal_type}, years={years})")
            continue

        try:
            updated = renew_domain(domain, token, renewal_type, years)
            new_expiry = _parse_date(_pick(updated, ("expiry_date", "expires_at")))
            print(f"[RENEWED] {domain} new_expires={new_expiry.strftime(DATE_FORMAT)}")
            changed = True
        except Exception as exc:
            errors.append(f"{domain}: renewal failed: {exc}")

    for error in errors:
        print(f"[ERROR] {error}", file=sys.stderr)
    if not changed and not errors:
        print("[DONE] No domains renewed (targets not in renewal window or dry-run)")
    elif changed:
        print("[DONE] Renewal requested")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
    raise SystemExit(1)
