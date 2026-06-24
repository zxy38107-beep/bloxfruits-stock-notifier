#!/usr/bin/env python3
"""
Blox Fruits Stock Notifier
==========================

A *legit* companion tool. It does NOT touch the Roblox client or the game in any
way. It only polls a PUBLIC community stock API and tells you when fruits you
care about appear in the in-game Blox Fruit Dealer shop (Normal stock and
Mirage/Advanced stock, which rotate every 4 hours).

Notifications:
  - Discord webhook  (cross-platform, recommended)
  - Desktop toast    (Windows, optional: pip install win10toast OR plyer)
  - Console          (always; the fallback)

Core runs on the Python standard library alone (no pip install required).
Optional packages only enhance desktop notifications.

Usage:
  python stock_notifier.py --init            # write a starter config.json
  python stock_notifier.py                    # run with ./config.json
  python stock_notifier.py --once             # check one time and exit
  python stock_notifier.py --config path.json # custom config location

The data-source adapter is isolated in `parse_stock()` and `STOCK_ENDPOINTS`
so the verified public endpoint + schema can be slotted in without touching
the rest of the program.
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

# --------------------------------------------------------------------------- #
# Data source  (THE ONLY PART THAT DEPENDS ON THE EXTERNAL SITE)
# --------------------------------------------------------------------------- #
# There is no OFFICIAL no-auth JSON API for Blox Fruits stock, but two community
# sources work and we use both for resilience:
#
#   1. bloxfruitvalues JSON API (primary) — clean JSON with reset timestamps,
#      but its own scraper can break and return empty arrays (success:true,
#      error:"Scraping failed..."). We detect that and fall back.
#   2. fruityblox.com (fallback) — server-renders stock as JSON embedded in its
#      HTML; we scrape the "normal":[...] / "mirage":[...] arrays out of it.
#
# This is the ONLY part that depends on external sites. If a source changes its
# shape, only the fetch_* / parse_* functions below need updating.
API_URL = "https://www.bloxfruitvalues.net/api/stocks"
STOCK_URL = "https://fruityblox.com/stock"  # fallback (HTML scrape)

# Browser-like UA: the tracker serves the data-bearing HTML to normal browsers.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT = 20  # seconds

# Fruits that are ALWAYS in stock — exclude from "new arrival" watch logic.
ALWAYS_IN_STOCK = {"rocket", "spin"}

# --------------------------------------------------------------------------- #
# Fruit emoji map  (EDIT THIS to use your server's real Blox Fruits emojis)
# --------------------------------------------------------------------------- #
# Discord shows a custom (game) emoji only if you give it that emoji's ID from a
# server where it's uploaded, in the form  <:name:1234567890>  (animated:
# <a:name:...>). There is no way to reference another server's emojis without
# the ID, so this ships with plain Unicode fallbacks that render everywhere.
#
# To use the actual in-game fruit emojis:
#   1. Upload the fruit emojis to your Discord server.
#   2. In Discord, type \:dragon:  (with the backslash) and send it — it prints
#      the raw form like <:dragon:1357924680>.
#   3. Paste that string as the value below, keyed by the normalized fruit name.
FRUIT_EMOJI = {
    # "dragon":  "<:dragon:PUT_ID_HERE>",
    # "kitsune": "<:kitsune:PUT_ID_HERE>",
    # ... add the rest of your server's fruit emojis here ...
}
DEFAULT_EMOJI = "\U0001F34E"  # 🍎 fallback when a fruit has no mapping

# Purple embed bar (Discord color is a decimal int; 0x9B59B6 = amethyst purple).
EMBED_COLOR = 0x9B59B6


def fruit_emoji(name: str) -> str:
    return FRUIT_EMOJI.get(norm_name(name), DEFAULT_EMOJI)


@dataclass
class Stock:
    """Normalized view of the shop, independent of any provider's JSON shape.

    `normal` and `mirage` are lists of {"name": str, "price": int|None} dicts.
    """
    normal: list[dict] = field(default_factory=list)
    mirage: list[dict] = field(default_factory=list)
    next_reset: str | None = None          # ISO/string if the source exposes one
    source: str | None = None              # endpoint it came from

    def all_names(self) -> set[str]:
        return {norm_name(f["name"]) for f in (*self.normal, *self.mirage)}


def norm_name(name: str) -> str:
    """Normalize a fruit name for matching: lowercase, strip noise words."""
    n = str(name).strip().lower()
    for junk in (" fruit", "-", "_"):
        n = n.replace(junk, " ")
    return " ".join(n.split())


def fmt_price(price) -> str:
    """Render a price like 1400000 -> '1.4M', 60000 -> '60K'."""
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


# Matches a JSON array literal that immediately follows a "normal": or
# "mirage": key in the page's embedded data. fruityblox server-renders the
# stock as JSON inside its HTML (App Router streamed payload), and the keys
# may appear with escaped quotes (\"normal\":) inside a script string.
_ARR_RE_TEMPLATE = r'\\?"%s\\?"\s*:\s*(\[.*?\])'
# Each fruit object looks like {"name":"Light","price":650000,"robuxPrice":...}.
# Capture name and the immediately-following price (escaped-quote tolerant).
_OBJ_RE = re.compile(
    r'\\?"name\\?"\s*:\s*\\?"([^"\\]+)\\?"'
    r'(?:\s*,\s*\\?"price\\?"\s*:\s*(\d+))?'
)


def _extract_array(html: str, key: str) -> list[dict]:
    """Find the JSON array after `"key":` and return [{name, price}, ...].

    We can't json.loads the whole page, so we locate the array's text span by
    bracket-matching from the key, then pull each name/price pair out of that
    span. Bracket-matching (vs a greedy regex) avoids swallowing a later array.
    """
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
    """Extract Normal and Mirage stock from the fetched fruityblox HTML."""
    normal = _extract_array(html, "normal")
    mirage = _extract_array(html, "mirage")
    return Stock(normal=normal, mirage=mirage, next_reset=None, source=source)


def _http_get(url: str, accept: str) -> str | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[warn] fetch failed for {url}: {e}", file=sys.stderr)
        return None


def _items_from_objs(arr: Any) -> list[dict]:
    """Pull [{name, price}, ...] from a list of fruit objects (or strings)."""
    out: list[dict] = []
    if not isinstance(arr, list):
        return out
    for it in arr:
        if isinstance(it, str):
            out.append({"name": it, "price": None})
        elif isinstance(it, dict):
            name = next((it[k] for k in ("name", "fruit", "Name", "title")
                         if isinstance(it.get(k), str)), None)
            if not name:
                continue
            price = next((it[k] for k in ("price", "value", "Price")
                          if isinstance(it.get(k), (int, float))), None)
            out.append({"name": name, "price": price})
    return out


def fetch_from_api() -> Stock | None:
    """Primary source: the bloxfruitvalues JSON API.

    Returns None (so the caller can fall back) when the API is reachable but
    its own scraper failed — it reports success:true with empty arrays and an
    `error` field in that case.
    """
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
        print(f"[info] API has no stock data ({err}); falling back",
              file=sys.stderr)
        return None

    nxt = data.get("nextUpdate") or {}
    next_reset = nxt.get("normal") if isinstance(nxt, dict) else None
    return Stock(normal=normal, mirage=mirage,
                 next_reset=next_reset, source=API_URL)


def fetch_from_fruityblox() -> Stock | None:
    """Fallback source: scrape the embedded stock JSON from fruityblox HTML."""
    html = _http_get(STOCK_URL, "text/html,application/xhtml+xml")
    if html is None:
        return None
    stock = parse_stock(html, source=STOCK_URL)
    if not stock.normal and not stock.mirage:
        print("[warn] fruityblox fetched but no stock parsed — markup may have "
              "changed; update parse_stock().", file=sys.stderr)
        return None
    return stock


def fetch_stock() -> Stock | None:
    """Try the JSON API first, then fall back to the fruityblox HTML scrape."""
    return fetch_from_api() or fetch_from_fruityblox()


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
def notify_console(title: str, body: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== {ts}  {title} ===\n{body}\n")


def _field_value(items: list[dict]) -> str:
    """One line per fruit: '<emoji> Name - 1.4M'. Discord field cap is 1024."""
    if not items:
        return "*(empty)*"
    lines = []
    for it in items:
        name = it["name"]
        price = fmt_price(it.get("price"))
        emoji = fruit_emoji(name)
        lines.append(f"{emoji} **{name}**" + (f" - {price}" if price else ""))
    return "\n".join(lines)[:1024]


def build_embed(stock: Stock) -> dict:
    """Rich embed: purple bar, Normal + Mirage as two fields, footer + time."""
    embed = {
        "title": "\U0001F4E6 Blox Fruits Stock Update",
        "color": EMBED_COLOR,
        "fields": [
            {"name": "\U0001F30A Normal Stock",
             "value": _field_value(stock.normal), "inline": True},
            {"name": "\U00002728 Mirage Stock",
             "value": _field_value(stock.mirage), "inline": True},
        ],
        "footer": {"text": "Blox Fruits Stock"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if stock.next_reset:
        embed["fields"].append(
            {"name": "\U000023F0 Next Reset", "value": str(stock.next_reset),
             "inline": False})
    return embed


def notify_discord(webhook: str, stock: Stock, content: str = "") -> None:
    """Post the stock embed. `content` carries the @mention ping (if any)."""
    body = {
        "username": "Blox Fruits Stock",
        "embeds": [build_embed(stock)],
    }
    if content:
        body["content"] = content
        # Allow the ping to actually notify (roles/users/everyone).
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
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[warn] discord notify failed: {e}", file=sys.stderr)


def notify_desktop(title: str, body: str) -> bool:
    """Best-effort Windows toast. Returns True if a toast was shown."""
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(title, body, duration=10, threaded=True)
        return True
    except Exception:
        pass
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=body, timeout=10)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Config + state
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    watchlist: list[str]
    poll_seconds: int = 300
    discord_webhook: str = ""
    desktop_toast: bool = True
    state_file: str = "state.json"
    # Fruits that trigger an @mention ping when they newly appear in stock.
    ping_fruits: list[str] = field(default_factory=lambda: ["Kitsune", "Dragon"])
    # What to ping: "@everyone", a role "<@&ROLE_ID>", or a user "<@USER_ID>".
    ping_target: str = "@everyone"


DEFAULT_CONFIG = {
    "watchlist": ["Dragon", "Leopard", "Kitsune", "Dough", "Mammoth", "Gas"],
    "poll_seconds": 300,
    "discord_webhook": "",
    "desktop_toast": True,
    "state_file": "state.json",
    "ping_fruits": ["Kitsune", "Dragon"],
    "ping_target": "@everyone",
}


def load_config(path: str) -> Config:
    # utf-8-sig tolerates a UTF-8 BOM (common when a config is edited in Notepad
    # or written by PowerShell's Set-Content -Encoding utf8) and plain UTF-8.
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)

    # Secrets/overrides come from the environment when present (e.g. GitHub
    # Actions Secrets) so the webhook URL never has to live in a committed file.
    webhook = os.environ.get("DISCORD_WEBHOOK") or str(raw.get("discord_webhook", ""))
    ping_target = os.environ.get("PING_TARGET") or str(raw.get("ping_target", "@everyone"))
    # On CI runners there is no desktop, so toasts are pointless/erroring there.
    desktop_default = bool(raw.get("desktop_toast", True))
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        desktop_default = False

    return Config(
        watchlist=list(raw.get("watchlist", [])),
        poll_seconds=int(raw.get("poll_seconds", 300)),
        discord_webhook=webhook,
        desktop_toast=desktop_default,
        state_file=str(raw.get("state_file", "state.json")),
        ping_fruits=list(raw.get("ping_fruits", ["Kitsune", "Dragon"])),
        ping_target=ping_target,
    )


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Core check
# --------------------------------------------------------------------------- #
def format_stock(stock: Stock) -> str:
    """Plain-text rendering for console / desktop toast."""
    def line(items):
        return ", ".join(
            it["name"] + (f" ({fmt_price(it.get('price'))})"
                          if it.get("price") else "")
            for it in items) or "(none)"
    lines = ["Normal: " + line(stock.normal), "Mirage: " + line(stock.mirage)]
    if stock.next_reset:
        lines.append(f"Next reset: {stock.next_reset}")
    return "\n".join(lines)


def run_check(cfg: Config) -> None:
    stock = fetch_stock()
    if stock is None:
        print("[warn] could not retrieve stock this cycle", file=sys.stderr)
        return

    state = load_state(cfg.state_file)
    ping_set = {norm_name(p) for p in cfg.ping_fruits}
    present_names = stock.all_names()

    # Signature of the current rotation, so we act only when stock CHANGES.
    sig = "|".join(sorted(present_names))
    changed = sig != state.get("last_signature")

    # Ping fruits newly present this rotation (vs the previous rotation), minus
    # the always-in-stock ones. This is what decides whether we @mention.
    prev_present = set(state.get("last_present", []))
    new_ping = sorted(
        n for n in present_names
        if n in ping_set and n not in ALWAYS_IN_STOCK and n not in prev_present
    )

    if changed:
        # Always send the full stock embed when the rotation changes.
        content = ""
        if new_ping:
            pretty = ", ".join(n.title() for n in new_ping)
            content = f"{cfg.ping_target} **{pretty}** is in stock!"
        title = "Stock updated" + (f"  (PING: {', '.join(new_ping)})"
                                   if new_ping else "")
        notify_console(title, format_stock(stock))
        if cfg.discord_webhook:
            notify_discord(cfg.discord_webhook, stock, content=content)
        if cfg.desktop_toast:
            toast_title = ("Blox Fruits: " + ", ".join(n.title() for n in new_ping)
                           + " in stock!") if new_ping else "Blox Fruits stock updated"
            notify_desktop(toast_title, format_stock(stock))
    else:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] checked ({stock.source}); no change")

    state["last_signature"] = sig
    state["last_present"] = sorted(present_names)
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    save_state(cfg.state_file, state)


def run_loop(cfg: Config) -> None:
    print(f"Watching: {', '.join(cfg.watchlist) or '(nothing)'}")
    print(f"Polling every {cfg.poll_seconds}s. Ctrl+C to stop.")
    while True:
        try:
            run_check(cfg)
        except Exception as e:  # keep the loop alive across transient failures
            print(f"[error] check failed: {e}", file=sys.stderr)
        time.sleep(max(30, cfg.poll_seconds))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_init(path: str) -> None:
    if os.path.exists(path):
        print(f"{path} already exists; not overwriting.")
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    print(f"Wrote starter config to {path}. Edit your watchlist and (optionally) "
          f"add a discord_webhook, then run: python stock_notifier.py")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Blox Fruits stock notifier (companion tool).")
    ap.add_argument("--config", default="config.json", help="path to config.json")
    ap.add_argument("--init", action="store_true", help="write a starter config and exit")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    args = ap.parse_args(argv)

    if args.init:
        cmd_init(args.config)
        return 0

    if not os.path.exists(args.config):
        print(f"No config at {args.config}. Run with --init first.", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    if args.once:
        run_check(cfg)
    else:
        try:
            run_loop(cfg)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
