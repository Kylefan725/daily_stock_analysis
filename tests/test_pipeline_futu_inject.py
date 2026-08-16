# -*- coding: utf-8 -*-
"""Pipeline injection of the Futu daily tape (capital-flow overlay + news_context)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data_provider.futu_research import format_prompt_block
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


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
    assert news_off.strip() == expected_block.strip()
    assert news_on.startswith(expected_block.strip())
    assert "SEARCH INTEL BLOCK" in news_on
    assert news_on.index(expected_block.strip()) < news_on.index("SEARCH INTEL BLOCK")
