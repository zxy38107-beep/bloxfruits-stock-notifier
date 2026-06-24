# Blox Fruits Stock Notifier

A small, **legit** companion tool that tells you when fruits you care about show up
in the in-game Blox Fruit Dealer shop (Normal stock, rotates every 4h; Mirage /
Advanced stock, rotates every 2h).

**It does not touch Roblox or the game in any way.** It only reads *public*
community stock data and notifies you — no executor, no injection, no login,
nothing against Roblox's Terms of Service. Just a poller + notifier.

## How it works

- **Primary source:** the `bloxfruitvalues` public JSON API
  (`https://www.bloxfruitvalues.net/api/stocks`), which includes reset times.
- **Fallback source:** `fruityblox.com`, scraped when the API's own data is
  temporarily empty (their scraper occasionally breaks). The tool detects that
  and falls back automatically.
- It posts a **rich Discord embed every time the stock rotates** (purple bar,
  Normal + Mirage shown as two fields, each fruit as `emoji Name - price`,
  "Blox Fruits Stock" footer). It remembers the last rotation in `state.json`,
  so it won't repeat for the same 4h cycle.
- It **@mentions (pings)** only when a *ping fruit* — by default **Kitsune** or
  **Dragon** — newly appears. Other rotations post the embed quietly, no ping.
- `Rocket` and `Spin` are always in stock, so they never trigger a ping.

## Fruit emojis (using the real in-game emojis)

The embed ships with a 🍎 fallback for every fruit. To show the actual Blox
Fruits emojis, Discord needs each emoji's ID from a server where it's uploaded:

1. Upload the fruit emojis to your Discord server.
2. In any channel type `\:dragon:` (note the leading backslash) and send it —
   Discord prints the raw form, e.g. `<:dragon:1357924680>`.
3. Open `stock_notifier.py`, find the `FRUIT_EMOJI` map near the top, and add an
   entry keyed by the lowercase fruit name:
   ```python
   FRUIT_EMOJI = {
       "dragon":  "<:dragon:1357924680>",
       "kitsune": "<:kitsune:1357924681>",
   }
   ```
Anything not in the map just uses the 🍎 fallback — partial maps are fine.

## Requirements

- **Python 3.9+** — the core uses only the standard library, so **no
  `pip install` is required**.
- *Optional*, for Windows desktop pop-up toasts: `pip install win10toast`
  (or `pip install plyer`). Without these, notifications still go to the console
  and/or Discord.

## Quick start

```bash
# 1. Create a starter config
python stock_notifier.py --init

# 2. Edit config.json — set your watchlist (and optionally a Discord webhook)

# 3. Run it (polls on a loop)
python stock_notifier.py

# Or check once and exit:
python stock_notifier.py --once
```

## Configuration (`config.json`)

```json
{
  "watchlist": ["Dragon", "Leopard", "Kitsune", "Dough", "Mammoth", "Gas"],
  "poll_seconds": 300,
  "discord_webhook": "",
  "desktop_toast": true,
  "state_file": "state.json",
  "ping_fruits": ["Kitsune", "Dragon"],
  "ping_target": "@everyone"
}
```

| Field | Meaning |
|---|---|
| `watchlist` | Informational list of fruits you care about (case-insensitive). |
| `poll_seconds` | How often to check (minimum enforced: 30s). 300 = every 5 min. |
| `discord_webhook` | Discord webhook URL (see below). Leave `""` to disable Discord. |
| `desktop_toast` | Show a Windows toast pop-up (needs `win10toast`/`plyer`). |
| `state_file` | Where the last-seen stock is remembered (for dedup). |
| `ping_fruits` | Fruits that trigger an `@mention` ping when they newly appear. |
| `ping_target` | Who to ping: `"@everyone"`, a role `"<@&ROLE_ID>"`, or a user `"<@USER_ID>"`. |

**Note:** the embed posts on *every* stock rotation; `ping_fruits` only controls
when it adds the loud `@mention`. To ping a role instead of everyone, set
`ping_target` to `"<@&YOUR_ROLE_ID>"` (right-click a role → Copy ID with
Developer Mode on); for yourself use `"<@YOUR_USER_ID>"`.

## Getting a Discord webhook (optional, recommended)

Discord webhooks are the most reliable channel and work on any OS / phone:

1. In your Discord server: **Server Settings → Integrations → Webhooks → New Webhook**.
2. Pick a channel, click **Copy Webhook URL**.
3. Paste it into `discord_webhook` in `config.json`.

## Notes

- Stock is **global** — every server worldwide shows the same rotation at the
  same time, anchored to UTC (00:00, 04:00, 08:00, … for Normal). Server-hopping
  doesn't change it.
- The data comes from fan trackers, which can lag the in-game dealer by a minute
  or two around a reset, and are unofficial. Treat the in-game dealer as the
  source of truth.
- If a tracker changes its format and parsing breaks, the tool logs a clear
  warning telling you which function to update. The external dependency is fully
  isolated to the `fetch_*` / `parse_*` functions at the top of
  `stock_notifier.py`.

## Running it continuously

- **Just leave the terminal open:** `python stock_notifier.py`
- **Windows Task Scheduler:** create a task that runs
  `python C:\path\to\stock_notifier.py --once` on a schedule (e.g. every 5 min)
  if you prefer not to keep a window open. (Use `--once` for scheduled runs;
  the dedup `state.json` keeps it from re-alerting.)

## Hosting 24/7 on GitHub Actions (free, no server)

This repo includes `.github/workflows/stock.yml`, which runs the check every
~10 minutes on GitHub's free runners — no machine of your own required.

Because each run is a fresh, throwaway machine:
- it runs `--once` (not the loop), and
- it commits `state.json` back to the repo so dedup survives between runs.

### Setup

1. **Push this folder to a GitHub repo.**
2. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, and add:
   - `DISCORD_WEBHOOK` — your Discord webhook URL. **Keep this in Secrets, never
     in `config.json`**, especially in a public repo — a committed webhook can be
     abused to spam your channel.
   - `PING_TARGET` *(optional)* — e.g. `@everyone`, `<@&ROLE_ID>`, or
     `<@USER_ID>`. If unset, it uses the value in `config.json`.
3. In **Settings → Actions → General → Workflow permissions**, select
   **Read and write permissions** (so the workflow can push `state.json`).
4. Go to the **Actions** tab, pick *Blox Fruits Stock Check*, and click
   **Run workflow** once to test it. After that the schedule takes over.

### Notes

- GitHub's scheduled runs are **best-effort** and can lag a few minutes under
  load; the minimum interval is 5 minutes. That's fine for 2-4h rotations.
- The workflow only commits when stock actually changes, so it won't spam your
  commit history every 10 minutes.
- `config.json` is read for the watchlist/ping settings; the webhook comes from
  the Secret. `desktop_toast` is auto-disabled on the runner (no desktop).
