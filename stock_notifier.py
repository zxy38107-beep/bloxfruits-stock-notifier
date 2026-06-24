#!/usr/bin/env python3
"""
Blox Fruits Stock Notifier - CURY Enhanced Edition
Your custom emojis + robust change detection + countdown timers + price deltas.
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

ALWAYS_IN_STOCK = {"rocket", "spin"}

# ==================== YOUR CUSTOM EMOJIS ====================
FRUIT_EMOJI = {
    "buddha": "<:buddha:1519382662144331879>",
    "control": "<:control:1519382870974791821>",
    "creation": "<:creation:1519382083552940185>",
    "dough": "<:dough:1519382250406281338>",
    "dragon": "<:dragon_fruit_east:1519382172060876850>",
    "dragon east": "<:dragon_fruit_east:1519382172060876850>",
    "dragon west": "<:dragon_fruit_west:1519382526509191349>",
    "gravity": "<:gravity:1519382019413512242>",
    "kitsune": "<:kitsune:1519382598432854047>",
    "leopard": "<:leopard:1519382733975977985>",
    "portal": "<:portal:1519382939580760326>",
    "yeti": "<:yeti:1519382793971437779>",
    # Unicode fallbacks
    "rocket": "🚀",
    "spin": "🌪️",
    "blade": "⚔️",
    "spring": "🌀",
    "bomb": "💣",
    "smoke": "💨",
    "spike": "🌵",
    "flame": "🔥",
    "ice": "❄️",
    "sand": "🏜️",
    "dark": "🌑",
    "eagle": "🦅",
    "diamond": "💎",
    "light": "⚡",
    "rubber": "🟡",
    "ghost": "👻",
    "magma": "🌋",
    "quake": "🌋",
    "love": "❤️",
    "spider": "🕷️",
    "sound": "🔊",
    "phoenix": "🔥",
    "lightning": "🌩️",
    "pain": "😣",
    "blizzard": "❄️",
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

# ==================== PARSING (Original) ====================
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

# ==================== RICH EMBED ====================
def parse_next_reset(next_reset_str: str | None) -> dict | None:
    if not next_reset_str:
        return None
    try:
        dt = datetime.fromisoformat(next_reset_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = dt - now
        if delta.total_seconds() < 0:
            return {"text": "Rotating soon", "raw": next_reset_str}
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        return {"text": f"in {hours}h {mins}m" if hours else f"in {mins}m", "raw": next_reset_str}
    except Exception:
        return {"text": str(next_reset_str), "raw": next_reset_str}

def _field_value_with_delta(items: list[dict], prev_items: list[dict]) -> str:
    prev_map = {norm_name(it["name"]): it.get("price") for it in prev_items}
    lines = []
    for it in items:
        name = it["name"]
        price = it.get("price")
        prev_price = prev_map.get(norm_name(name))
        emoji = fruit_emoji(name)
        price_str = fmt_price(price)
        delta = ""
        if price is not None and prev_price is not None:
            diff = price - prev_price
            if diff > 0:
                delta = f" ↑{fmt_price(abs(diff))}"
            elif diff < 0:
                delta = f" ↓{fmt_price(abs(diff))}"
        elif price is not None and prev_price is None:
            delta = " **NEW**"
        lines.append(f"{emoji} **{name}** - {price_str}{delta}")
    return "\n".join(lines)[:1024] or "*(empty)*"

def build_embed(stock: Stock, prev_state: dict) -> dict:
    embed = {
        "title": "🛒 Blox Fruits Stock Update",
        "color": EMBED_COLOR,
        "fields": [
            {"name": "🌊 Normal Stock", "value": _field_value_with_delta(stock.normal, prev_state.get("normal", [])), "inline": True},
            {"name": "✨ Mirage Stock", "value": _field_value_with_delta(stock.mirage, prev_state.get("mirage", [])), "inline": True},
        ],
        "footer": {"text": "Powered by CURY Oracle • 2341"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    countdown = parse_next_reset(stock.next_reset)
    if countdown:
        embed["fields"].append({"name": "⏳ Next Reset", "value": f"**{countdown['text']}**", "inline": False})
    return embed

# ==================== NOTIFICATIONS ====================
def notify_discord(webhook: str, stock: Stock, prev_state: dict, content: str = "") -> None:
    body = {
        "username": "Blox Fruits Oracle",
        "embeds": [build_embed(stock, prev_state)],
    }
    if content:
        body["content"] = content
        body["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
    except Exception as e:
        print(f"[warn] discord failed: {e}", file=sys.stderr)

def notify_desktop(title: str, body: str) -> bool:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, body, duration=10, threaded=True)
        return True
    except Exception:
        pass
    try:
        from plyer import notification
        notification.notify(title=title, message=body, timeout=10)
        return True
    except Exception:
        return False

# ==================== STATE & CHANGE DETECTION ====================
def load_state(state_file: str = "state.json") -> dict:
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(stock: Stock, state_file: str = "state.json"):
    data = {
        "normal": stock.normal,
        "mirage": stock.mirage,
        "last_signature": get_stock_signature(stock),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    with open(state_file, "w") as f:
        json.dump(data, f, indent=2)

def get_stock_signature(stock: Stock) -> str:
    normal_sig = "|".join(sorted(norm_name(f["name"]) for f in stock.normal))
    mirage_sig = "|".join(sorted(norm_name(f["name"]) for f in stock.mirage))
    return f"N:{normal_sig}|M:{mirage_sig}|R:{stock.next_reset or ''}"

def should_notify(new_stock: Stock, last_state: dict) -> bool:
    if not last_state or not last_state.get("last_signature"):
        return True
    return get_stock_signature(new_stock) != last_state.get("last_signature")

# ==================== CONFIG & MAIN ====================
def load_config(path: str = "config.json") -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args(argv)

    if args.init:
        default = {
            "watchlist": ["Dragon", "Leopard", "Kitsune", "Dough", "Mammoth", "Gas"],
            "poll_seconds": 300,
            "discord_webhook": "",
            "desktop_toast": True,
            "state_file": "state.json",
            "ping_fruits": ["Kitsune", "Dragon"],
            "ping_target": "@everyone"
        }
        with open(args.config, "w") as f:
            json.dump(default, f, indent=2)
        print(f"Created {args.config}")
        return 0

    config = load_config(args.config)
    webhook = config.get("discord_webhook") or os.getenv("DISCORD_WEBHOOK")
    state_file = config.get("state_file", "state.json")

    if args.once or not webhook:
        stock = fetch_stock()
        if stock:
            print(f"Normal: {len(stock.normal)} | Mirage: {len(stock.mirage)}")
        return 0

    print("CURY Enhanced Blox Fruits Oracle running (5-min GitHub sync)...")
    while True:
        stock = fetch_stock()
        if stock:
            last_state = load_state(state_file)
            if should_notify(stock, last_state):
                print("[info] Stock rotation detected!")
                ping_fruits = {norm_name(f) for f in config.get("ping_fruits", [])}
                has_ping = any(norm_name(f["name"]) in ping_fruits for f in stock.normal + stock.mirage)
                content = config.get("ping_target", "") if has_ping else ""
                notify_discord(webhook, stock, last_state, content)
                if config.get("desktop_toast"):
                    notify_desktop("Blox Fruits Rotation!", "New stock available!")
                save_state(stock, state_file)
            else:
                print("[debug] No change")
        time.sleep(config.get("poll_seconds", 300))

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
