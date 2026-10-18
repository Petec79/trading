#!/usr/bin/env python3
"""
Discord helpers for spicy-trading.
Both webhook (real-time) and bot-API (cron) modes.
"""

import json, os, urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Channels ──────────────────────────────────────────────────────────────────
SIGNALS_CHANNEL = "1506260659032035340"   # spicy-trading → signals
JOURNAL_CHANNEL  = "1506260660827459615"   # spicy-trading → journal

ENV_FILE = Path("/home/openclaw/.hermes/.env")
STATE_FILE = Path("/home/openclaw/.hermes/state/discord_status.json")

def load_env():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

# ── Bot API helpers ───────────────────────────────────────────────────────────
def bot_headers():
    load_env()
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

def discord_get_messages(channel_id, limit=3):
    """Get last N messages from a channel."""
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}",
        headers=bot_headers()
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def discord_edit_message(channel_id, message_id, content):
    """Edit an existing message."""
    load_env()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
        data=json.dumps({"content": content}).encode(),
        headers={**bot_headers(), "Content-Type": "application/json"}
    )
    req.get_method = lambda: "PATCH"
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def discord_post(channel_id, content):
    """Post a new message to a channel."""
    load_env()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=json.dumps({"content": content}).encode(),
        headers={**bot_headers(), "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

# ── Webhook helpers ───────────────────────────────────────────────────────────
def discord_webhook(url, content, username="SpicyScanner"):
    """Post via webhook (no auth needed, no rate limits)."""
    payload = json.dumps({
        "content": content,
        "username": username,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()

# ── Signal formatters ─────────────────────────────────────────────────────────
def format_signal_embed(signal: dict) -> dict:
    """Build a rich Discord embed for a signal."""
    direction = signal.get("direction", "short").upper()
    emoji = "🟢" if direction == "LONG" else "🔴"
    score = signal.get("quality_score", 0)
    coin = signal.get("coin", signal.get("symbol", "???"))

    entry = signal.get("entry_price", 0)
    sl = signal.get("sl_price", 0)
    tp = signal.get("tp_price", 0)
    flags = signal.get("flags", [])

    fields = [
        {"name": "Direction", "value": f"{emoji} {direction}", "inline": True},
        {"name": "Quality", "value": f"{score}/3", "inline": True},
        {"name": "Entry", "value": f"{entry:.4f}" if entry else "?", "inline": True},
        {"name": "Stop Loss", "value": f"{sl:.4f}" if sl else "?", "inline": True},
        {"name": "Take Profit", "value": f"{tp:.4f}" if tp else "?", "inline": True},
    ]
    if flags:
        fields.append({"name": "Flags", "value": " | ".join(flags), "inline": False})

    return {
        "title": f"🌶️ {coin} — {direction}",
        "description": signal.get("thesis", ""),
        "color": 0x00FF00 if direction == "LONG" else 0xFF0000,
        "fields": fields,
        "footer": {"text": f"Scanner: {signal.get('scanner','momentum')} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def format_trade_embed(trade: dict) -> dict:
    """Build a rich Discord embed for a closed trade."""
    outcome = trade.get("outcome", "unknown")
    emoji = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
    pnl_r = trade.get("pnl_r", 0)
    coin = trade.get("coin", trade.get("symbol", "???"))
    direction = trade.get("direction", "???").upper()
    entry = trade.get("entry_price", 0)
    exit_px = trade.get("exit_price", 0)

    return {
        "title": f"{emoji} {coin} {direction} — {outcome.upper()}",
        "description": f"**PnL:** {pnl_r:+.2f}R\n**Entry:** {entry:.4f}\n**Exit:** {exit_px:.4f}",
        "color": 0x00FF00 if outcome == "win" else 0xFF0000,
        "footer": {"text": f"Closed {trade.get('closed_at', datetime.now(timezone.utc).strftime('%d %b %H:%M'))}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# ── High-level posting ─────────────────────────────────────────────────────────
def post_signal(signal: dict):
    """Post a signal to Discord #signals via bot-API."""
    coin = signal.get("coin", signal.get("symbol", "???"))
    direction = signal.get("direction", "short").upper()
    score = signal.get("quality_score", 0)
    thesis = signal.get("thesis", "")
    entry = signal.get("entry_price", 0)
    sl = signal.get("sl_price", 0)
    tp = signal.get("tp_price", 0)
    content = (
        f"🌶️ **{coin}** {direction} — quality {score}/3\n"
        f"_{thesis}_\n"
        f"Entry: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`"
    )
    discord_post(SIGNALS_CHANNEL, content)

def post_trade_result(trade: dict):
    """Post a closed trade result to Discord #journal via bot-API."""
    outcome = trade.get("outcome", "unknown")
    emoji = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
    coin = trade.get("coin", trade.get("symbol", "???"))
    direction = trade.get("direction", "???").upper()
    pnl_r = trade.get("pnl_r", 0)
    content = (
        f"{emoji} **{coin}** {direction} — {outcome.upper()}\n"
        f"PnL: {pnl_r:+.2f}R | Entry: {trade.get('entry_price','?')} → Exit: {trade.get('exit_price','?')}"
    )
    discord_post(JOURNAL_CHANNEL, content)

def post_scan_summary(scan_data: dict, report: str):
    """Post daily scanner summary to #signals."""
    bias = scan_data.get("bias", "UNKNOWN")
    strength = scan_data.get("strength", "UNKNOWN")
    top_longs = scan_data.get("top_longs", [])[:3]
    top_shorts = scan_data.get("top_shorts", [])[:3]

    lines = [
        f"**🌶️ SPICY SCAN — {datetime.now(timezone.utc).strftime('%d %b %H:%M UTC')}**",
        f"Bias: **{bias}** | Strength: **{strength}**",
        "",
    ]
    if top_longs:
        longs_str = " | ".join([f"{s['symbol']} ({s.get('quality_score',0)})" for s in top_longs])
        lines.append(f"🟢 Longs: {longs_str}")
    if top_shorts:
        shorts_str = " | ".join([f"{s['symbol']} ({s.get('quality_score',0)})" for s in top_shorts])
        lines.append(f"🔴 Shorts: {shorts_str}")

    content = "\n".join(lines)
    discord_post(SIGNALS_CHANNEL, content)