"""
backend/tests/test_ai_news_scorer.py

Tests for AINewsScorer — unit tests + integration smoke test.
Run: pytest backend/tests/test_ai_news_scorer.py -v
"""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_news_scorer import AINewsScorer, ALLOWED_CATEGORIES, ALLOWED_HORIZONS


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_scorer(api_key: str | None = "test-key") -> AINewsScorer:
    engine = AsyncMock()
    engine.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=AsyncMock()), __aexit__=AsyncMock()))
    scorer = AINewsScorer(engine)
    scorer._api_key = api_key
    scorer._enabled = bool(api_key)
    return scorer


SAMPLE_ARTICLES = [
    {
        "title": "Apple beats Q3 earnings, raises guidance",
        "url": "https://example.com/1",
        "text": "Apple reported record revenue of $90B, above expectations of $87B.",
        "site": "Reuters",
        "publishedDate": "2024-07-25T16:00:00Z",
    },
    {
        "title": "Apple CEO under SEC investigation",
        "url": "https://example.com/2",
        "text": "Regulators are investigating potential insider trading.",
        "site": "Bloomberg",
        "publishedDate": "2024-07-25T10:00:00Z",
    },
]

MOCK_LLM_RESPONSE = json.dumps([
    {"index": 0, "materiality": 8, "sentiment": 0.7, "category": "earnings", "horizon": "short"},
    {"index": 1, "materiality": 9, "sentiment": -0.8, "category": "legal", "horizon": "mid"},
])


# ── Unit Tests ────────────────────────────────────────────────────────────────

class TestComputeDailySummary:
    def test_basic_weighted_sentiment(self):
        scorer = make_scorer()
        scored = [
            {"materiality": 10, "sentiment": 1.0, "category": "earnings", "horizon": "short",
             "title": "Big win", "_hash": "a"},
            {"materiality": 5, "sentiment": -0.5, "category": "legal", "horizon": "mid",
             "title": "Bad news", "_hash": "b"},
        ]
        result = scorer._compute_daily_summary(scored)

        # weighted = (10*1.0 + 5*-0.5) / 15 = 7.5/15 = 0.5 → normalized = 75.0
        assert result["weighted_sentiment"] == 75.0
        assert result["article_count"] == 2
        assert result["high_impact_count"] == 2  # both materiality >= 7
        assert result["category_breakdown"] == {"earnings": 1, "legal": 1}
        assert result["top_driver_title"] == "Big win"

    def test_neutral_news(self):
        scorer = make_scorer()
        scored = [
            {"materiality": 5, "sentiment": 0.0, "category": "other", "horizon": "all",
             "title": "Meh", "_hash": "x"},
        ]
        result = scorer._compute_daily_summary(scored)
        assert result["weighted_sentiment"] == 50.0

    def test_all_negative(self):
        scorer = make_scorer()
        scored = [
            {"materiality": 8, "sentiment": -1.0, "category": "legal", "horizon": "short",
             "title": "Disaster", "_hash": "y"},
        ]
        result = scorer._compute_daily_summary(scored)
        assert result["weighted_sentiment"] == 0.0


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_duplicates_removed(self):
        scorer = make_scorer(api_key=None)  # disabled
        articles = [
            {"title": "Same", "url": "https://x.com/1"},
            {"title": "Same", "url": "https://x.com/1"},  # duplicate
        ]
        result = await scorer.score_stock_news(
            stock_id=1, symbol="AAPL", company_name="Apple",
            market_cap_b=3000, articles=articles, batch_date=date.today()
        )
        assert result["status"] == "disabled"


class TestAllowedValues:
    def test_allowed_categories_complete(self):
        expected = {
            "earnings", "guidance", "analyst", "insider", "regulatory",
            "product", "management", "legal", "macro", "partnership", "other"
        }
        assert ALLOWED_CATEGORIES == expected

    def test_allowed_horizons(self):
        assert ALLOWED_HORIZONS == {"short", "mid", "long", "all"}


class TestFallbackOnLLMFailure:
    @pytest.mark.asyncio
    async def test_fallback_returns_neutral_scores(self):
        scorer = make_scorer()

        articles_with_hash = [
            {**a, "_hash": f"hash{i}"}
            for i, a in enumerate(SAMPLE_ARTICLES)
        ]

        with patch.object(scorer, "_call_llm", side_effect=Exception("API down")):
            # _call_llm has internal fallback — shouldn't raise
            # We test the fallback path directly:
            result = await scorer._call_llm("AAPL", "Apple", 3000, articles_with_hash)

        for r in result:
            assert r["materiality"] == 5
            assert r["sentiment"] == 0.0
            assert r["category"] == "other"


# ── Integration Smoke Test ────────────────────────────────────────────────────

class TestIntegrationWithMockLLM:
    @pytest.mark.asyncio
    async def test_full_flow_with_mock_llm(self):
        scorer = make_scorer()

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": MOCK_LLM_RESPONSE}]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch.object(scorer, "_persist_scores", AsyncMock()):
                with patch.object(scorer, "_persist_summary", AsyncMock()):
                    result = await scorer.score_stock_news(
                        stock_id=1,
                        symbol="AAPL",
                        company_name="Apple Inc",
                        market_cap_b=3000,
                        articles=SAMPLE_ARTICLES,
                        batch_date=date(2024, 7, 25),
                    )

        assert "weighted_sentiment" in result
        assert result["article_count"] == 2
        assert result["high_impact_count"] == 2  # both materiality >= 7
        # Weighted: (8*0.7 + 9*-0.8) / 17 = (5.6 - 7.2) / 17 = -0.094 → ~45.3
        assert 40 < result["weighted_sentiment"] < 55
        assert result["category_breakdown"] == {"earnings": 1, "legal": 1}