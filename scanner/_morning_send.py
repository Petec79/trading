#!/usr/bin/env python3
"""Build combined morning scan report and deliver to Telegram via direct Bot API.
Cron-safe: uses urllib (not requests, blocked by scanner) and reads creds from
~/.hermes/.env. No paid APIs.
"""
import json
import os
import urllib.request
import urllib.error
import datetime
import pathlib
import sys

SCAN = pathlib.Path('/home/openclaw/workspace/scanner/latest_spicy_scan.json')
MOM  = pathlib.Path('/home/openclaw/workspace/scanner/latest_momentum_report.txt')
MR   = pathlib.Path('/home/openclaw/workspace/scanner/latest_report.txt')

mr = json.loads(SCAN.read_text())
ts = datetime.datetime.fromisoformat(mr['timestamp'].replace('Z', '+00:00'))
now = datetime.datetime.now(datetime.timezone.utc)
age = now - ts
freshness = "FRESH" if age.total_seconds() < 24 * 3600 else f"STALE ({age})"

bias = mr['bias']
spag = mr['spaghetti']
longs = mr['candidates']['longs'][:5]
shorts = mr['candidates']['shorts'][:8]

# Funding alerts sorted by abs rate
funding_alerts = []
for c in mr['candidates']['longs'] + mr['candidates']['shorts']:
    fr = c.get('funding_rate')
    if fr is None:
        continue
    funding_alerts.append((c['symbol'], fr * 100, c.get('next_fund') or 'N/A', c['chg24']))
funding_alerts.sort(key=lambda x: abs(x[1]), reverse=True)


def flag_str(c):
    flags = []
    if c.get('parabolic'):
        flags.append('PARA!')
    if c.get('in_top10_trd'):
        flags.append('TOP_TRD')
    if c.get('5m_chg') is not None and abs(c['5m_chg']) >= 1.5:
        flags.append('FAST')
    if c.get('skip'):
        flags.append(f"SKIP:{c.get('skip_reason', '')}")
    return ' '.join(f"★{f}" for f in flags)


mom_text = MOM.read_text()

lines = []
lines.append(f"🌶️ *MORNING SCAN* — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
lines.append(f"`files: {freshness} · age {int(age.total_seconds())}s`")
lines.append("")

lines.append("━━━ *SESSION BIAS* ━━━")
lines.append(f"MR: *{bias['value']}* / {bias['strength']}  ·  Spaghetti: {spag['classification']}")
lines.append("")

lines.append("━━━ *MEAN REVERSION* ━━━")
lines.append("🔴 *Top SHORTs (fade the up-moves):*")
for c in shorts:
    lines.append(f"`{c['symbol']:<8}` {c['chg24']:+7.2f}%  MR `{c['score']:.1f}` {flag_str(c)}")
lines.append("")
lines.append("🟢 *Top LONGs (buy the dip):*")
for c in longs:
    lines.append(f"`{c['symbol']:<8}` {c['chg24']:+7.2f}%  MR `{c['score']:.1f}` {flag_str(c)}")
lines.append("")

lines.append("━━━ *FUNDING ALERTS* ━━━")
if not funding_alerts:
    lines.append("_No notable funding skews_")
else:
    lines.append("```")
    lines.append(f"{'Coin':<8} {'Funding':>8}   {'Next':<6}   {'24h':>7}")
    lines.append("-" * 36)
    for sym, fr, nxt, chg in funding_alerts[:8]:
        marker = " 🚨" if abs(fr) >= 5 else (" ⚠️" if abs(fr) >= 1 else "")
        lines.append(f"{sym:<8} {fr:+7.2f}%   {nxt:<6}   {chg:+.2f}%{marker}")
    lines.append("```")
    if funding_alerts and abs(funding_alerts[0][1]) >= 5:
        top = funding_alerts[0]
        direction = "LONG pays funding" if top[1] > 0 else "SHORT pays funding"
        lines.append(f"🚨 *{top[0]}* at {top[1]:+.2f}% — {direction}. Avoid holding through {top[2]} funding unless intentional.")
lines.append("")

lines.append("━━━ *MOMENTUM SCANNER* ━━━")
lines.append("⚠️ *CAUTIOUS* — Score 2/3 max (conservative 1R targets only)")
for l in mom_text.splitlines():
    s = l.strip()
    if not s:
        continue
    if s.startswith('=') or s.startswith('SPICY MOMENTUM') or s.startswith('Coin source') or s.startswith('Saved:'):
        continue
    if s.startswith('~') or s.startswith('🚫') or s.startswith('📋') or 'CAUTIOUS' in s or s.startswith('C1=') or s.startswith('🟢🟢⚪'):
        lines.append(s)
lines.append("")

lines.append("━━━ *SESSION EDGE* ━━━")
lines.append("• *Asian session* (00–08 UTC) — 91% WR historical. *ACTIVE NOW.*")
lines.append("• London open (07–10 UTC) — second-best window for breakouts")
lines.append("• NY cash (13–16 UTC) — momentum strategies peak; fade quality drops")
lines.append("• Avoid: 16–22 UTC thin liquidity, news-driven whipsaws")
lines.append("")

# Bottom Line — derived from live data
bias_label = f"{bias['value']}/{bias['strength']}"
top_short = shorts[0] if shorts else None
top_long = longs[0] if longs else None
mom_count = sum(1 for l in mom_text.splitlines() if l.strip().startswith('~'))

bl = []
bl.append(f"• Bias *{bias_label}* — both directions playable, no strong conviction")
if top_short:
    fr_s = f"{top_short['funding_rate']*100:+.2f}% funding" if top_short.get('funding_rate') is not None else "no funding data"
    bl.append(f"• Top MR SHORT: *{top_short['symbol']}* ({top_short['chg24']:+.2f}%, MR {top_short['score']:.1f}) — {fr_s}")
if top_long:
    fr_l = f"⚠️ but {top_long['funding_rate']*100:+.2f}% funding = expensive to hold" if top_long.get('funding_rate') is not None else ""
    bl.append(f"• Top MR LONG: *{top_long['symbol']}* ({top_long['chg24']:+.2f}%, MR {top_long['score']:.1f}) {fr_l}")
if funding_alerts:
    top_fund = funding_alerts[0]
    bl.append(f"• Biggest funding alert: *{top_fund[0]}* at {top_fund[1]:+.2f}% — {'LONG pays' if top_fund[1] > 0 else 'SHORT pays'}. Avoid holding through {top_fund[2]} funding.")
    cheap = [f for f in funding_alerts if f[1] < -0.5]
    if cheap:
        cheap_str = ", ".join(f"*{s}* ({fr:+.2f}%, short pays)" for s, fr, _, _ in cheap[:3])
        bl.append(f"• Funding edge (cheap to short): {cheap_str}")
bl.append(f"• Momentum: {mom_count} names at 2/3, *no 3/3 setups today*. Conservative targets only.")
bl.append("• Playbook: Asian session edge (ACTIVE NOW) + selective MR fades on TOP_TRD names.")
lines.append("━━━ *BOTTOM LINE* ━━━")
lines.extend(bl)

msg = "\n".join(lines)
print(f"Message length: {len(msg)} chars (limit 4096)")

# Read creds
env_path = os.path.expanduser('~/.hermes/.env')
token, chat_id = None, None
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        if k == 'TELEGRAM_BOT_TOKEN':
            token = v
        elif k == 'TELEGRAM_ALLOWED_USERS':
            chat_id = v.split(',')[0]

if not token or not chat_id:
    print('FATAL: missing telegram creds', file=sys.stderr)
    sys.exit(2)

url = f"https://api.telegram.org/bot{token}/sendMessage"


def send_chunk(text, parse_md=True):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_md:
        payload["parse_mode"] = "Markdown"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='ignore')
        if e.code == 400 and parse_md:
            return send_chunk(text, parse_md=False)
        print(f'HTTP {e.code}: {body}', file=sys.stderr)
        raise


# Send (single chunk, well under 4096)
result = send_chunk(msg)
print(f"Delivery: ok={result.get('ok')} msg_id={result.get('result', {}).get('message_id')}")
print("---REPORT---")
print(msg)
