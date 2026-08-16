"""Futu OpenD research sidecar: analyst consensus + financials + daily tape.

Calls the official futuapi scripts. OpenD must be up at 127.0.0.1:11111.
Never prints credentials. Paper/simulate only; no trade unlock.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCRIPT_ROOT = Path("/home/box/futu-skills/futuapi/scripts/quote")
_CONSENSUS = _SCRIPT_ROOT / "get_research_analyst_consensus.py"
_STATEMENTS = _SCRIPT_ROOT / "get_financials_statements.py"
_CAPITAL_FLOW = _SCRIPT_ROOT / "get_capital_flow.py"
_CAPITAL_DIST = _SCRIPT_ROOT / "get_capital_distribution.py"
_RATING_CHANGE = _SCRIPT_ROOT / "get_rating_change.py"
_EARNINGS_CAL = _SCRIPT_ROOT / "get_earnings_calendar.py"
_PRE_MARKET = _SCRIPT_ROOT / "get_us_pre_market_rank.py"

TAPE_UNIVERSE = frozenset(
    {
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "CBRS",
        "NOK",
        "MU",
        "LITE",
        "SPCX",
        "SPY",
        "QQQ",
        "IWM",
        "SMH",
    }
)

_market_tape_lock = threading.Lock()
_market_tape_cache: Optional[Dict[str, Any]] = None


def to_futu_code(stock_code: str) -> Optional[str]:
    code = (stock_code or "").strip().upper()
    if not code:
        return None
    if code.startswith("US.") or code.startswith("HK."):
        return code
    if code.startswith("HK") and code[2:].isdigit():
        return f"HK.{code[2:].zfill(5)}"
    if code.replace(".", "").isalnum() and not code[0].isdigit():
        return f"US.{code}"
    return None


def _bare_symbol(code: str) -> str:
    text = (code or "").strip().upper()
    if "." in text:
        return text.split(".", 1)[1]
    return text


def _record_symbol(rec: Dict[str, Any]) -> str:
    sec = rec.get("security") or rec.get("code") or rec.get("stock_code") or ""
    if isinstance(sec, dict):
        sec = sec.get("code") or sec.get("symbol") or ""
    return _bare_symbol(str(sec))


def _in_universe(rec: Dict[str, Any], extra: Optional[str] = None) -> bool:
    symbol = _record_symbol(rec)
    if extra and symbol == _bare_symbol(extra):
        return True
    return symbol in TAPE_UNIVERSE


def _parse_json_payload(stdout: str, script_name: str) -> Dict[str, Any]:
    text = stdout or ""
    decoder = json.JSONDecoder()
    last: Optional[Dict[str, Any]] = None
    idx = 0
    while True:
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                last = obj
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    if last is None:
        raise RuntimeError(f"{script_name} returned no JSON")
    if last.get("error") and len(last) == 1:
        raise RuntimeError(f"{script_name} failed: {last['error']}")
    return last


def _run_script(script: Path, args: list[str], timeout: int = 45) -> Dict[str, Any]:
    if not script.exists():
        raise FileNotFoundError(str(script))
    cmd = ["python3", str(script), "--json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"{script.name} failed: {err}")
    return _parse_json_payload(proc.stdout or "", script.name)


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: Any, digits: int = 2) -> str:
    num = _safe_float(value)
    if num is None:
        return "N/A"
    return f"{num:,.{digits}f}"


def get_analyst_consensus(stock_code: str) -> Dict[str, Any]:
    futu_code = to_futu_code(stock_code)
    if not futu_code:
        return {}
    payload = _run_script(_CONSENSUS, [futu_code])
    return payload.get("data") or {}


def get_key_financials(stock_code: str, num: int = 2) -> Dict[str, Any]:
    futu_code = to_futu_code(stock_code)
    if not futu_code:
        return {}
    payload = _run_script(
        _STATEMENTS,
        ["--statement-type", "4", "--financial-type", "10", "--num", str(num), futu_code],
    )
    return payload.get("data") or {}


def get_capital_flow(stock_code: str) -> Dict[str, Any]:
    """Intraday capital flow via official script.

    Official --period-type is an int, but OpenD expects PeriodType
    (INTRADAY/DAY/WEEK/MONTH), so we omit it and use the default INTRADAY
    series. in_flow on that series is a running session total.
    """
    futu_code = to_futu_code(stock_code)
    if not futu_code:
        return {}
    return _run_script(_CAPITAL_FLOW, [futu_code])


def get_capital_distribution(stock_code: str) -> Dict[str, Any]:
    futu_code = to_futu_code(stock_code)
    if not futu_code:
        return {}
    return _run_script(_CAPITAL_DIST, [futu_code])


def get_us_rating_changes() -> Dict[str, Any]:
    out: Dict[str, Any] = {"upgrades": [], "downgrades": [], "new_ratings": []}
    for key, change_type, count in (
        ("upgrades", "UPGRADE", "20"),
        ("downgrades", "DOWNGRADE", "20"),
        ("new_ratings", "NEW_RATING", "10"),
    ):
        try:
            payload = _run_script(
                _RATING_CHANGE,
                ["--market", "US", "--change-type", change_type, "--count", count],
            )
            recs = payload.get("records") or payload.get("data") or []
            if isinstance(recs, list):
                out[key] = [r for r in recs if isinstance(r, dict) and _in_universe(r)]
        except Exception as exc:
            logger.info("[Futu] rating_change(%s) failed: %s", change_type, exc)
            out[f"{key}_error"] = type(exc).__name__
    return out


def get_us_earnings_calendar() -> Dict[str, Any]:
    """US earnings in the next ~14 days. OpenD caps one call at 7 days."""
    start = date.today()
    windows = (
        (start, start + timedelta(days=6)),
        (start + timedelta(days=7), start + timedelta(days=13)),
    )
    recs: List[Dict[str, Any]] = []
    errors: List[str] = []
    for begin, end in windows:
        try:
            payload = _run_script(
                _EARNINGS_CAL,
                [
                    "--market",
                    "US",
                    "--begin-date",
                    begin.isoformat(),
                    "--end-date",
                    end.isoformat(),
                ],
                timeout=60,
            )
            chunk = payload.get("data") or []
            if isinstance(chunk, list):
                recs.extend(r for r in chunk if isinstance(r, dict))
        except Exception as exc:
            logger.info("[Futu] earnings_calendar(%s..%s) failed: %s", begin, end, exc)
            errors.append(type(exc).__name__)
    seen = set()
    filtered: List[Dict[str, Any]] = []
    for rec in recs:
        if not _in_universe(rec):
            continue
        key = (
            _record_symbol(rec),
            str(rec.get("earnings_date") or ""),
            str(rec.get("period_text") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        filtered.append(rec)
    out: Dict[str, Any] = {
        "begin": start.isoformat(),
        "end": (start + timedelta(days=13)).isoformat(),
        "records": filtered,
    }
    if errors and not filtered:
        out["window_errors"] = errors
    return out


def get_us_pre_market_rank() -> Dict[str, Any]:
    payload = _run_script(_PRE_MARKET, ["--count", "30"])
    recs = payload.get("data") or []
    if not isinstance(recs, list):
        recs = []
    return {
        "all_count": payload.get("all_count"),
        "highlighted": [r for r in recs if isinstance(r, dict) and _in_universe(r)],
        "top": [r for r in recs if isinstance(r, dict)][:10],
    }


def get_market_tape() -> Dict[str, Any]:
    """US market-level tape, fetched once per process and reused."""
    global _market_tape_cache
    with _market_tape_lock:
        if _market_tape_cache is not None:
            return _market_tape_cache
        tape: Dict[str, Any] = {}
        try:
            tape["rating_changes"] = get_us_rating_changes()
        except Exception as exc:
            logger.info("[Futu] rating_changes failed: %s", exc)
            tape["rating_changes"] = {}
            tape["rating_changes_error"] = type(exc).__name__
        try:
            tape["earnings_calendar"] = get_us_earnings_calendar()
        except Exception as exc:
            logger.info("[Futu] earnings_calendar failed: %s", exc)
            tape["earnings_calendar"] = {}
            tape["earnings_calendar_error"] = type(exc).__name__
        try:
            tape["pre_market_rank"] = get_us_pre_market_rank()
        except Exception as exc:
            logger.info("[Futu] pre_market_rank failed: %s", exc)
            tape["pre_market_rank"] = {}
            tape["pre_market_rank_error"] = type(exc).__name__
        _market_tape_cache = tape
        return tape


def map_capital_flow_for_analyzer(flow_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map Futu session flow into analyzer stock_flow shape.

    Official daily period_type is not usable (int vs PeriodType enum), so we
    take the last INTRADAY bar. in_flow is a running session total.
    main_net_inflow <- last in_flow (or main_in_flow if present).
    inflow_5d / inflow_10d stay None — not the same as A-share 5/10-day sums.
    Analyzer bias still works from main_net_inflow alone.
    """
    records = flow_payload.get("data") if isinstance(flow_payload, dict) else None
    if not isinstance(records, list) or not records:
        return None

    def _sort_key(rec: Dict[str, Any]) -> str:
        return str(rec.get("capital_flow_item_time") or rec.get("last_valid_time") or "")

    last = None
    for rec in sorted((r for r in records if isinstance(r, dict)), key=_sort_key):
        last = rec
    if not isinstance(last, dict):
        return None
    main = _safe_float(last.get("main_in_flow"))
    inflow = _safe_float(last.get("in_flow"))
    net = main if main is not None else inflow
    if net is None:
        return None
    return {
        "main_net_inflow": net,
        "inflow_5d": None,
        "inflow_10d": None,
        "as_of": str(last.get("capital_flow_item_time") or last.get("last_valid_time") or "") or None,
        "points": 1,
        "session_in_flow": inflow,
        "super_in_flow": _safe_float(last.get("super_in_flow")),
        "big_in_flow": _safe_float(last.get("big_in_flow")),
    }


def apply_capital_flow_to_fundamental(
    fundamental_context: Optional[Dict[str, Any]],
    snap: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Overlay mapped Futu flow onto fundamental_context.capital_flow. Fail-open."""
    mapped = (snap or {}).get("capital_flow_for_analyzer")
    if not isinstance(mapped, dict):
        return fundamental_context
    if all(mapped.get(k) is None for k in ("main_net_inflow", "inflow_5d", "inflow_10d")):
        return fundamental_context
    ctx = dict(fundamental_context) if isinstance(fundamental_context, dict) else {}
    ctx["capital_flow"] = {
        "status": "ok",
        "data": {
            "stock_flow": {
                "main_net_inflow": mapped.get("main_net_inflow"),
                "inflow_5d": mapped.get("inflow_5d"),
                "inflow_10d": mapped.get("inflow_10d"),
            }
        },
        "source": "futu_opend",
    }
    coverage = dict(ctx.get("coverage") or {})
    coverage["capital_flow"] = "ok"
    ctx["coverage"] = coverage
    return ctx


def snapshot(stock_code: str) -> Dict[str, Any]:
    """Consensus + financials + daily tape for one name. Fail-open."""
    out: Dict[str, Any] = {"code": stock_code, "source": "futu_opend"}
    try:
        out["consensus"] = get_analyst_consensus(stock_code)
    except Exception as exc:
        logger.info("[Futu] consensus(%s) failed: %s", stock_code, exc)
        out["consensus"] = {}
        out["consensus_error"] = type(exc).__name__
    try:
        out["financials"] = get_key_financials(stock_code)
    except Exception as exc:
        logger.info("[Futu] financials(%s) failed: %s", stock_code, exc)
        out["financials"] = {}
        out["financials_error"] = type(exc).__name__
    try:
        flow = get_capital_flow(stock_code)
        out["capital_flow"] = flow
        out["capital_flow_for_analyzer"] = map_capital_flow_for_analyzer(flow)
    except Exception as exc:
        logger.info("[Futu] capital_flow(%s) failed: %s", stock_code, exc)
        out["capital_flow"] = {}
        out["capital_flow_error"] = type(exc).__name__
        out["capital_flow_for_analyzer"] = None
    try:
        out["capital_distribution"] = get_capital_distribution(stock_code)
    except Exception as exc:
        logger.info("[Futu] capital_distribution(%s) failed: %s", stock_code, exc)
        out["capital_distribution"] = {}
        out["capital_distribution_error"] = type(exc).__name__

    tape = get_market_tape()
    out["rating_changes"] = tape.get("rating_changes") or {}
    out["earnings_calendar"] = tape.get("earnings_calendar") or {}
    out["pre_market_rank"] = tape.get("pre_market_rank") or {}
    cal = out["earnings_calendar"]
    if isinstance(cal, dict) and cal.get("window_errors") and not cal.get("records"):
        out["earnings_calendar_error"] = ",".join(str(x) for x in cal.get("window_errors") or [])
    for err_key in (
        "rating_changes_error",
        "earnings_calendar_error",
        "pre_market_rank_error",
    ):
        if tape.get(err_key):
            out[err_key] = tape[err_key]
    return out


def _mark_current(symbol: str, current: str) -> str:
    return f"★ {symbol}" if symbol and symbol == _bare_symbol(current) else symbol


def format_prompt_block(snap: Dict[str, Any]) -> str:
    """Structured Futu block for the LLM prompt. Not a search snippet."""
    if not isinstance(snap, dict):
        return ""
    current = str(snap.get("code") or "")
    c = snap.get("consensus") or {}
    lines = [
        "## 权威数据（Futu OpenD，非搜索）",
        "以下目标价、评级、财报来自富途 OpenD 结构化接口。禁止用新闻搜索结果覆盖。",
        "无 10-K / 业绩幻灯 / 具名研报时，写“无权威市占”，禁止编造市场份额。",
    ]
    if c:
        lines += [
            "",
            "### 分析师一致预期（近3个月）",
            f"- 综合评级: {c.get('rating') or 'N/A'}",
            f"- 平均目标价: {c.get('average')}（高 {c.get('highest')} / 低 {c.get('lowest')}）",
            f"- 覆盖人数: {c.get('total')}",
            f"- 买入/持有/卖出占比: {c.get('buy')} / {c.get('hold')} / {c.get('sell')}",
            f"- 更新日期: {c.get('update_time_str') or 'N/A'}",
        ]
    else:
        err = snap.get("consensus_error")
        lines += ["", f"### 分析师一致预期: 不可用{(' ('+err+')') if err else ''}"]

    fin = snap.get("financials") or {}
    reports = fin.get("report_list") if isinstance(fin, dict) else None
    if isinstance(reports, list) and reports:
        latest = reports[0]
        wanted = {
            "Gross Margin",
            "Net Margin",
            "ROE",
            "ROA",
            "Revenue CAGR (3Y)",
            "Net Income CAGR (3Y)",
        }
        picked = []
        for item in latest.get("item_list") or []:
            name = item.get("display_name")
            if name in wanted and item.get("data") is not None:
                picked.append(f"- {name}: {item.get('data')} (YoY {item.get('yoy')})")
        lines += [
            "",
            f"### 关键财务（{latest.get('period_text') or latest.get('date_time_str')} {latest.get('currency_code') or ''}）",
        ]
        lines.extend(picked or ["- 关键比率字段缺失"])

    mapped = snap.get("capital_flow_for_analyzer")
    flow_err = snap.get("capital_flow_error")
    lines += ["", "### 资金流向（Futu OpenD 日内累计）"]
    if isinstance(mapped, dict) and mapped.get("main_net_inflow") is not None:
        lines += [
            f"- 最新主力净流入: {_fmt_num(mapped.get('main_net_inflow'))}",
            f"- 近5日净流入: {_fmt_num(mapped.get('inflow_5d'))}（无官方日频）",
            f"- 近10日净流入: {_fmt_num(mapped.get('inflow_10d'))}（无官方日频）",
            f"- 最新时段: {mapped.get('as_of') or 'N/A'}",
            f"- 特大单/大单净流入: {_fmt_num(mapped.get('super_in_flow'))} / {_fmt_num(mapped.get('big_in_flow'))}",
        ]
    else:
        lines.append(f"- 不可用{(' ('+flow_err+')') if flow_err else ''}")

    dist = snap.get("capital_distribution") or {}
    dist_err = snap.get("capital_distribution_error")
    lines += ["", "### 资金分布（Futu OpenD）"]
    if isinstance(dist, dict) and any(
        dist.get(k) is not None
        for k in (
            "capital_in_super",
            "capital_out_super",
            "capital_in_big",
            "capital_out_big",
        )
    ):
        for label, in_key, out_key in (
            ("特大单", "capital_in_super", "capital_out_super"),
            ("大单", "capital_in_big", "capital_out_big"),
            ("中单", "capital_in_mid", "capital_out_mid"),
            ("小单", "capital_in_small", "capital_out_small"),
        ):
            inn = _safe_float(dist.get(in_key)) or 0.0
            outv = _safe_float(dist.get(out_key)) or 0.0
            lines.append(f"- {label} 流入/流出/净: {_fmt_num(inn)} / {_fmt_num(outv)} / {_fmt_num(inn - outv)}")
    else:
        lines.append(f"- 不可用{(' ('+dist_err+')') if dist_err else ''}")

    ratings = snap.get("rating_changes") or {}
    lines += ["", "### 美股评级变动（宇宙内，Futu OpenD）"]
    if snap.get("rating_changes_error") and not any(
        ratings.get(k) for k in ("upgrades", "downgrades", "new_ratings")
    ):
        lines.append(f"- 不可用 ({snap.get('rating_changes_error')})")
    else:
        shown = False
        for title, key in (
            ("UPGRADE", "upgrades"),
            ("DOWNGRADE", "downgrades"),
            ("NEW_RATING", "new_ratings"),
        ):
            recs = ratings.get(key) or []
            err = ratings.get(f"{key}_error")
            if err and not recs:
                lines.append(f"- {title}: 不可用 ({err})")
                shown = True
                continue
            for rec in recs[:8]:
                symbol = _record_symbol(rec)
                lines.append(
                    "- "
                    f"{title} {_mark_current(symbol, current)} "
                    f"{rec.get('name') or ''} "
                    f"{rec.get('last_rating') or '?'}→{rec.get('rating') or '?'} "
                    f"目标 {rec.get('last_target_price') or '?'}→{rec.get('target_price') or '?'} "
                    f"{rec.get('institution_name') or ''} "
                    f"{rec.get('recommendation_date') or ''}".rstrip()
                )
                shown = True
        if not shown:
            lines.append("- 宇宙内暂无评级变动")

    cal = snap.get("earnings_calendar") or {}
    cal_recs = cal.get("records") if isinstance(cal, dict) else None
    lines += ["", "### 美股财报日历（未来14天，宇宙内）"]
    if snap.get("earnings_calendar_error"):
        lines.append(f"- 不可用 ({snap.get('earnings_calendar_error')})")
    elif isinstance(cal_recs, list) and cal_recs:
        window = ""
        if cal.get("begin") or cal.get("end"):
            window = f"（{cal.get('begin') or '?'} ~ {cal.get('end') or '?'}）"
            lines.append(f"- 窗口{window}")
        for rec in cal_recs[:16]:
            symbol = _record_symbol(rec)
            lines.append(
                "- "
                f"{_mark_current(symbol, current)} {rec.get('name') or ''} "
                f"{rec.get('earnings_date') or rec.get('period_text') or ''} "
                f"{rec.get('pub_type') or ''} "
                f"EPS预期 {rec.get('eps_predict') if rec.get('eps_predict') is not None else 'N/A'} "
                f"营收预期 {rec.get('revenue_predict') if rec.get('revenue_predict') is not None else 'N/A'}".rstrip()
            )
    else:
        lines.append("- 宇宙内未来14天无财报")

    pre = snap.get("pre_market_rank") or {}
    lines += ["", "### 美股盘前榜（宇宙高亮）"]
    if snap.get("pre_market_rank_error"):
        lines.append(f"- 不可用 ({snap.get('pre_market_rank_error')})")
    else:
        highlighted = pre.get("highlighted") if isinstance(pre, dict) else None
        top = pre.get("top") if isinstance(pre, dict) else None
        if isinstance(highlighted, list) and highlighted:
            for rec in highlighted[:16]:
                symbol = _record_symbol(rec)
                lines.append(
                    "- "
                    f"{_mark_current(symbol, current)} {rec.get('name') or ''} "
                    f"盘前 {_fmt_num(rec.get('pre_market_price'))} "
                    f"涨跌 {_fmt_num(rec.get('pre_market_change_ratio'), 4)} "
                    f"额 {_fmt_num(rec.get('pre_market_turnover'))}".rstrip()
                )
        elif isinstance(top, list) and top:
            lines.append("- 宇宙内无名上榜，盘前榜前排：")
            for rec in top[:5]:
                symbol = _record_symbol(rec)
                lines.append(
                    f"- {symbol} {rec.get('name') or ''} "
                    f"盘前 {_fmt_num(rec.get('pre_market_price'))} "
                    f"涨跌 {_fmt_num(rec.get('pre_market_change_ratio'), 4)}"
                )
        else:
            lines.append("- 暂无盘前榜数据")

    return "\n".join(lines).strip() + "\n"
