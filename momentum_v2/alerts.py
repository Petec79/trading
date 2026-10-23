"""
Manual-trading alerts for the v2 bots (paper only — nothing is ever ordered).

Telegram is the primary channel (push to phone; follow-ups are threaded as
replies under the original signal). Discord gets the same text for the record.

Every actionable message carries:
  * the candle close time and the send time (so you can see the delay)
  * the exact order: MARKET now, or LIMIT at a price with its cancel rules
  * stop / target as prices AND % distances (your venue's prices may differ)
  * R:R, and size for a $10k account risking 1% (ALERT_ACCOUNT / ALERT_RISK_PCT)
  * a validity line: when the signal is no longer worth taking
Signals that arrive late (bot catching up after an outage) are marked LATE.

Env (in ~/.hermes/.env): TELEGRAM_BOT_TOKEN, TRADING_TELEGRAM_CHAT_ID (falls
back to the first id in TELEGRAM_ALLOWED_USERS), DISCORD_SIGNALS_WEBHOOK,
ALERT_ACCOUNT (default 10000), ALERT_RISK_PCT (default 1), ALERT_LATE_SECONDS (90).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .core import Trade
from .reporting import SETUP_NAMES, _load_hermes_env, discord

_load_hermes_env()

MARKET_VALID_FRACTION = 0.3     # skip a market signal once price has used 30% of the way to target


def _hhmm(ms: Optional[int]) -> str:
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")


def _px(x: Optional[float]) -> str:
    if x is None:
        return "—"
    if x >= 100:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6g}"


def _pct(a: float, b: float) -> str:
    return f"{(b - a) / a * 100:+.2f}%"


def sizing(ref: float, stop: float) -> dict:
    acct = float(os.environ.get("ALERT_ACCOUNT", "10000"))
    risk_pct = float(os.environ.get("ALERT_RISK_PCT", "1"))
    risk_usd = acct * risk_pct / 100
    dist = abs(ref - stop) / ref
    notional = risk_usd / dist if dist > 0 else 0.0
    return {"account": acct, "risk_pct": risk_pct, "risk_usd": risk_usd,
            "notional": notional, "qty": notional / ref if ref else 0.0, "leverage": notional / acct}


def rr(ref: float, stop: float, target: Optional[float]) -> Optional[float]:
    if target is None or ref == stop:
        return None
    return abs(target - ref) / abs(ref - stop)


class Notifier:
    def __init__(self, strategy: str, cfg=None):
        self.strategy = strategy
        self.cfg = cfg
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TRADING_TELEGRAM_CHAT_ID") or \
            (os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip() or None)
        self.chat = chat
        self.late_s = float(os.environ.get("ALERT_LATE_SECONDS", "90"))
        self.sent: list[dict] = []          # for tests / debugging

    # ── transport ──────────────────────────────────────────────────────────
    def send(self, text: str, reply_to: Optional[int] = None) -> Optional[int]:
        self.sent.append({"text": text, "reply_to": reply_to})
        msg_id = None
        if self.token and self.chat:
            try:
                body = {"chat_id": self.chat, "text": text[:4000], "disable_web_page_preview": True}
                if reply_to:
                    body["reply_to_message_id"] = reply_to
                    body["allow_sending_without_reply"] = True
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    msg_id = json.loads(r.read()).get("result", {}).get("message_id")
            except Exception as e:
                print(f"[telegram] send failed: {e}", flush=True)
        discord("signals", text)
        return msg_id

    @staticmethod
    def _now(now_ms: Optional[int]) -> int:
        """The real clock at send time (the cycle clock can be a minute old by now)."""
        return max(int(time.time() * 1000), now_ms or 0)

    def late(self, event_close_ms: int, now_ms: int) -> bool:
        return self._now(now_ms) - event_close_ms > self.late_s * 1000

    # ── messages ───────────────────────────────────────────────────────────
    def signal(self, t: Trade, now_ms: int, news_line: str = "") -> Optional[int]:
        now_ms = self._now(now_ms)
        icon = "🟢" if t.side == "long" else "🔴"
        name = SETUP_NAMES.get(t.setup, t.setup)
        late = self.late(t.signal_t, now_ms)
        ref = t.details.get("ref_entry") or t.level
        d = 1 if t.side == "long" else -1
        head = (f"{'⚠️ LATE — bot catching up, do NOT act. ' if late else ''}{icon} {t.symbol.replace('USDT', '')} "
                f"{t.side.upper()} — {name} ({t.score}/3)\n"
                f"candle closed {_hhmm(t.signal_t)} UTC · sent {_hhmm(now_ms)}")
        if t.entry_type == "market":
            order = f"MARKET NOW (~{_px(ref)})"
            if t.target is not None:
                skip = ref + d * MARKET_VALID_FRACTION * abs(t.target - ref)
                valid = f"Valid: skip if price is already past {_px(skip)} ({_pct(ref, skip)}) when you see this"
            else:
                valid = "Valid: act within ~60s of the candle close"
        else:
            exp = t.signal_t + self._expiry_min() * 60_000
            trip = None if t.target is None else t.level + self._missed_frac() * (t.target - t.level)
            order = f"LIMIT {'BUY' if d == 1 else 'SELL'} at {_px(t.level)}"
            valid = (f"Cancel at {_hhmm(exp)[:5]} UTC" +
                     (f" or if price reaches {_px(trip)} first" if trip else "") + " (unfilled)")
        sz = sizing(ref, t.stop)
        r = rr(ref, t.stop, t.target)
        tp = (f"TP {_px(t.target)} ({_pct(ref, t.target)})" if t.target is not None
              else "TP none — trail stop to each new swing (follow-ups will say when)")
        lines = [head, order,
                 f"SL {_px(t.stop)} ({_pct(ref, t.stop)}) | {tp}",
                 f"R:R {r:.2f}" if r is not None else "R:R open (trailing)",
                 f"${sz['account']:,.0f} @ {sz['risk_pct']:g}% → risk ${sz['risk_usd']:,.0f} → size "
                 f"${sz['notional']:,.0f} (≈{sz['qty']:,.4g} {t.symbol.replace('USDT', '')}, {sz['leverage']:.1f}x)",
                 valid]
        crit = " ".join(f"{k.replace('_', ' ')} {'✓' if v else '✗'}" for k, v in t.criteria.items())
        lines.append(crit)
        if news_line:
            lines.append(news_line)
        lines.append("paper signal — your call")
        # remember what we told you, so follow-ups can flag any change at fill
        t.details["alert_stop"], t.details["alert_target"] = t.stop, t.target
        mid = self.send("\n".join(lines))
        if mid:
            t.details["tg_msg_id"] = mid
        return mid

    def _expiry_min(self) -> int:
        if self.cfg is not None:
            return int(self.cfg.limit_expiry_bars * self.cfg.bar_ms / 60_000)
        return 60

    def _missed_frac(self) -> float:
        if self.cfg is not None:
            return self.cfg.missed_fraction
        return 0.9 if self.strategy == "mean_reversion" else 1.0

    def followup(self, kind: str, t: Trade, now_ms: int, bar_close_ms: int, why: str = "") -> None:
        sym = t.symbol.replace("USDT", "")
        late = "⚠️ LATE " if self.late(bar_close_ms, now_ms) else ""
        reply = t.details.get("tg_msg_id")
        if kind == "fill":
            txt = f"{late}✅ {sym} {t.side} filled at {_px(t.entry)} (paper). Stop {_px(t.cur_stop)}, " \
                  f"TP {_px(t.target)}."
            a_stop, a_tgt = t.details.get("alert_stop"), t.details.get("alert_target")
            if a_stop is not None and abs(a_stop - t.cur_stop) > 1e-12:
                txt += (f"\n⚠️ STOP CHANGED: now {_px(t.cur_stop)} (signal said {_px(a_stop)}) — "
                        f"re-based on the fill price, move yours to match")
            if a_tgt is not None and t.target is not None and abs(a_tgt - t.target) > 1e-12:
                txt += f"\n⚠️ TARGET CHANGED: now {_px(t.target)} (signal said {_px(a_tgt)})"
            if t.journal_id:
                sz = sizing(t.entry, t.cur_stop)
                txt += (f"\nTook it? Log yours (put in YOUR fill):\n"
                        f"python3 journal/journal.py manual-open --signal {t.journal_id} --coin {sym} "
                        f"--direction {t.side} --entry YOUR_FILL --stop {t.cur_stop:g} --size-usd {sz['notional']:.0f}")
        elif kind == "cancel":
            reason = {"missed_ran_to_target": "price ran most of the way to target without filling",
                      "limit_expired": "not filled in time",
                      "gapped_through_stop": "price went through the stop before filling",
                      "target_too_close_after_fill": "not enough room left to target"}.get(t.exit_reason, t.exit_reason)
            if t.entry_type == "market":
                # there was no order to cancel — you may already be in the trade
                act = ("If you entered at market, EXIT NOW — price is already through the stop."
                       if t.exit_reason == "gapped_through_stop" else
                       "If you entered at market, the move left is too small — exit at about breakeven.")
                txt = f"{late}⛔ {sym}: bot did NOT take this {t.side} — {reason}.\n{act}"
            else:
                txt = f"{late}🚫 {sym}: CANCEL the {t.side} limit order — {reason}."
        elif kind == "stop_move":
            txt = f"{late}🔒 {sym}: move stop to {_px(t.cur_stop)} ({why})."
        elif kind == "exit":
            what = {"TP": "target hit", "SL": "stop hit", "SL_after_+0.9R": "stop hit after it was moved to ~breakeven",
                    "TRAIL_STOP": "trailing stop hit", "TIME_2h": "2-hour time stop — EXIT NOW",
                    "EARLY_CUT_15m": "no progress in 15 min — EXIT NOW"}.get(t.exit_reason, t.exit_reason)
            txt = f"{late}🏁 {sym} {t.side}: {what} at {_px(t.exit)} → {t.net_r:+.2f}R net (paper)."
        else:
            return
        self.send(txt, reply_to=reply)

    def news_result(self, t: Trade, res: dict, mode: str) -> None:
        sym = t.symbol.replace("USDT", "")
        v = res.get("verdict")
        hype = res.get("hype") or {}
        hype_s = (f" | hype: {hype.get('posts_per_hour')} posts/h, {int((hype.get('bot_share') or 0) * 100)}% bot-like"
                  if hype.get("sample") else "")
        if v == "big_news":
            items = "; ".join(f"[{i['source']}] {i['title'][:70]}" for i in res.get("items", [])[:2])
            txt = f"📰 {sym}: BIG NEWS found — {items}{hype_s}"
            txt += "\n→ skipped (enforce)" if mode == "enforce" else "\n→ guide says skip news spikes (shadow mode: bot still trades it)"
        elif v == "no_news":
            txt = f"📰 {sym}: no news found ({res.get('sources_ok')} sources){hype_s}"
        else:
            txt = f"📰 {sym}: news check FAILED ({'; '.join(res.get('errors', [])[:2])})" + \
                  ("\n→ skipped (enforce)" if mode == "enforce" else "")
        self.send(txt, reply_to=t.details.get("tg_msg_id"))

    def watch(self, w: dict, now_ms: int) -> None:
        if self.late(w["bar_close"], now_ms):
            return                                   # a stale "get ready" is useless
        icon = "🟢" if w["side"] == "long" else "🔴"
        self.send(f"👀 GET READY {icon} {w['symbol'].replace('USDT', '')} {w['side'].upper()} — {w['setup']}\n"
                  f"{w['text']}\n(as of {_hhmm(w['bar_close'])} UTC close)")

    def heartbeat_problem(self, text: str) -> None:
        self.send(f"🛑 {text}")
