# -*- coding: utf-8 -*-
"""US/HK single-stock news: Futu primary, Longbridge fallback, no SerpAPI."""

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
from data_provider.longbridge_cli import map_longbridge_news
from data_provider.futu_research import resolve_stock_headlines
from src.search_service import SearchResponse, SearchResult, SearchService


@pytest.fixture(autouse=True)
def _clear_search_news_cache():
    futu_research._search_news_cache.clear()
    yield
    futu_research._search_news_cache.clear()


def test_map_longbridge_news_uses_official_cli_schema() -> None:
    """longbridge news --schema: id, title, url, published_at, likes_count, comments_count."""
    payload = [
        {
            "id": 12345678,
            "title": "Tesla delivers record quarter",
            "url": "https://longbridge.com/news/12345678",
            "published_at": "2026-08-16T14:30:00Z",
            "likes_count": 12,
            "comments_count": 3,
        },
        {
            "id": "87654321",
            "title": "EV demand update",
            "url": "https://longbridge.com/news/87654321",
            "published_at": "2026-08-15T09:00:00Z",
            "likes_count": 0,
            "comments_count": 0,
        },
    ]

    headlines = map_longbridge_news(payload)

    assert headlines == [
        {
            "title": "Tesla delivers record quarter",
            "time": "2026-08-16T14:30:00Z",
            "source": "Longbridge",
            "url": "https://longbridge.com/news/12345678",
        },
        {
            "title": "EV demand update",
            "time": "2026-08-15T09:00:00Z",
            "source": "Longbridge",
            "url": "https://longbridge.com/news/87654321",
        },
    ]


def test_map_longbridge_news_fail_open_on_empty_or_error() -> None:
    assert map_longbridge_news(None) == []
    assert map_longbridge_news({}) == []
    assert map_longbridge_news([]) == []
    assert map_longbridge_news("nope") == []
    assert map_longbridge_news([{"id": 1}]) == []



def _proc(payload, returncode=0):
    stdout = json.dumps(payload) if not isinstance(payload, str) else payload
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_resolve_stock_headlines_prefers_futu_search_news() -> None:
    captured = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        if any("get_search_news.py" in str(part) for part in cmd):
            return _proc(
                {
                    "keyword": "NVIDIA",
                    "news_sub_type": "NEWS",
                    "data": [
                        {
                            "title": "NVIDIA announces new GPU",
                            "source": "Reuters",
                            "publish_time": "2026-08-16 10:00:00",
                            "url": "https://news.example.com/nvda-gpu",
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run), patch(
        "data_provider.longbridge_cli.subprocess.run", side_effect=fake_run
    ):
        headlines = resolve_stock_headlines("NVDA", "NVIDIA")

    assert headlines == [
        {
            "title": "NVIDIA announces new GPU",
            "time": "2026-08-16 10:00:00",
            "source": "Reuters",
            "url": "https://news.example.com/nvda-gpu",
        }
    ]
    assert any("get_search_news.py" in str(part) for cmd in captured for part in cmd)
    assert all("longbridge" not in str(part).lower() for cmd in captured for part in cmd)
    assert all("serpapi" not in str(part).lower() for cmd in captured for part in cmd)



def test_resolve_stock_headlines_falls_back_to_longbridge() -> None:
    captured = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        joined = " ".join(str(part) for part in cmd)
        if "get_search_news.py" in joined:
            return _proc({"keyword": "NVIDIA", "news_sub_type": "NEWS", "data": []})
        if "longbridge" in joined and "news" in joined:
            return _proc(
                [
                    {
                        "id": 12345678,
                        "title": "Tesla delivers record quarter",
                        "url": "https://longbridge.com/news/12345678",
                        "published_at": "2026-08-16T14:30:00Z",
                        "likes_count": 12,
                        "comments_count": 3,
                    }
                ]
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run), patch(
        "data_provider.longbridge_cli.subprocess.run", side_effect=fake_run
    ):
        headlines = resolve_stock_headlines("NVDA", "NVIDIA")

    assert headlines == [
        {
            "title": "Tesla delivers record quarter",
            "time": "2026-08-16T14:30:00Z",
            "source": "Longbridge",
            "url": "https://longbridge.com/news/12345678",
        }
    ]
    assert any("get_search_news.py" in str(part) for cmd in captured for part in cmd)
    assert any("news" in str(part) and "longbridge" in " ".join(str(x) for x in cmd)
               for cmd in captured for part in cmd)
    assert all("serpapi" not in str(part).lower() for cmd in captured for part in cmd)


def test_resolve_stock_headlines_empty_when_both_fail() -> None:
    def fake_run(cmd, **_kwargs):
        raise RuntimeError("provider down")

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run), patch(
        "data_provider.longbridge_cli.subprocess.run", side_effect=fake_run
    ):
        headlines = resolve_stock_headlines("NVDA", "NVIDIA")

    assert headlines == []



def _futu_news_run(cmd, **_kwargs):
    if any("get_search_news.py" in str(part) for part in cmd):
        return _proc(
            {
                "keyword": "NVIDIA",
                "news_sub_type": "NEWS",
                "data": [
                    {
                        "title": "NVIDIA announces new GPU",
                        "source": "Reuters",
                        "publish_time": "2026-08-16 10:00:00",
                        "url": "https://news.example.com/nvda-gpu",
                    }
                ],
            }
        )
    return _proc([])


def test_us_single_stock_latest_news_uses_resolver_not_serpapi() -> None:
    service = SearchService(
        serpapi_keys=["dummy_key"],
        searxng_public_instances_enabled=False,
    )
    serp_search = MagicMock(
        return_value=SearchResponse(query="spy", results=[], provider="SerpAPI", success=False)
    )
    for provider in service._providers:
        provider.search = serp_search

    with patch("data_provider.futu_research.subprocess.run", side_effect=_futu_news_run), patch(
        "src.search_service.time.sleep", return_value=None
    ):
        results = service.search_comprehensive_intel("NVDA", "NVIDIA", max_searches=5)

    latest = results["latest_news"]
    assert latest.success
    assert [item.title for item in latest.results] == ["NVIDIA announces new GPU"]
    assert latest.results[0].source == "Reuters"
    assert latest.results[0].published_date == "2026-08-16 10:00:00"
    assert latest.results[0].url == "https://news.example.com/nvda-gpu"
    latest_news_calls = [
        call
        for call in serp_search.call_args_list
        if call.args and "latest news" in str(call.args[0]).lower()
    ]
    assert latest_news_calls == []



def test_hk_single_stock_latest_news_uses_resolver_not_serpapi() -> None:
    service = SearchService(
        serpapi_keys=["dummy_key"],
        searxng_public_instances_enabled=False,
    )
    serp_search = MagicMock(
        return_value=SearchResponse(query="spy", results=[], provider="SerpAPI", success=False)
    )
    for provider in service._providers:
        provider.search = serp_search

    def fake_run(cmd, **_kwargs):
        if any("get_search_news.py" in str(part) for part in cmd):
            return _proc(
                {
                    "keyword": "Tencent",
                    "news_sub_type": "NEWS",
                    "data": [
                        {
                            "title": "Tencent games beat estimates",
                            "source": "SCMP",
                            "publish_time": "2026-08-16 11:00:00",
                            "url": "https://news.example.com/0700",
                        }
                    ],
                }
            )
        return _proc([])

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run), patch(
        "src.search_service.time.sleep", return_value=None
    ):
        results = service.search_comprehensive_intel("HK00700", "Tencent", max_searches=5)

    latest = results["latest_news"]
    assert [item.title for item in latest.results] == ["Tencent games beat estimates"]
    latest_news_calls = [
        call
        for call in serp_search.call_args_list
        if call.args and "latest news" in str(call.args[0]).lower()
    ]
    assert latest_news_calls == []



def test_a_share_latest_news_keeps_existing_search_path() -> None:
    """A-share intel must keep the existing search path, not Futu-only routing."""
    service = SearchService(
        serpapi_keys=["dummy_key"],
        searxng_public_instances_enabled=False,
    )
    provider_response = SearchResponse(
        query="贵州茅台 600519 最新 新闻 重大 事件",
        results=[
            SearchResult(
                title="茅台发布公告",
                snippet="公司公告",
                url="https://www.cninfo.com.cn/maotai",
                source="cninfo",
                published_date="2026-08-16",
            )
        ],
        provider="SerpAPI",
        success=True,
    )
    serp_search = MagicMock(return_value=provider_response)
    for provider in service._providers:
        provider.search = serp_search

    with patch("data_provider.futu_research.resolve_stock_headlines") as mock_resolve, patch(
        "src.search_service.time.sleep", return_value=None
    ):
        results = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=5)

    mock_resolve.assert_not_called()
    assert serp_search.called
    queries = [call.args[0] for call in serp_search.call_args_list if call.args]
    assert any("最新 新闻" in query for query in queries)
    assert any("研报 目标价" in query for query in queries)
    assert "latest_news" in results
    assert "market_analysis" in results



def test_us_risk_check_uses_futu_notice_not_serpapi() -> None:
    service = SearchService(
        serpapi_keys=["dummy_key"],
        searxng_public_instances_enabled=False,
    )
    serp_search = MagicMock(
        return_value=SearchResponse(query="spy", results=[], provider="SerpAPI", success=False)
    )
    for provider in service._providers:
        provider.search = serp_search

    def fake_run(cmd, **_kwargs):
        if any("get_search_news.py" in str(part) for part in cmd):
            subtype = cmd[cmd.index("--news-sub-type") + 1] if "--news-sub-type" in cmd else "ALL"
            if subtype == "NOTICE":
                return _proc(
                    {
                        "keyword": "NVIDIA",
                        "news_sub_type": "NOTICE",
                        "data": [
                            {
                                "title": "NVIDIA 10-K filed",
                                "source": "SEC",
                                "publish_time": "2026-08-16 09:00:00",
                                "url": "https://news.example.com/nvda-10k",
                            }
                        ],
                    }
                )
            return _proc(
                {
                    "keyword": "NVIDIA",
                    "news_sub_type": "NEWS",
                    "data": [
                        {
                            "title": "NVIDIA announces new GPU",
                            "source": "Reuters",
                            "publish_time": "2026-08-16 10:00:00",
                            "url": "https://news.example.com/nvda-gpu",
                        }
                    ],
                }
            )
        return _proc([])

    with patch("data_provider.futu_research.subprocess.run", side_effect=fake_run), patch(
        "src.search_service.time.sleep", return_value=None
    ):
        results = service.search_comprehensive_intel("NVDA", "NVIDIA", max_searches=5)

    risk = results["risk_check"]
    assert [item.title for item in risk.results] == ["NVIDIA 10-K filed"]
    assert risk.results[0].source == "SEC"
    assert serp_search.call_count == 0
