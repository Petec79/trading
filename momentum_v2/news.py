"""
News check for fast spike reversals (MR guide part 1 p122-125), paper only.

The guide: before fading a fast spike, check whether it was caused by "big
news" — founder/team problem, hack/exploit, big update, partnership, listing
or delisting. News-driven spikes get "excess flow" and are skipped.

This module answers that at signal time, from sources a bot farm can't fake:
  1. Exchange announcements — Bybit and OKX official announcement APIs, and
     Binance's public announcements page. Listings/delistings/hacks notices.
  2. News-site RSS (CoinDesk, The Block, Cointelegraph, Decrypt) by ticker.
  3. X via Apify, restricted to an ALLOWLIST of accounts (exchanges,
     security alerts, established news/alert accounts, the coin's own account).
Plus, recorded but never used to block:
  4. A "hype score" from a sample of recent cashtag posts: volume and the share
     of bot-like accounts — to test later whether bot-hyped, news-free spikes
     fade better (see news_report.py).

Modes (env NEWS_MODE or MRConfig.news_mode):
  shadow  — record the verdict on the trade, never block (default)
  enforce — skip the trade on big news, or if the check itself failed
  off     — don't check

Everything is best-effort: each source has a short timeout, and a failure is
recorded as such rather than crashing the bot.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

CONFIG_FILE = Path(__file__).resolve().parent / "news_sources.json"
UA = {"User-Agent": "Mozilla/5.0 (momentum-v2 news check)"}

# The guide's list of "big news" (P1 p124) as keyword families.
BIG_NEWS = {
    "listing": r"\b(list(s|ed|ing)?|launchpool|launchpad|will (add|list)|new (spot|futures) (pair|listing)|perpetual contract)\b",
    "delisting": r"\b(delist(s|ed|ing)?|will remove|cease trading|monitoring tag)\b",
    "hack": r"\b(hack(ed)?|exploit(ed)?|drain(ed)?|attack(er|ed)?|stolen|compromis(e|ed)|rug ?pull|vulnerabilit(y|ies))\b",
    "team": r"\b(founder|ceo|co-?founder|team|arrest(ed)?|resign(s|ed)?|lawsuit|sued|sec\b|investigation|charged)\b",
    "update": r"\b(mainnet|upgrade|hard ?fork|migration|token swap|v[0-9]+ launch|roadmap|tokenomics|unlock|burn)\b",
    "partnership": r"\b(partner(s|ed|ship)?|collaborat(e|ion)|integrat(e|es|ion)|acquir(e|es|ed|ition)|invest(s|ed|ment)|backed by)\b",
}


@dataclass
class NewsItem:
    source: str
    title: str
    ts: Optional[int]           # ms
    url: str = ""
    author: str = ""
    kinds: list = field(default_factory=list)


def _get_json(url: str, timeout: float = 8.0, data: Optional[dict] = None):
    body = json.dumps(data).encode() if data is not None else None
    hdr = dict(UA)
    if body is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_text(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_sources() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {"rss": [], "x_allowlist": [], "coin_accounts": {}}


def classify(text: str) -> list[str]:
    t = text.lower()
    return [k for k, rx in BIG_NEWS.items() if re.search(rx, t)]


def mentions(text: str, base: str) -> bool:
    """Ticker mentioned as a ticker: $PEPE, (PEPE), #PEPE, PEPE/USDT, PEPEUSDT or the
    bare uppercase word. Tickers shorter than 3 letters need the $/()/# form."""
    b = re.escape(base.upper())
    strong = re.search(rf"(\${b}\b|\({b}\)|#{b}\b|\b{b}/USDT?\b|\b{b}USDT\b)", text, re.I)
    if strong:
        return True
    return len(base) >= 3 and re.search(rf"(?<![A-Za-z0-9]){b}(?![A-Za-z0-9])", text) is not None


# ── 1. exchange announcements ────────────────────────────────────────────────
def bybit_announcements() -> list[NewsItem]:
    d = _get_json("https://api.bybit.com/v5/announcements/index?locale=en-US&limit=50")
    out = []
    for a in (d.get("result") or {}).get("list", []):
        out.append(NewsItem("bybit", a.get("title", ""), int(a.get("dateTimestamp") or a.get("publishTime") or 0) or None,
                            a.get("url", "")))
    return out


def okx_announcements() -> list[NewsItem]:
    d = _get_json("https://www.okx.com/api/v5/support/announcements")
    out = []
    for page in d.get("data", []):
        for a in page.get("details", []):
            out.append(NewsItem("okx", a.get("title", ""), int(a.get("pTime") or 0) or None, a.get("url", "")))
    return out


def binance_announcements() -> list[NewsItem]:
    # Binance's public announcements page data (unofficial, no key).
    d = _get_json("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                  "?type=1&pageNo=1&pageSize=50")
    out = []
    for cat in (d.get("data") or {}).get("catalogs", []):
        for a in cat.get("articles", []):
            code = a.get("code", "")
            out.append(NewsItem("binance", a.get("title", ""), int(a.get("releaseDate") or 0) or None,
                                f"https://www.binance.com/en/support/announcement/{code}" if code else ""))
    return out


# ── 2. RSS ───────────────────────────────────────────────────────────────────
def rss_items(url: str) -> list[NewsItem]:
    root = ET.fromstring(_get_text(url))
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")[:400]
        ts = None
        pd = it.findtext("pubDate")
        if pd:
            try:
                ts = int(parsedate_to_datetime(pd).timestamp() * 1000)
            except Exception:
                ts = None
        out.append(NewsItem(urllib.parse.urlparse(url).netloc, f"{title} — {desc}".strip(" —"), ts,
                            it.findtext("link") or ""))
    return out


# ── 3/4. X via Apify ─────────────────────────────────────────────────────────
def apify_search(query: str, max_items: int, actor: str, token: str, timeout: int = 45) -> list[dict]:
    url = (f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
           f"?token={urllib.parse.quote(token)}&timeout={timeout}")
    data = {"searchTerms": [query], "sort": "Latest", "maxItems": max_items}
    res = _get_json(url, timeout=timeout + 15, data=data)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(res["error"].get("message", str(res["error"])))
    return [r for r in res if isinstance(r, dict) and (r.get("text") or r.get("fullText"))]


def _tweet_ts(tw: dict) -> Optional[int]:
    s = tw.get("createdAt") or tw.get("created_at")
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(datetime.strptime(s.replace("Z", "+0000"), fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def bot_like(tw: dict) -> bool:
    """Heuristics for low-quality / bot-farm posts. Deliberately simple."""
    a = tw.get("author") or {}
    text = tw.get("text") or tw.get("fullText") or ""
    followers = a.get("followers") or a.get("followersCount") or 0
    following = a.get("following") or a.get("followingCount") or 0
    cashtags = len(re.findall(r"\$[A-Za-z]{2,10}\b", text))
    signs = [
        followers < 100,
        following > 5 * max(followers, 1) and following > 500,
        cashtags >= 4,
        bool(re.search(r"t\.me/|telegram", text, re.I)),
        bool(re.search(r"\b(100x|1000x|gem|moon(ing)?|pump(ing)?|airdrop|giveaway)\b", text, re.I)) and followers < 1000,
    ]
    return sum(signs) >= 2


def hype_score(tweets: list[dict], now_ms: int) -> dict:
    ts = [t for t in (_tweet_ts(x) for x in tweets) if t]
    if not ts:
        return {"sample": len(tweets), "posts_per_hour": None, "bot_share": None, "dup_share": None}
    span_h = max((now_ms - min(ts)) / 3_600_000, 1 / 60)
    texts = [re.sub(r"\W+", " ", (t.get("text") or t.get("fullText") or "").lower()).strip()[:80] for t in tweets]
    dups = len(texts) - len(set(texts))
    return {"sample": len(tweets), "posts_per_hour": round(len(ts) / span_h, 1),
            "bot_share": round(sum(bot_like(t) for t in tweets) / len(tweets), 2),
            "dup_share": round(dups / len(tweets), 2)}


# ── the check ────────────────────────────────────────────────────────────────
class NewsChecker:
    def __init__(self, lookback_min: int = 120, cache_s: int = 120):
        self.lookback_ms = lookback_min * 60_000
        self.cache_s = cache_s
        self._cache: dict[str, tuple[float, list]] = {}
        self.sources = load_sources()

    def _cached(self, key, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_s:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def check(self, symbol: str, now_ms: int, spike_start_ms: Optional[int] = None) -> dict:
        """Returns {"verdict": "big_news"|"no_news"|"check_failed", "items": [...], "hype": {...},
        "errors": [...]}. Items are big-news hits about this coin in the lookback window.
        All sources are fetched in parallel, so the check takes about as long as the slowest one."""
        from concurrent.futures import ThreadPoolExecutor
        base = re.sub(r"^1000+", "", symbol.upper().replace("USDT", ""))
        since = (spike_start_ms or now_ms) - self.lookback_ms
        token = os.environ.get("APIFY_TOKEN")

        jobs = {"bybit": bybit_announcements, "okx": okx_announcements, "binance": binance_announcements}
        for u in self.sources.get("rss", []):
            jobs[u] = (lambda u=u: rss_items(u))
        if token:
            allow = list(self.sources.get("x_allowlist", []))
            own = self.sources.get("coin_accounts", {}).get(base)
            if own:
                allow.append(own)
            day = datetime.fromtimestamp(since / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if allow:
                q = f"(${base} OR #{base} OR {base}) ({' OR '.join('from:' + a for a in allow)}) since:{day}"
                small = self.sources.get("apify_actor_small", "apidojo~twitter-scraper-lite")
                jobs["x_allowlist"] = lambda: apify_search(q, 20, small, token)
            big = self.sources.get("apify_actor_sample", "apidojo~tweet-scraper")
            jobs["x_sample"] = lambda: apify_search(f"${base} -filter:retweets", 50, big, token)

        results, errors = {}, []
        cacheable = {"bybit", "okx", "binance", *self.sources.get("rss", [])}
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futs = {name: pool.submit(self._cached, name, fn) if name in cacheable else pool.submit(fn)
                    for name, fn in jobs.items()}
            for name, f in futs.items():
                try:
                    results[name] = f.result()
                except Exception as e:
                    errors.append(f"{name}: {str(e)[:120]}")
        if not token:
            errors.append("x: APIFY_TOKEN not set")

        hits = []
        for name, items in results.items():
            if name.startswith("x_"):
                continue
            for it in items:
                if it.ts is None or it.ts < since:
                    continue
                if mentions(it.title, base):
                    it.kinds = classify(it.title)
                    if it.kinds:
                        hits.append(it)
        for tw in results.get("x_allowlist", []):
            t = _tweet_ts(tw)
            text = tw.get("text") or tw.get("fullText") or ""
            if t is None or t < since:
                continue
            kinds = classify(text)
            if kinds:
                hits.append(NewsItem("x", text[:200], t, tw.get("url", ""),
                                     (tw.get("author") or {}).get("userName", ""), kinds))
        hype = hype_score(results["x_sample"], now_ms) if "x_sample" in results else None
        ok_sources = sum(1 for k in results if k != "x_sample")

        if hits:
            verdict = "big_news"
        elif ok_sources == 0:
            verdict = "check_failed"
        else:
            verdict = "no_news"
        return {"verdict": verdict,
                "items": [{"source": h.source, "title": h.title[:160], "kinds": h.kinds,
                           "ts": h.ts, "url": h.url, "author": h.author} for h in hits[:8]],
                "hype": hype, "sources_ok": ok_sources, "errors": errors[:8]}


def blocks(result: dict, mode: str) -> bool:
    """enforce: block on big news, and on a failed check (never let a dead feed through)."""
    if mode != "enforce":
        return False
    return result["verdict"] in ("big_news", "check_failed")
