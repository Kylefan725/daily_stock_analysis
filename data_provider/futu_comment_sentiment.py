"""Futu community-feed sentiment sidecar.

Retail discussion from the public stock_feed endpoint. Not official consensus.
Fail-open. No LLM. No adanos.org / moomoo fallback.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

STOCK_FEED_URL = "https://ai-news-search.futunn.com/stock_feed"
USER_AGENT = "futunn-comment-sentiment/0.0.2 (Skill)"
DEFAULT_SIZE = 30
MAX_SIZE = 50
INDEX_ETFS = frozenset({"SPY", "QQQ", "IWM", "SMH"})


def _fetch_result(symbol: str, size: int = DEFAULT_SIZE) -> Dict[str, Any]:
    """Internal HTTP result: status ok/empty/error plus mapped posts."""
    keyword = (symbol or "").strip()
    empty = {"status": "empty", "posts": [], "upstream_code": None, "symbol": keyword}
    if not keyword:
        return empty
    try:
        clamped = max(1, min(int(size), MAX_SIZE))
    except (TypeError, ValueError):
        clamped = DEFAULT_SIZE
    params = urlencode({"keyword": keyword, "size": str(clamped)})
    req = Request(
        f"{STOCK_FEED_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urlopen(req, timeout=8) as resp:
            raw = resp.read()
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.info("[Futu community] stock_feed(%s) failed: %s", keyword, exc)
        return {
            "status": "error",
            "posts": [],
            "upstream_code": None,
            "symbol": keyword,
        }
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return {
            "status": "error",
            "posts": [],
            "upstream_code": payload.get("code") if isinstance(payload, dict) else None,
            "symbol": keyword,
        }
    data = payload.get("data")
    if not isinstance(data, list):
        return {**empty, "upstream_code": 0}
    posts: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        posts.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "desc": str(item.get("desc") or ""),
                "publish_time": item.get("publish_time"),
                "url": str(item.get("url") or ""),
            }
        )
    status = "ok" if posts else "empty"
    return {"status": status, "posts": posts, "upstream_code": 0, "symbol": keyword}


def fetch_stock_feed(symbol: str, size: int = DEFAULT_SIZE) -> List[Dict[str, Any]]:
    """GET stock_feed for one symbol. Fail-open to []."""
    return _fetch_result(symbol, size=size)["posts"]


_PURE_BUY = {"买", "买入", "buy", "to the moon", "冲"}
_SPAM_RE = re.compile(
    r"加微信|领取|内幕|优惠|广告|promo code|click here|free money|referral",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "]+",
    flags=re.UNICODE,
)


def _post_text(post: Dict[str, Any]) -> str:
    return f"{post.get('title') or ''} {post.get('desc') or ''}".strip()


def _is_emoji_only(text: str) -> bool:
    stripped = _EMOJI_RE.sub("", text)
    return not stripped.strip()


def _is_low_quality(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if _is_emoji_only(cleaned):
        return True
    normalized = cleaned.lower().rstrip("!！。.?？")
    if normalized in _PURE_BUY:
        return True
    if _SPAM_RE.search(cleaned):
        return True
    if len(cleaned) < 8:
        return True
    return False


def filter_low_quality(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop emoji-only, pure buy, spam/ads, and no-opinion short posts."""
    kept: List[Dict[str, Any]] = []
    for post in posts or []:
        if not isinstance(post, dict):
            continue
        if _is_low_quality(_post_text(post)):
            continue
        kept.append(post)
    return kept


_BULLISH_CUES = (
    "看好",
    "看多",
    "上涨",
    "突破",
    "加仓",
    "反弹",
    "bullish",
    "rebound",
    "breakout",
    "upside",
    "accelerating",
)
_BEARISH_CUES = (
    "看空",
    "下跌",
    "跌破",
    "减仓",
    "不及预期",
    "担心",
    "miss",
    "bearish",
    "drop",
    "softness",
    "lower",
)


def classify_post(text: str) -> str:
    """Deterministic EN+ZH keyword classify. No LLM."""
    blob = (text or "").strip()
    if not blob:
        return "neutral"
    lowered = blob.lower()
    bull = sum(1 for cue in _BULLISH_CUES if cue.lower() in lowered or cue in blob)
    bear = sum(1 for cue in _BEARISH_CUES if cue.lower() in lowered or cue in blob)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


_VALID_LABELS = {"bullish", "bearish", "neutral"}


def aggregate_labels(labels: List[str]) -> Dict[str, Any]:
    """Percentages and mixed/dominant/weak label from a known bag of post labels."""
    cleaned = [item for item in (labels or []) if item in _VALID_LABELS]
    post_count = len(cleaned)
    if post_count == 0:
        return {
            "label": "neutral",
            "bull_pct": 0,
            "bear_pct": 0,
            "neutral_pct": 0,
            "post_count": 0,
        }
    bull = cleaned.count("bullish")
    bear = cleaned.count("bearish")
    bull_pct = (bull * 100) // post_count
    bear_pct = (bear * 100) // post_count
    neutral_pct = 100 - bull_pct - bear_pct
    if abs(bull_pct - bear_pct) < 15 and bull_pct >= 25 and bear_pct >= 25:
        label = "mixed"
    elif post_count < 3:
        label = "neutral"
    elif bull_pct > bear_pct and bull_pct >= neutral_pct:
        label = "bullish"
    elif bear_pct > bull_pct and bear_pct >= neutral_pct:
        label = "bearish"
    else:
        label = "neutral"
    return {
        "label": label,
        "bull_pct": bull_pct,
        "bear_pct": bear_pct,
        "neutral_pct": neutral_pct,
        "post_count": post_count,
    }


DISPLAY_TZ = ZoneInfo("Asia/Hong_Kong")


def _epoch_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts = ts / 1000.0
    return ts


def format_published_at(publish_time: Any) -> str:
    ts = _epoch_seconds(publish_time)
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")


def representative_viewpoints(
    posts: List[Dict[str, Any]],
    limit: int = 3,
) -> List[Dict[str, str]]:
    """1-3 retained opinion posts, most recent first, Asia/Hong_Kong times."""
    opinions: List[Dict[str, Any]] = []
    for post in posts or []:
        if not isinstance(post, dict):
            continue
        text = (post.get("title") or "").strip() or _post_text(post)
        if classify_post(_post_text(post)) not in {"bullish", "bearish"}:
            continue
        opinions.append(
            {
                "text": text,
                "published_at": format_published_at(post.get("publish_time")),
                "_ts": _epoch_seconds(post.get("publish_time")) or 0.0,
            }
        )
    opinions.sort(key=lambda item: item["_ts"], reverse=True)
    out: List[Dict[str, str]] = []
    for item in opinions[: max(0, int(limit))]:
        out.append({"text": item["text"], "published_at": item["published_at"]})
    return out



def _empty_snapshot(symbol: str, status: str) -> Dict[str, Any]:
    return {
        "symbol": (symbol or "").strip().upper() or symbol,
        "status": status,
        "label": "neutral",
        "bull_pct": 0,
        "bear_pct": 0,
        "neutral_pct": 0,
        "post_count": 0,
        "top_opinions": [],
    }


def _bare_symbol(symbol: str) -> str:
    text_value = (symbol or "").strip().upper()
    if text_value.startswith(("US.", "HK.")):
        return text_value.split(".", 1)[1]
    return text_value


def snapshot(symbol: str, size: int = DEFAULT_SIZE) -> Dict[str, Any]:
    """Per-symbol community sentiment. Fail-open to empty/error, never raises."""
    if _bare_symbol(symbol) in INDEX_ETFS:
        return _empty_snapshot(symbol, "skipped")
    try:
        fetched = _fetch_result(symbol, size=size)
    except Exception as exc:
        logger.info("[Futu community] snapshot(%s) failed: %s", symbol, exc)
        return _empty_snapshot(symbol, "error")
    status = fetched.get("status") or "empty"
    raw_posts = fetched.get("posts") or []
    if status == "error":
        return _empty_snapshot(symbol, "error")
    retained = filter_low_quality(raw_posts)
    if not retained:
        return _empty_snapshot(symbol, "empty")
    labels = [classify_post(_post_text(post)) for post in retained]
    agg = aggregate_labels(labels)
    return {
        "symbol": (symbol or "").strip().upper() or symbol,
        "status": "ok",
        "label": agg["label"],
        "bull_pct": agg["bull_pct"],
        "bear_pct": agg["bear_pct"],
        "neutral_pct": agg["neutral_pct"],
        "post_count": agg["post_count"],
        "top_opinions": representative_viewpoints(retained),
    }



def batch_snapshot(symbols: List[str], size: int = DEFAULT_SIZE) -> List[Dict[str, Any]]:
    """One request per symbol. A single failure does not abort the batch."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in symbols or []:
        symbol = (raw or "").strip()
        if not symbol:
            continue
        key = symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        try:
            out.append(snapshot(symbol, size=size))
        except Exception as exc:
            logger.info("[Futu community] batch(%s) failed: %s", symbol, exc)
            out.append(_empty_snapshot(symbol, "error"))
    return out


_LABEL_ZH = {
    "bullish": "看多",
    "bearish": "看空",
    "mixed": "分歧",
    "neutral": "中性",
}


def format_prompt_block(snap: Dict[str, Any]) -> str:
    """Community section only. Never writes official 目标价 / 财报 fields."""
    lines = ["## 社区情绪（Futu，非官方）"]
    if not isinstance(snap, dict) or snap.get("status") != "ok":
        lines.append("- 不可用")
        return "\n".join(lines) + "\n"
    direction = _LABEL_ZH.get(str(snap.get("label") or ""), "中性")
    lines += [
        f"- 方向: {direction}",
        (
            f"- 占比: 看多 {snap.get('bull_pct')}% / "
            f"看空 {snap.get('bear_pct')}% / "
            f"中性 {snap.get('neutral_pct')}%"
        ),
        f"- 帖数: {snap.get('post_count')}",
    ]
    opinions = snap.get("top_opinions") or []
    if opinions:
        lines.append("- 代表性观点:")
        for idx, item in enumerate(opinions[:3], 1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            when = str(item.get("published_at") or "").strip()
            if not text:
                continue
            suffix = f" · {when}" if when else ""
            lines.append(f'  {idx}. "{text}"{suffix}')
    return "\n".join(lines) + "\n"



def is_community_eligible(symbol: str) -> bool:
    """US/HK single-name names. ETFs are handled separately."""
    text_value = (symbol or "").strip()
    if not text_value:
        return False
    upper = text_value.upper()
    if _bare_symbol(upper) in INDEX_ETFS:
        return False
    from data_provider.us_index_mapping import is_us_stock_code
    if is_us_stock_code(upper) or is_us_stock_code(text_value):
        return True
    from data_provider.akshare_fetcher import is_hk_stock_code
    if is_hk_stock_code(text_value) or is_hk_stock_code(upper):
        return True
    return upper.startswith("HK.")
