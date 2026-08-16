# -*- coding: utf-8 -*-
"""Characterization tests for the Futu daily-tape research sidecar.

Public seams only. Subprocess / official-script JSON is mocked at the
process boundary. No live OpenD (127.0.0.1:11111) and no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from datetime import date

import data_provider.futu_research as futu_research

from data_provider.futu_research import (
    TAPE_UNIVERSE,
    apply_capital_flow_to_fundamental,
    format_prompt_block,
    get_us_earnings_calendar,
    get_us_pre_market_rank,
    get_us_rating_changes,
    map_capital_flow_for_analyzer,
    snapshot,
    to_futu_code,
)


def _proc(payload, returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr="")


def test_to_futu_code_maps_us_and_hk_tickers() -> None:
    assert to_futu_code("NVDA") == "US.NVDA"
    assert to_futu_code("us.nvda") == "US.NVDA"
    assert to_futu_code("US.AAPL") == "US.AAPL"
    assert to_futu_code("HK.00700") == "HK.00700"
    assert to_futu_code("HK00700") == "HK.00700"
    assert to_futu_code("HK700") == "HK.00700"
    assert to_futu_code("") is None
    assert to_futu_code("600519") is None


def test_universe_filter_drops_names_outside_watch_universe() -> None:
    assert "NVDA" in TAPE_UNIVERSE
    assert "AAPL" in TAPE_UNIVERSE
    assert "AMD" not in TAPE_UNIVERSE

    def fake_run(cmd, **_kwargs):
        return _proc(
            {
                "records": [
                    {"security": "US.NVDA", "name": "NVIDIA", "rating": "Buy"},
                    {"security": "US.AMD", "name": "AMD", "rating": "Buy"},
                    {"security": "US.AAPL", "name": "Apple", "rating": "Hold"},
                ]
            }
        )

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run):
        out = get_us_rating_changes()

    kept_names = [rec["name"] for rec in out["upgrades"]]
    assert kept_names == ["NVIDIA", "Apple"]
    assert "AMD" not in kept_names


def test_earnings_calendar_fetches_two_seven_day_windows() -> None:
    """OpenD caps one call at 7 days, so the next ~14 days is two windows."""
    captured = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        begin = cmd[cmd.index("--begin-date") + 1]
        if begin == "2026-08-17":
            return _proc(
                {
                    "data": [
                        {
                            "security": "US.NVDA",
                            "name": "NVIDIA",
                            "earnings_date": "2026-08-20",
                            "period_text": "Q2",
                        },
                        {
                            "security": "US.AMD",
                            "name": "AMD",
                            "earnings_date": "2026-08-19",
                            "period_text": "Q2",
                        },
                    ]
                }
            )
        return _proc(
            {
                "data": [
                    {
                        "security": "US.AAPL",
                        "name": "Apple",
                        "earnings_date": "2026-08-28",
                        "period_text": "Q3",
                    }
                ]
            }
        )

    frozen = date(2026, 8, 17)
    with patch("data_provider.futu_research.date") as mock_date, patch(
        "data_provider.futu_research.subprocess.run", side_effect=fake_run
    ):
        mock_date.today.return_value = frozen
        out = get_us_earnings_calendar()

    calendar_cmds = [cmd for cmd in captured if "get_earnings_calendar.py" in cmd[1]]
    assert len(calendar_cmds) == 2
    windows = [
        (cmd[cmd.index("--begin-date") + 1], cmd[cmd.index("--end-date") + 1])
        for cmd in calendar_cmds
    ]
    assert windows == [("2026-08-17", "2026-08-23"), ("2026-08-24", "2026-08-30")]
    assert all("--market" in cmd and "US" in cmd for cmd in calendar_cmds)
    assert out["begin"] == "2026-08-17"
    assert out["end"] == "2026-08-30"
    assert [rec["name"] for rec in out["records"]] == ["NVIDIA", "Apple"]


def test_rating_changes_are_universe_filtered_and_first_page_only() -> None:
    captured = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        change_type = cmd[cmd.index("--change-type") + 1]
        page = {
            "UPGRADE": [
                {"security": "US.NVDA", "name": "NVIDIA", "rating": "Buy"},
                {"security": "US.SNOW", "name": "Snowflake", "rating": "Buy"},
            ],
            "DOWNGRADE": [
                {"security": "US.TSLA", "name": "Tesla", "rating": "Hold"},
                {"security": "US.AMD", "name": "AMD", "rating": "Sell"},
            ],
            "NEW_RATING": [
                {"security": "US.META", "name": "Meta", "rating": "Buy"},
            ],
        }[change_type]
        return _proc({"records": page})

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run):
        out = get_us_rating_changes()

    rating_cmds = [cmd for cmd in captured if "get_rating_change.py" in cmd[1]]
    assert len(rating_cmds) == 3
    by_type = {
        cmd[cmd.index("--change-type") + 1]: cmd[cmd.index("--count") + 1]
        for cmd in rating_cmds
    }
    assert by_type == {"UPGRADE": "20", "DOWNGRADE": "20", "NEW_RATING": "10"}
    assert all("--market" in cmd and "US" in cmd for cmd in rating_cmds)
    assert all("--all-pages" not in cmd and "--page" not in cmd for cmd in rating_cmds)

    assert [rec["name"] for rec in out["upgrades"]] == ["NVIDIA"]
    assert [rec["name"] for rec in out["downgrades"]] == ["Tesla"]
    assert [rec["name"] for rec in out["new_ratings"]] == ["Meta"]


def test_pre_market_rank_maps_universe_hits_into_highlight_payload() -> None:
    recs = [
        {"security": "US.AMD", "name": "AMD", "pre_market_price": 120.0, "pre_market_change_ratio": 0.04},
        {"security": "US.NVDA", "name": "NVIDIA", "pre_market_price": 180.5, "pre_market_change_ratio": 0.0123},
        {"security": "US.SNOW", "name": "Snowflake", "pre_market_price": 90.0, "pre_market_change_ratio": 0.02},
        {"security": "US.TSLA", "name": "Tesla", "pre_market_price": 250.0, "pre_market_change_ratio": -0.01},
    ]

    def fake_run(cmd, **_kwargs):
        assert "get_us_pre_market_rank.py" in cmd[1]
        assert cmd[cmd.index("--count") + 1] == "30"
        return _proc({"all_count": 88, "data": recs})

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run):
        out = get_us_pre_market_rank()

    assert out["all_count"] == 88
    assert [rec["name"] for rec in out["highlighted"]] == ["NVIDIA", "Tesla"]
    assert [rec["name"] for rec in out["top"]] == ["AMD", "NVIDIA", "Snowflake", "Tesla"]


def test_map_capital_flow_uses_last_intraday_in_flow_only() -> None:
    payload = {
        "code": "US.NVDA",
        "data": [
            {
                "capital_flow_item_time": "2026-08-17 09:35:00",
                "in_flow": 100.0,
                "super_in_flow": 10.0,
                "big_in_flow": 20.0,
            },
            {
                "capital_flow_item_time": "2026-08-17 15:55:00",
                "in_flow": 1234567.89,
                "super_in_flow": 400.0,
                "big_in_flow": 250.0,
            },
            {
                "capital_flow_item_time": "2026-08-17 10:00:00",
                "in_flow": 500.0,
                "super_in_flow": 30.0,
                "big_in_flow": 40.0,
            },
        ],
    }

    mapped = map_capital_flow_for_analyzer(payload)

    assert mapped["main_net_inflow"] == 1234567.89
    assert mapped["inflow_5d"] is None
    assert mapped["inflow_10d"] is None
    assert mapped["as_of"] == "2026-08-17 15:55:00"
    assert mapped["session_in_flow"] == 1234567.89
    assert mapped["super_in_flow"] == 400.0
    assert mapped["big_in_flow"] == 250.0


def test_apply_capital_flow_overlays_mapped_flow_onto_fundamental() -> None:
    snap = {
        "capital_flow_for_analyzer": {
            "main_net_inflow": 1234567.89,
            "inflow_5d": None,
            "inflow_10d": None,
        }
    }
    fundamental = {
        "market": "us",
        "status": "ok",
        "coverage": {"valuation": "ok"},
    }

    out = apply_capital_flow_to_fundamental(fundamental, snap)

    assert out["capital_flow"] == {
        "status": "ok",
        "data": {
            "stock_flow": {
                "main_net_inflow": 1234567.89,
                "inflow_5d": None,
                "inflow_10d": None,
            }
        },
        "source": "futu_opend",
    }
    assert out["coverage"]["capital_flow"] == "ok"
    assert out["coverage"]["valuation"] == "ok"
    assert fundamental.get("capital_flow") is None


def test_format_prompt_block_emits_known_section_fragments() -> None:
    snap = {
        "code": "NVDA",
        "consensus": {
            "rating": "Buy",
            "average": 200.0,
            "highest": 220.0,
            "lowest": 180.0,
            "total": 42,
            "buy": 0.7,
            "hold": 0.2,
            "sell": 0.1,
            "update_time_str": "2026-08-15",
        },
        "financials": {
            "report_list": [
                {
                    "period_text": "FY2025",
                    "currency_code": "USD",
                    "item_list": [
                        {"display_name": "Gross Margin", "data": 0.75, "yoy": 0.02},
                        {"display_name": "ROE", "data": 0.55, "yoy": 0.01},
                    ],
                }
            ]
        },
        "capital_flow_for_analyzer": {
            "main_net_inflow": 1234567.89,
            "inflow_5d": None,
            "inflow_10d": None,
            "as_of": "2026-08-17 15:55:00",
            "super_in_flow": 400.0,
            "big_in_flow": 250.0,
        },
        "capital_distribution": {
            "capital_in_super": 100.0,
            "capital_out_super": 40.0,
            "capital_in_big": 80.0,
            "capital_out_big": 30.0,
            "capital_in_mid": 20.0,
            "capital_out_mid": 10.0,
            "capital_in_small": 5.0,
            "capital_out_small": 2.0,
        },
        "rating_changes": {
            "upgrades": [
                {
                    "security": "US.NVDA",
                    "name": "NVIDIA",
                    "last_rating": "Hold",
                    "rating": "Buy",
                    "last_target_price": 180,
                    "target_price": 200,
                    "institution_name": "Goldman",
                    "recommendation_date": "2026-08-15",
                }
            ]
        },
        "earnings_calendar": {
            "begin": "2026-08-17",
            "end": "2026-08-30",
            "records": [
                {
                    "security": "US.NVDA",
                    "name": "NVIDIA",
                    "earnings_date": "2026-08-20",
                    "pub_type": "AMC",
                    "eps_predict": 0.81,
                    "revenue_predict": 46000000000,
                }
            ],
        },
        "pre_market_rank": {
            "highlighted": [
                {
                    "security": "US.NVDA",
                    "name": "NVIDIA",
                    "pre_market_price": 180.5,
                    "pre_market_change_ratio": 0.0123,
                    "pre_market_turnover": 1000000,
                }
            ]
        },
    }

    block = format_prompt_block(snap)

    assert "## 权威数据（Futu OpenD，非搜索）" in block
    assert "### 分析师一致预期（近3个月）" in block
    assert "- 综合评级: Buy" in block
    assert "- 平均目标价: 200.0（高 220.0 / 低 180.0）" in block
    assert "- 覆盖人数: 42" in block
    assert "- 买入/持有/卖出占比: 0.7 / 0.2 / 0.1" in block
    assert "### 关键财务（FY2025 USD）" in block
    assert "- Gross Margin: 0.75 (YoY 0.02)" in block
    assert "- ROE: 0.55 (YoY 0.01)" in block
    assert "### 资金流向（Futu OpenD 日内累计）" in block
    assert "- 最新主力净流入: 1,234,567.89" in block
    assert "- 近5日净流入: N/A（无官方日频）" in block
    assert "- 近10日净流入: N/A（无官方日频）" in block
    assert "- 最新时段: 2026-08-17 15:55:00" in block
    assert "- 特大单/大单净流入: 400.00 / 250.00" in block
    assert "### 资金分布（Futu OpenD）" in block
    assert "- 特大单 流入/流出/净: 100.00 / 40.00 / 60.00" in block
    assert "### 美股评级变动（宇宙内，Futu OpenD）" in block
    assert "- UPGRADE ★ NVDA NVIDIA Hold→Buy 目标 180→200 Goldman 2026-08-15" in block
    assert "### 美股财报日历（未来14天，宇宙内）" in block
    assert "- 窗口（2026-08-17 ~ 2026-08-30）" in block
    assert "- ★ NVDA NVIDIA 2026-08-20 AMC EPS预期 0.81 营收预期 46000000000" in block
    assert "### 美股盘前榜（宇宙高亮）" in block
    assert "- ★ NVDA NVIDIA 盘前 180.50 涨跌 0.0123 额 1,000,000.00" in block


def test_snapshot_fail_open_on_subprocess_failure() -> None:
    def fake_run(cmd, **_kwargs):
        raise RuntimeError("opend down")

    futu_research._market_tape_cache = None
    try:
        with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run):
            snap = snapshot("NVDA")
    finally:
        futu_research._market_tape_cache = None

    assert snap["code"] == "NVDA"
    assert snap["source"] == "futu_opend"
    assert snap["consensus"] == {}
    assert snap["consensus_error"] == "RuntimeError"
    assert snap["financials"] == {}
    assert snap["capital_flow"] == {}
    assert snap["capital_flow_error"] == "RuntimeError"
    assert snap["capital_flow_for_analyzer"] is None
    assert snap["capital_distribution"] == {}
    assert snap["rating_changes"]["upgrades"] == []
    assert snap["rating_changes"]["downgrades"] == []
    assert snap["rating_changes"]["new_ratings"] == []
    assert snap["earnings_calendar"]["records"] == []
    assert snap["pre_market_rank"] == {}
    assert snap["pre_market_rank_error"] == "RuntimeError"
