# -*- coding: utf-8 -*-
"""Pipeline injection of the Futu daily tape (capital-flow overlay + news_context)."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

import pytest

import data_provider.futu_research as futu_research
from data_provider.futu_research import format_prompt_block
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.search_service import SearchResponse, SearchService


@pytest.fixture(autouse=True)
def _clear_search_news_cache():
    futu_research._search_news_cache.clear()
    yield
    futu_research._search_news_cache.clear()


FUTU_SNAP = {
    "code": "NVDA",
    "source": "futu_opend",
    "consensus": {"rating": "Buy", "average": 200.0, "highest": 220.0, "lowest": 180.0, "total": 42, "buy": 0.7, "hold": 0.2, "sell": 0.1},
    "capital_flow_for_analyzer": {
        "main_net_inflow": 1234567.89,
        "inflow_5d": None,
        "inflow_10d": None,
        "as_of": "2026-08-17 15:55:00",
        "super_in_flow": 400.0,
        "big_in_flow": 250.0,
    },
    "rating_changes": {},
    "earnings_calendar": {},
    "pre_market_rank": {},
}


def _build_pipeline(*, search_available: bool) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=False,
        market_review_enabled=False,
        daily_market_context_enabled=False,
        report_language="zh",
        agent_mode=False,
        agent_skills=[],
        save_context_snapshot=False,
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
        litellm_model="test-model",
    )
    pipeline.query_source = "system"
    pipeline.analysis_phase = "auto"
    pipeline.analysis_skills = None
    pipeline.portfolio_context = None
    pipeline.save_context_snapshot = False
    pipeline.daily_market_context_enabled = False
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "NVIDIA"
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.get_fundamental_context.return_value = {
        "market": "us",
        "status": "ok",
        "coverage": {},
    }
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
        "market": "us",
        "status": "failed",
        "coverage": {},
    }
    pipeline.db = MagicMock()
    pipeline.db.get_analysis_context.return_value = {
        "code": "NVDA",
        "stock_name": "NVIDIA",
        "today": {},
        "yesterday": {},
    }
    pipeline.db.get_data_range.return_value = []
    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    result = MagicMock()
    result.success = True
    result.operation_advice = "持有"
    result.report_language = "zh"
    pipeline.analyzer.analyze.return_value = result
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = search_available
    pipeline.search_service.news_window_days = 3
    pipeline.search_service.search_comprehensive_intel.return_value = {
        "news": SimpleNamespace(success=True, results=["headline"], query="nvda news")
    }
    pipeline.search_service.format_intel_report.return_value = "SEARCH INTEL BLOCK"
    pipeline.social_sentiment_service = None
    pipeline._emit_progress = MagicMock()
    pipeline._load_persisted_intelligence_context = MagicMock(return_value=None)
    pipeline._build_market_structure_context = MagicMock(return_value=None)
    pipeline._refresh_decision_action_for_final_result = MagicMock()
    pipeline._extract_decision_signal_after_history_save = MagicMock()
    return pipeline


def test_analyze_stock_overlays_futu_capital_flow_onto_fundamental() -> None:
    pipeline = _build_pipeline(search_available=False)

    with patch("data_provider.futu_research.snapshot", return_value=FUTU_SNAP):
        result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-futu-flow")

    assert result is not None
    enhanced = pipeline.analyzer.analyze.call_args.args[0]
    flow = enhanced["fundamental_context"]["capital_flow"]
    assert flow["source"] == "futu_opend"
    assert flow["status"] == "ok"
    assert flow["data"]["stock_flow"]["main_net_inflow"] == 1234567.89
    assert flow["data"]["stock_flow"]["inflow_5d"] is None
    assert flow["data"]["stock_flow"]["inflow_10d"] is None
    assert enhanced["fundamental_context"]["coverage"]["capital_flow"] == "ok"
    assert result.fundamental_context["capital_flow"]["source"] == "futu_opend"


def test_news_context_injects_same_futu_block_whether_search_on_or_off() -> None:
    expected_block = format_prompt_block(FUTU_SNAP)
    assert "## 权威数据（Futu OpenD，非搜索）" in expected_block
    assert "- 综合评级: Buy" in expected_block

    captured = {}
    for search_on in (False, True):
        pipeline = _build_pipeline(search_available=search_on)
        with patch("data_provider.futu_research.snapshot", return_value=FUTU_SNAP):
            result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-futu-news")
        assert result is not None
        captured[search_on] = pipeline.analyzer.analyze.call_args.kwargs["news_context"]

    news_off = captured[False]
    news_on = captured[True]
    assert expected_block.strip() in news_off
    assert expected_block.strip() in news_on
    assert news_off.startswith(expected_block.strip())
    assert news_on.startswith(expected_block.strip())
    assert news_off.index("权威数据（Futu OpenD，非搜索）") < news_off.index("社区情绪（Futu，非官方）")
    assert "SEARCH INTEL BLOCK" in news_on
    assert news_on.index(expected_block.strip()) < news_on.index("SEARCH INTEL BLOCK")



def test_pipeline_does_not_invent_short_interest_analyzer_fields() -> None:
    """Analyzer/fundamental schema has no short-interest fields; only the prompt gets them."""
    from dataclasses import fields

    from src.analyzer import AnalysisResult

    analyzer_fields = {item.name for item in fields(AnalysisResult)}
    assert "short_interest" not in analyzer_fields
    assert "shares_short" not in analyzer_fields
    assert "short_percent" not in analyzer_fields
    assert "days_to_cover" not in analyzer_fields
    assert "avg_daily_share_volume" not in analyzer_fields

    snap = {
        **FUTU_SNAP,
        "short_interest": {
            "shares_short": 50000000,
            "short_percent": 2.5,
            "days_to_cover": 2.0,
            "avg_daily_share_volume": 28000000,
            "close": 180.5,
            "as_of": "2026-08-15",
            "vs_prior": {
                "shares_short": 5000000,
                "short_percent": 1.25,
                "days_to_cover": 0.5,
            },
        },
    }
    pipeline = _build_pipeline(search_available=False)
    with patch("data_provider.futu_research.snapshot", return_value=snap):
        result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-futu-short")

    assert result is not None
    enhanced = pipeline.analyzer.analyze.call_args.args[0]
    fundamental = enhanced["fundamental_context"]
    assert "short_interest" not in fundamental
    assert "shares_short" not in fundamental
    assert "short_percent" not in fundamental
    news = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    assert "### 空头持仓（Futu OpenD）" in news
    assert "- 卖空股数: 50,000,000（较上期 +5,000,000）" in news



def test_pipeline_news_context_uses_futu_headlines_not_serpapi() -> None:
    pipeline = _build_pipeline(search_available=True)
    service = SearchService(
        serpapi_keys=["dummy_key"],
        searxng_public_instances_enabled=False,
    )
    serp_search = MagicMock(
        return_value=SearchResponse(query="spy", results=[], provider="SerpAPI", success=False)
    )
    for provider in service._providers:
        provider.search = serp_search
    pipeline.search_service = service

    def fake_run(cmd, **_kwargs):
        if any("get_search_news.py" in str(part) for part in cmd):
            subtype = cmd[cmd.index("--news-sub-type") + 1] if "--news-sub-type" in cmd else "NEWS"
            title = "NVIDIA 10-K filed" if subtype == "NOTICE" else "NVIDIA announces new GPU"
            source = "SEC" if subtype == "NOTICE" else "Reuters"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "keyword": "NVIDIA",
                        "news_sub_type": subtype,
                        "data": [
                            {
                                "title": title,
                                "source": source,
                                "publish_time": "2026-08-16 10:00:00",
                                "url": "https://news.example.com/nvda",
                            }
                        ],
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout=json.dumps({"data": []}), stderr="")

    with patch("data_provider.futu_research.snapshot", return_value=FUTU_SNAP), patch(
        "data_provider.futu_research.subprocess.run", side_effect=fake_run
    ), patch("src.search_service.time.sleep", return_value=None):
        result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-futu-headlines")

    assert result is not None
    news = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    expected_block = format_prompt_block(FUTU_SNAP)
    assert expected_block.strip() in news
    assert "NVIDIA announces new GPU" in news
    assert "SerpAPI" not in news
    assert serp_search.call_count == 0
