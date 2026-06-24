#!/usr/bin/env python3
"""
Blox Fruits Stock Notifier - CURY Stable Healthcheck Edition
Your custom emojis + original parsing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ==================== CONSTANTS ====================
API_URL = "https://www.bloxfruitvalues.net/api/stocks"
STOCK_URL = "https://fruityblox.com/stock"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HTTP_TIMEOUT = 20

FRUIT_EMOJI = {
    "buddha": "<:buddha:1519382662144331879>",
    "control": "<:control:1519382870974791821>",
    "creation": "<:creation:1519382083552940185>",
    "dough": "<:dough:1519382250406281338>",
    "dragon": "<:dragon_fruit_east:1519382172060876850>",
    "gravity": "<:gravity:1519382019413512242>",
    "kitsune": "<:kitsune:1519382598432854047>",
    "leopard": "<:leopard:1519382733975977985>",
    "portal": "<:portal:1519382939580760326>",
    "yeti": "<:yeti:1519382793971437779>",
    "rocket": "🚀",
    "spin": "🌪️",
    "blade": "⚔️",
    "spring": "🌀",
    "bomb": "💣",
    "smoke": "💨",
    "flame": "🔥",
    "ice": "❄️",
    "light": "⚡",
    "dark": "🌑",
    "magma": "🌋",
    "quake": "🌋",
    "love": "❤️",
    "spider": "🕷️",
    "sound": "🔊",
    "phoenix": "🔥",
    "lightning": "🌩️",
    "mammoth": "🐘",
    "t-rex": "🦖",
    "tiger": "🐅",
    "venom": "☠️",
    "gas": "☁️",
    "spirit": "👻",
    "shadow": "🌑",
}

DEFAULT_EMOJI = "🍎"
EMBED_COLOR = 0x9B59B6

def fruit_emoji(name: str) -> str:
    n = norm_name(name)
    return FRUIT_EMOJI.get(n, DEFAULT_EMOJI)

@dataclass
class Stock:
    normal: list[dict] = field(default_factory=list)
    mirage: list[dict] = field(default_factory=list)
    next_reset: str | None = None
    source: str | None = None

def norm_name(name: str) -> str:
    n = str(name).strip().lower()
    for junk in (" fruit", "-", "_"):
        n = n.replace(junk, " ")
    return " ".join(n.split())

def fmt_price(price) -> str:
    try:
        p = int(price)
    except (TypeError, ValueError):
        return ""
    if p >= 1_000_000:
        s = f"{p / 1_000_000:.1f}M".replace(".0M", "M")
    elif p >= 1_000:
        s = f"{p / 1_000:.1f}K".replace(".0K", "K")
    else:
        s = str(p)
    return s

# ==================== ORIGINAL PARSING ====================
_ARR_RE_TEMPLATE = r'\\?"%s\\?"\s*:\s*(\[.*?\])'
_OBJ_RE = re.compile(r'\\?"name\\?"\s*:\s*\\?"([^"\\]+)\\?"(?:\s*,\s*\\?"price\\?"\s*:\s*(\d+))?')

def _extract_array(html: str, key: str) -> list[dict]:
    m = re.search(_ARR_RE_TEMPLATE % key, html)
    if not m:
        return []
    start = m.start(1)
    depth = 0
    end = start
    for i in range(start, len(html)):
        c = html[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    span = html[start:end]
    out: list[dict] = []
    for name, price in _OBJ_RE.findall(span):
        out.append({"name": name, "price": int(price) if price else None})
    return out

def parse_stock(html: str, source: str) -> Stock:
    normal = _extract_array(html, "normal")
    mirage = _extract_array(html, "mirage")
    return Stock(normal=normal, mirage=mirage, next_reset=None, source=source)

def _http_get(url: str, accept: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[warn] fetch {url}: {e}", file=sys.stderr)
        return None

def _items_from_objs(arr: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(arr, list):
        return out
    for it in arr:
        if isinstance(it, str):
            out.append({"name": it, "price": None})
        elif isinstance(it, dict):
            name = next((it[k] for k in ("name", "fruit", "Name", "title") if isinstance(it.get(k), str)), None)
            if not name:
                continue
            price = next((it[k] for k in ("price", "value", "Price") if isinstance(it.get(k), (int, float))), None)
            out.append({"name": name, "price": price})
    return out

def fetch_from_api() -> Stock | None:
    raw = _http_get(API_URL, "application/json")
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        normal = _items_from_objs(data.get("normal"))
        mirage = _items_from_objs(data.get("mirage"))
        nxt = data.get("nextUpdate") or {}
        next_reset = nxt.get("normal") if isinstance(nxt, dict) else None
        return Stock(normal=normal, mirage=mirage, next_reset=next_reset, source=API_URL)
    except Exception:
        return None

def fetch_from_fruityblox() -> Stock | None:
    html = _http_get(STOCK_URL, "text/html")
    return parse_stock(html, STOCK_URL) if html else None

def fetch_stock() -> Stock | None:
    return fetch_from_api() or fetch_from_fruityblox()

# ==================== RICH EMBED + NOTIFICATIONS + STATE (as before) ====================
# (Copy the rich embed, notify_discord, load_state, save_state, should_notify, main() from the previous stable version I gave you)

# For brevity, paste the remaining code from the full stable version earlier in our conversation.

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
