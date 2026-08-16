"""Futu OpenD research sidecar: analyst consensus + financials.

Calls the official futuapi scripts. OpenD must be up at 127.0.0.1:11111.
Never prints credentials. Paper/simulate only; no trade unlock.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SCRIPT_ROOT = Path("/home/box/futu-skills/futuapi/scripts/quote")
_CONSENSUS = _SCRIPT_ROOT / "get_research_analyst_consensus.py"
_STATEMENTS = _SCRIPT_ROOT / "get_financials_statements.py"


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


def _run_script(script: Path, args: list[str], timeout: int = 45) -> Dict[str, Any]:
    if not script.exists():
        raise FileNotFoundError(str(script))
    cmd = ["python3", str(script), "--json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"{script.name} failed: {err}")
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"{script.name} returned no JSON")
    return json.loads(lines[-1])


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


def snapshot(stock_code: str) -> Dict[str, Any]:
    """Consensus + latest key ratios for one name. Fail-open."""
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
    return out


def format_prompt_block(snap: Dict[str, Any]) -> str:
    """Structured Futu block for the LLM prompt. Not a search snippet."""
    if not isinstance(snap, dict):
        return ""
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
        wanted = {"Gross Margin", "Net Margin", "ROE", "ROA", "Revenue CAGR (3Y)", "Net Income CAGR (3Y)"}
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
    return "\n".join(lines).strip() + "\n"
