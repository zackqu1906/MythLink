"""Website identities used by recognition and configuration; no key bindings."""
import ipaddress
import re
from urllib.parse import urlsplit

BILIBILI = "bilibili.com"


def website_domain(value):
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        raise ValueError("请输入网站域名，例如 bilibili.com")
    parsed = urlsplit(text if "://" in text else "https://" + text)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("请使用 http 或 https 网站地址")
    host = (parsed.hostname or "").rstrip(".").encode("idna").decode().lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
            raise ValueError("请输入完整的网站域名")
    if host == "www.bilibili.com":
        host = BILIBILI
    return host


def matches_website(host, domain):
    return bool(host and domain and (host == domain or host.endswith("." + domain)))
