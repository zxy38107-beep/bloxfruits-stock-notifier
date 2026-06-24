#!/usr/bin/env python3
"""
Blox Fruits Stock Notifier - CURY Enhanced Real-Time Edition
Full merge with robust Mirage detection + countdown + price deltas.
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

# ==================== ORIGINAL CONSTANTS & HELPERS ====================
API_URL = "https://www.bloxfruitvalues.net/api/stocks"
STOCK_URL = "https://fruityblox.com/stock"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT = 20

ALWAYS_IN_STOCK = {"rocket", "spin"}

FRUIT_EMOJI = {
    # "dragon": "<:dragon:YOUR_ID>",
    # "kitsune": "<:kitsune:YOUR_ID>",
    # Add your custom emojis here
}
DEFAULT_EMOJI = "\U0001F34E"  # 🍎

EMBED_COLOR = 0x9B59B6


def fruit_emoji(name: str) -> str:
    return FRUIT_EMOJI.get(norm_name(name), DEFAULT_EMOJI)


@dataclass
class Stock:
    normal: list[dict] = field(default_factory=list)
    mirage: list[dict] = field(default_factory=list)
    next_reset: str | None = None
    source: str | None = None

    def all_names(self) -> set[str]:
        return {norm_name(f["name"]) for f in (*self.normal, *self.mirage)}


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


# ==================== ORIGINAL PARSING (Unchanged) ====================
_ARR_RE_TEMPLATE = r'\\?"%s\\?"\s*:\s*(\[.*?\])'
_OBJ_RE = re.compile(
    r'\\?"name\\?"\s*:\s*\\?"([^"\\]+)\\?"'
    r'(?:\s*,\s*\\?"price\\?"\s*:\s*(\d+))?'
)


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
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[warn] fetch failed for {url}: {e}", file=sys.stderr)
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
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[warn] API returned non-JSON: {e}", file=sys.stderr)
        return None

    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    normal = _items_from_objs(data.get("normal"))
    mirage = _items_from_objs(data.get("mirage"))
    if not normal and not mirage:
        err = data.get("error") or "empty stock arrays"
        print(f"[info] API has no stock data ({err}); falling back", file=sys.stderr)
        return None

    nxt = data.get("nextUpdate") or {}
    next_reset = nxt.get("normal") if isinstance(nxt, dict) else None
    return Stock(normal=normal, mirage=mirage, next_reset=next_reset, source=API_URL)


def fetch_from_fruityblox() -> Stock | None:
    html = _http_get(STOCK_URL, "text/html,application/xhtml+xml")
    if html is None:
        return None
    stock = parse_stock(html, source=STOCK_URL)
    if not stock.normal and not stock.mirage:
        print("[warn] fruityblox fetched but no stock parsed", file=sys.stderr)
        return None
    return stock


def fetch_stock() -> Stock | None:
    return fetch_from_api() or fetch_from_fruityblox()


# ==================== ENHANCED RICH EMBED + DELTAS + COUNTDOWN ====================
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
    prev_normal = prev_state.get("normal", [])
    prev_mirage = prev_state.get("mirage", [])
    embed = {
        "title": "🛒 Blox Fruits Stock Update",
        "color": EMBED_COLOR,
        "fields": [
            {"name": "🌊 Normal Stock", "value": _field_value_with_delta(stock.normal, prev_normal), "inline": True},
            {"name": "✨ Mirage Stock", "value": _field_value_with_delta(stock.mirage, prev_mirage), "inline": True},
        ],
        "footer": {"text": "Powered by CURY Oracle • 2341"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    countdown = parse_next_reset(stock.next_reset)
    if countdown:
        embed["fields"].append({"name": "⏳ Next Reset", "value": f"**{countdown['text']}**", "inline": False})
    return embed


# ==================== NOTIFICATIONS ====================
def notify_console(title: str, body: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== {ts}  {title} ===\n{body}\n")


def notify_discord(webhook: str, stock: Stock, prev_state: dict, content: str = "") -> None:
    body = {
        "username": "Blox Fruits Real-Time Oracle",
        "embeds": [build_embed(stock, prev_state)],
    }
    if content:
        body["content"] = content
        body["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
    except Exception as e:
        print(f"[warn] discord notify failed: {e}", file=sys.stderr)


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


# ==================== STATE & ROBUST CHANGE DETECTION ====================
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


# ==================== MAIN LOGIC ====================
def load_config(path: str = "config.json") -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        print("[warn] No config found, using defaults", file=sys.stderr)
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
        print(f"Created starter {args.config}")
        return 0

    config = load_config(args.config)
    webhook = config.get("discord_webhook") or os.getenv("DISCORD_WEBHOOK")
    state_file = config.get("state_file", "state.json")

    if args.once or not webhook:
        stock = fetch_stock()
        if stock:
            notify_console("Current Stock", f"Normal: {len(stock.normal)} | Mirage: {len(stock.mirage)}")
        return 0

    print("CURY Enhanced Notifier running...")
    while True:
        stock = fetch_stock()
        if stock:
            last_state = load_state(state_file)
            if should_notify(stock, last_state):
                print("[info] Stock change detected (Mirage or Normal)!")
                content = config.get("ping_target", "") if any(norm_name(f) in config.get("ping_fruits", []) for f in stock.mirage + stock.normal) else ""
                notify_discord(webhook, stock, last_state, content)
                if config.get("desktop_toast"):
                    notify_desktop("Blox Fruits Rotation!", "Stock updated!")
                save_state(stock, state_file)
            else:
                print("[debug] No change")
        time.sleep(config.get("poll_seconds", 300))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
