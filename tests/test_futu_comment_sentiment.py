# -*- coding: utf-8 -*-
"""Futu community-feed sentiment. Public seams only. No live futunn.com / adanos.org."""

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

from data_provider.futu_comment_sentiment import (
    aggregate_labels,
    classify_post,
    fetch_stock_feed,
    filter_low_quality,
    representative_viewpoints,
    batch_snapshot,
    format_prompt_block,
    snapshot,
)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_fetch_stock_feed_maps_success_json_and_sends_contract_headers() -> None:
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["ua"] = req.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "code": 0,
                "message": "",
                "data": [
                    {
                        "id": "p1",
                        "title": "NVDA AI demand",
                        "desc": "Demand is accelerating",
                        "publish_time": 1775043900,
                        "url": "https://news.futunn.com/p1",
                    }
                ],
            }
        )

    with patch("data_provider.futu_comment_sentiment.urlopen", side_effect=fake_urlopen):
        posts = fetch_stock_feed("NVDA")

    assert captured["ua"] == "futunn-comment-sentiment/0.0.2 (Skill)"
    assert "keyword=NVDA" in captured["url"]
    assert "size=30" in captured["url"]
    assert "https://ai-news-search.futunn.com/stock_feed" in captured["url"]
    assert posts == [
        {
            "id": "p1",
            "title": "NVDA AI demand",
            "desc": "Demand is accelerating",
            "publish_time": 1775043900,
            "url": "https://news.futunn.com/p1",
        }
    ]


def test_fetch_stock_feed_fail_open_on_nonzero_code_or_http_error() -> None:
    def fake_urlopen_code(req, timeout=None):
        return _FakeResponse({"code": 1, "message": "busy", "data": []})

    with patch("data_provider.futu_comment_sentiment.urlopen", side_effect=fake_urlopen_code):
        assert fetch_stock_feed("NVDA") == []

    with patch(
        "data_provider.futu_comment_sentiment.urlopen",
        side_effect=OSError("connection refused"),
    ):
        assert fetch_stock_feed("AAPL") == []



def test_filter_low_quality_drops_filler_keeps_concrete_opinion() -> None:
    posts = [
        {"id": "e", "title": "🚀🚀🚀", "desc": "", "publish_time": 1, "url": ""},
        {"id": "b1", "title": "买", "desc": "", "publish_time": 2, "url": ""},
        {"id": "b2", "title": "buy", "desc": "", "publish_time": 3, "url": ""},
        {"id": "s", "title": "加微信领取内幕消息", "desc": "限时优惠", "publish_time": 4, "url": ""},
        {"id": "n", "title": "看", "desc": "", "publish_time": 5, "url": ""},
        {
            "id": "keep",
            "title": "AI需求持续加速，这次反弹还没结束",
            "desc": "订单和毛利率都在扩张",
            "publish_time": 6,
            "url": "https://news.futunn.com/keep",
        },
    ]

    kept = filter_low_quality(posts)

    assert [p["id"] for p in kept] == ["keep"]
    assert kept[0]["title"] == "AI需求持续加速，这次反弹还没结束"



def test_classify_post_uses_deterministic_en_zh_keywords() -> None:
    assert classify_post("看好后市，突破在即，继续加仓") == "bullish"
    assert classify_post("AI demand is still accelerating; this rebound may continue") == "bullish"
    assert classify_post("业绩不及预期，担心继续下跌") == "bearish"
    assert classify_post("Demand softness and an earnings miss could push the stock lower") == "bearish"
    assert classify_post("公司今日发布了季度财报") == "neutral"
    assert classify_post("The company released its quarterly results today") == "neutral"



def test_aggregate_labels_mixed_rule_uses_literal_percentages() -> None:
    # 4 / 3 / 3 of 10 posts -> 40% / 30% / 30%. |40-30|=10 < 15 and both >= 25 -> mixed.
    mixed = aggregate_labels(["bullish"] * 4 + ["bearish"] * 3 + ["neutral"] * 3)
    assert mixed == {
        "label": "mixed",
        "bull_pct": 40,
        "bear_pct": 30,
        "neutral_pct": 30,
        "post_count": 10,
    }

    dominant = aggregate_labels(["bullish"] * 6 + ["bearish"] * 2 + ["neutral"] * 2)
    assert dominant == {
        "label": "bullish",
        "bull_pct": 60,
        "bear_pct": 20,
        "neutral_pct": 20,
        "post_count": 10,
    }

    weak = aggregate_labels(["bullish", "neutral"])
    assert weak == {
        "label": "neutral",
        "bull_pct": 50,
        "bear_pct": 0,
        "neutral_pct": 50,
        "post_count": 2,
    }



def test_representative_viewpoints_are_recent_opinions_in_hong_kong() -> None:
    posts = [
        {
            "id": "old",
            "title": "Valuation is not cheap, but fundamentals still deliver upside",
            "desc": "",
            "publish_time": 1775037780,
            "url": "",
        },
        {
            "id": "mid",
            "title": "The pullback looks like noise; still bullish mid-term",
            "desc": "",
            "publish_time": 1775040660,
            "url": "",
        },
        {
            "id": "new",
            "title": "AI demand is still accelerating",
            "desc": "this NVDA run may not be over yet",
            "publish_time": 1775043900000,
            "url": "",
        },
        {
            "id": "watch",
            "title": "still watching the tape today",
            "desc": "",
            "publish_time": 1775044000,
            "url": "",
        },
        {
            "id": "older",
            "title": "继续看多加仓，这是第四条不应入选",
            "desc": "",
            "publish_time": 1775030000,
            "url": "",
        },
    ]

    views = representative_viewpoints(posts)

    assert [item["text"] for item in views] == [
        "AI demand is still accelerating",
        "The pullback looks like noise; still bullish mid-term",
        "Valuation is not cheap, but fundamentals still deliver upside",
    ]
    assert [item["published_at"] for item in views] == [
        "2026-04-01 19:45",
        "2026-04-01 18:51",
        "2026-04-01 18:03",
    ]



def _feed_response(items, code=0):
    return _FakeResponse({"code": code, "message": "", "data": items})


def test_snapshot_returns_structured_sentiment_for_one_symbol() -> None:
    items = [
        {
            "id": "1",
            "title": "AI demand is still accelerating; this rebound may continue",
            "desc": "",
            "publish_time": 1775043900,
            "url": "https://news.futunn.com/1",
        },
        {
            "id": "2",
            "title": "Demand softness and an earnings miss could push the stock lower",
            "desc": "",
            "publish_time": 1775040660,
            "url": "",
        },
        {
            "id": "3",
            "title": "看好后市，突破在即，继续加仓",
            "desc": "",
            "publish_time": 1775037780,
            "url": "",
        },
        {
            "id": "4",
            "title": "买",
            "desc": "",
            "publish_time": 1775040000,
            "url": "",
        },
        {
            "id": "5",
            "title": "公司今日发布了季度财报",
            "desc": "",
            "publish_time": 1775034000,
            "url": "",
        },
    ]

    with patch("data_provider.futu_comment_sentiment.urlopen", return_value=_feed_response(items)):
        snap = snapshot("NVDA")

    assert snap["symbol"] == "NVDA"
    assert snap["status"] == "ok"
    assert snap["label"] == "bullish"
    assert snap["bull_pct"] == 50
    assert snap["bear_pct"] == 25
    assert snap["neutral_pct"] == 25
    assert snap["post_count"] == 4
    assert snap["top_opinions"] == [
        {
            "text": "AI demand is still accelerating; this rebound may continue",
            "published_at": "2026-04-01 19:45",
        },
        {
            "text": "Demand softness and an earnings miss could push the stock lower",
            "published_at": "2026-04-01 18:51",
        },
        {
            "text": "看好后市，突破在即，继续加仓",
            "published_at": "2026-04-01 18:03",
        },
    ]


def test_snapshot_fail_open_empty_or_http_error() -> None:
    with patch("data_provider.futu_comment_sentiment.urlopen", return_value=_feed_response([])):
        empty = snapshot("NVDA")
    assert empty["status"] == "empty"
    assert empty["post_count"] == 0
    assert empty["top_opinions"] == []

    with patch("data_provider.futu_comment_sentiment.urlopen", side_effect=OSError("down")):
        errored = snapshot("AAPL")
    assert errored["status"] == "error"
    assert errored["post_count"] == 0
    assert errored["symbol"] == "AAPL"



def test_batch_snapshot_continues_when_second_symbol_http_fails() -> None:
    ok_items = [
        {
            "id": "1",
            "title": "看好后市，突破在即，继续加仓",
            "desc": "",
            "publish_time": 1775043900,
            "url": "",
        },
        {
            "id": "2",
            "title": "AI demand is still accelerating; this rebound may continue",
            "desc": "",
            "publish_time": 1775040660,
            "url": "",
        },
        {
            "id": "3",
            "title": "公司今日发布了季度财报",
            "desc": "",
            "publish_time": 1775037780,
            "url": "",
        },
    ]
    calls = []

    def fake_urlopen(req, timeout=None):
        keyword = "NVDA" if "NVDA" in req.full_url else "AAPL"
        calls.append(keyword)
        if keyword == "AAPL":
            raise OSError("AAPL feed down")
        return _feed_response(ok_items)

    with patch("data_provider.futu_comment_sentiment.urlopen", side_effect=fake_urlopen):
        batch = batch_snapshot(["NVDA", "AAPL"])

    assert [item["symbol"] for item in batch] == ["NVDA", "AAPL"]
    assert batch[0]["status"] == "ok"
    assert batch[0]["post_count"] == 3
    assert batch[0]["label"] == "bullish"
    assert batch[1]["status"] == "error"
    assert batch[1]["post_count"] == 0
    assert calls == ["NVDA", "AAPL"]



def test_format_prompt_block_emits_community_section_not_official_targets() -> None:
    snap = {
        "symbol": "NVDA",
        "status": "ok",
        "label": "bullish",
        "bull_pct": 50,
        "bear_pct": 25,
        "neutral_pct": 25,
        "post_count": 4,
        "top_opinions": [
            {
                "text": "AI demand is still accelerating; this rebound may continue",
                "published_at": "2026-04-01 19:45",
            },
            {
                "text": "Demand softness and an earnings miss could push the stock lower",
                "published_at": "2026-04-01 18:51",
            },
        ],
    }

    block = format_prompt_block(snap)

    assert "社区情绪（Futu，非官方）" in block
    assert "方向" in block
    assert "看多" in block
    assert "占比" in block
    assert "看多 50%" in block
    assert "看空 25%" in block
    assert "中性 25%" in block
    assert "帖数" in block
    assert "帖数: 4" in block
    assert "AI demand is still accelerating; this rebound may continue" in block
    assert "2026-04-01 19:45" in block
    assert "平均目标价" not in block
    assert "关键财务" not in block
    assert "权威数据（Futu OpenD" not in block

    missing = format_prompt_block({"symbol": "NVDA", "status": "error"})
    assert "社区情绪（Futu，非官方）" in missing
    assert "不可用" in missing

    from data_provider.futu_research import format_prompt_block as format_official

    official_snap = {
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
        },
    }
    official_block = format_official(official_snap)
    assert "平均目标价: 200.0" in official_block
    community_again = format_prompt_block(snap)
    assert "平均目标价" not in community_again
    assert official_snap["consensus"]["average"] == 200.0
    assert "consensus" not in snap



def _build_pipeline(*, search_available: bool = False):
    from src.core.pipeline import StockAnalysisPipeline

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
        social_sentiment_api_key=None,
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
    pipeline.search_service.search_comprehensive_intel.return_value = {}
    pipeline.search_service.format_intel_report.return_value = "SEARCH INTEL BLOCK"
    pipeline.social_sentiment_service = None
    pipeline._emit_progress = MagicMock()
    pipeline._load_persisted_intelligence_context = MagicMock(return_value=None)
    pipeline._build_market_structure_context = MagicMock(return_value=None)
    pipeline._refresh_decision_action_for_final_result = MagicMock()
    pipeline._extract_decision_signal_after_history_save = MagicMock()
    return pipeline


OFFICIAL_FUTU_SNAP = {
    "code": "NVDA",
    "source": "futu_opend",
    "consensus": {
        "rating": "Buy",
        "average": 200.0,
        "highest": 220.0,
        "lowest": 180.0,
        "total": 42,
        "buy": 0.7,
        "hold": 0.2,
        "sell": 0.1,
    },
    "capital_flow_for_analyzer": None,
    "rating_changes": {},
    "earnings_calendar": {},
    "pre_market_rank": {},
}

COMMUNITY_ITEMS = [
    {
        "id": "1",
        "title": "AI demand is still accelerating; this rebound may continue",
        "desc": "",
        "publish_time": 1775043900,
        "url": "",
    },
    {
        "id": "2",
        "title": "看好后市，突破在即，继续加仓",
        "desc": "",
        "publish_time": 1775040660,
        "url": "",
    },
    {
        "id": "3",
        "title": "公司今日发布了季度财报",
        "desc": "",
        "publish_time": 1775037780,
        "url": "",
    },
]


def test_analyze_stock_injects_futu_community_without_social_api_key() -> None:
    from src.enums import ReportType

    pipeline = _build_pipeline()
    assert pipeline.social_sentiment_service is None

    with patch("data_provider.futu_research.snapshot", return_value=OFFICIAL_FUTU_SNAP), patch(
        "data_provider.futu_comment_sentiment.urlopen",
        return_value=_feed_response(COMMUNITY_ITEMS),
    ), patch("src.services.social_sentiment_service._get_with_retry") as adanos_http:
        result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-community")

    assert result is not None
    news = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    assert "社区情绪（Futu，非官方）" in news
    assert "方向" in news
    assert "占比" in news
    assert "帖数: 3" in news
    assert "AI demand is still accelerating; this rebound may continue" in news
    assert "平均目标价: 200.0" in news
    assert news.index("权威数据（Futu OpenD，非搜索）") < news.index("社区情绪（Futu，非官方）")
    assert adanos_http.call_count == 0
    assert OFFICIAL_FUTU_SNAP["consensus"]["average"] == 200.0
    assert "Social Sentiment Intelligence" not in news
    assert "api.adanos.org" not in news



def test_analyze_stock_uses_futu_community_even_when_social_key_set() -> None:
    from src.enums import ReportType

    pipeline = _build_pipeline()
    social = MagicMock()
    social.is_available = True
    social.get_social_context.return_value = (
        "Social Sentiment Intelligence for NVDA (Reddit / X / Polymarket)\n"
        "Source: api.adanos.org"
    )
    pipeline.social_sentiment_service = social

    with patch("data_provider.futu_research.snapshot", return_value=OFFICIAL_FUTU_SNAP), patch(
        "data_provider.futu_comment_sentiment.urlopen",
        return_value=_feed_response(COMMUNITY_ITEMS),
    ), patch("src.services.social_sentiment_service._get_with_retry") as adanos_http:
        result = pipeline.analyze_stock("NVDA", ReportType.SIMPLE, "q-community-key")

    assert result is not None
    news = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    assert "社区情绪（Futu，非官方）" in news
    assert "api.adanos.org" not in news
    assert "Social Sentiment Intelligence" not in news
    assert social.get_social_context.call_count == 0
    assert adanos_http.call_count == 0

    pipeline.fetcher_manager.get_stock_name.return_value = "Tencent"
    pipeline.db.get_analysis_context.return_value = {
        "code": "00700",
        "stock_name": "Tencent",
        "today": {},
        "yesterday": {},
    }
    with patch("data_provider.futu_research.snapshot", return_value={**OFFICIAL_FUTU_SNAP, "code": "00700"}), patch(
        "data_provider.futu_comment_sentiment.urlopen",
        return_value=_feed_response(COMMUNITY_ITEMS),
    ), patch("src.services.social_sentiment_service._get_with_retry") as adanos_hk:
        hk_result = pipeline.analyze_stock("00700", ReportType.SIMPLE, "q-community-hk")

    assert hk_result is not None
    news_hk = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    assert "社区情绪（Futu，非官方）" in news_hk
    assert social.get_social_context.call_count == 0
    assert adanos_hk.call_count == 0



def test_etf_community_sentiment_is_skipped_not_single_name_retail() -> None:
    from src.enums import ReportType

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        raise AssertionError("ETF must not call stock_feed")

    with patch("data_provider.futu_comment_sentiment.urlopen", side_effect=fake_urlopen):
        snap = snapshot("SPY")

    assert snap["status"] == "skipped"
    assert snap["post_count"] == 0
    assert calls == []

    pipeline = _build_pipeline()
    pipeline.fetcher_manager.get_stock_name.return_value = "SPDR S&P 500 ETF"
    pipeline.db.get_analysis_context.return_value = {
        "code": "SPY",
        "stock_name": "SPDR S&P 500 ETF",
        "today": {},
        "yesterday": {},
    }
    with patch("data_provider.futu_research.snapshot", return_value={**OFFICIAL_FUTU_SNAP, "code": "SPY"}), patch(
        "data_provider.futu_comment_sentiment.urlopen",
        side_effect=fake_urlopen,
    ):
        result = pipeline.analyze_stock("SPY", ReportType.SIMPLE, "q-etf")

    assert result is not None
    news = pipeline.analyzer.analyze.call_args.kwargs["news_context"]
    assert "帖数:" not in news
    assert "看多 50%" not in news
    assert calls == []
