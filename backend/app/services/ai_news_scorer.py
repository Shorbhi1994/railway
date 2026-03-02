"""
TradeMatrix AI Engine
Component 1: LLM News Impact Scoring

Railway-Ready Production Version
"""

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any, List

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are a financial news analyst scoring articles for STOCK PRICE impact.

RULES:
- Score STOCK PRICE impact, not general sentiment.
- Layoffs can be positive (+0.2 to +0.4).
- Beats earnings but lowers guidance = mixed.
- FDA approval = strong positive.
- CEO investigation = strong negative.
- Materiality must scale relative to company size.
- Mega caps (> $500B): most news 1–4 unless existential.
- Microcaps (< $2B): contracts/financing can be 7–9.

Return ONLY valid JSON array. No markdown.
"""

ALLOWED_CATEGORIES = {
    "earnings", "guidance", "analyst", "insider", "regulatory",
    "product", "management", "legal", "macro",
    "partnership", "other"
}

ALLOWED_HORIZONS = {"short", "mid", "long", "all"}


class AINewsScorer:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        settings = get_settings()
        self._api_key = settings.ANTHROPIC_API_KEY
        self._enabled = bool(self._api_key)
        self._model = "claude-haiku-4-5-20251001"

    async def score_stock_news(
        self,
        stock_id: int,
        symbol: str,
        company_name: str,
        market_cap_b: float,
        articles: List[dict],
        batch_date: date,
    ) -> dict[str, Any]:

        if not self._enabled or not articles:
            return {"status": "disabled" if not self._enabled else "no_articles"}

        # Deduplicate
        seen = set()
        unique = []

        for a in articles:
            h = hashlib.sha256(
                f"{a.get('title','')}{a.get('url','')}".encode()
            ).hexdigest()

            if h not in seen:
                seen.add(h)
                unique.append({**a, "_hash": h})

        # Cost safety cap
        unique = unique[:20]

        scored = []

        for i in range(0, len(unique), 10):
            batch = unique[i:i + 10]
            batch_scores = await self._call_llm(
                symbol, company_name, market_cap_b, batch
            )
            scored.extend(batch_scores)

        if not scored:
            return {"status": "failed"}

        await self._persist_scores(stock_id, scored, batch_date)

        summary = self._compute_daily_summary(scored)

        await self._persist_summary(stock_id, batch_date, summary)

        return summary

    async def _call_llm(
        self,
        symbol: str,
        company_name: str,
        market_cap_b: float,
        articles: List[dict],
    ) -> List[dict]:

        formatted = "\n".join(
            f"Article {i}: {a.get('title','')}. "
            f"{(a.get('text') or a.get('summary') or '')[:200]}"
            for i, a in enumerate(articles)
        )

        user_prompt = f"""
Score these {len(articles)} articles for {symbol}
({company_name}, market cap ${market_cap_b}B):

{formatted}

Return JSON:
[{{"index":0,"materiality":1-10,"sentiment":-1.0 to 1.0,
"category":"allowed","horizon":"short|mid|long|all"}}]
"""

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=10),
            ) as client:

                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "max_tokens": 1000,
                        "temperature": 0.1,
                        "system": SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )

                resp.raise_for_status()
                data = resp.json()

                text_content = data["content"][0]["text"]

                clean = re.sub(
                    r"^```json|^```|```$",
                    "",
                    text_content.strip(),
                    flags=re.MULTILINE,
                ).strip()

                results = json.loads(clean)
                results = sorted(results, key=lambda x: x.get("index", 0))

                if len(results) != len(articles):
                    raise ValueError("Incomplete LLM batch")

                scored = []

                for r in results:
                    idx = r.get("index", 0)

                    if 0 <= idx < len(articles):
                        scored.append({
                            "_hash": articles[idx]["_hash"],
                            "title": articles[idx].get("title", ""),
                            "source": articles[idx].get("source", ""),
                            "published_at": articles[idx].get("published_at"),
                            "materiality": max(1, min(10, int(r.get("materiality", 5)))),
                            "sentiment": max(-1.0, min(1.0, float(r.get("sentiment", 0)))),
                            "category": r.get("category")
                                if r.get("category") in ALLOWED_CATEGORIES else "other",
                            "horizon": r.get("horizon")
                                if r.get("horizon") in ALLOWED_HORIZONS else "all",
                        })

                return scored

        except Exception as exc:
            logger.warning("AI LLM call failed: %s", exc)

            return [{
                "_hash": a["_hash"],
                "title": a.get("title", ""),
                "source": a.get("source", ""),
                "published_at": a.get("published_at"),
                "materiality": 5,
                "sentiment": 0.0,
                "category": "other",
                "horizon": "all",
            } for a in articles]

    def _compute_daily_summary(self, scored: List[dict]) -> dict:

        weighted_sum = sum(s["materiality"] * s["sentiment"] for s in scored)
        weight_total = sum(s["materiality"] for s in scored)

        raw = weighted_sum / weight_total if weight_total else 0
        normalized = (raw + 1) * 50

        cats = {}
        for s in scored:
            cats[s["category"]] = cats.get(s["category"], 0) + 1

        top = max(scored, key=lambda s: abs(s["materiality"] * s["sentiment"]))

        return {
            "weighted_sentiment": round(normalized, 2),
            "article_count": len(scored),
            "high_impact_count": sum(1 for s in scored if s["materiality"] >= 7),
            "avg_materiality": round(
                sum(s["materiality"] for s in scored) / len(scored), 2
            ),
            "top_driver_title": top["title"],
            "top_driver_sentiment": top["sentiment"],
            "category_breakdown": cats,
        }

    async def _persist_scores(self, stock_id, scored, batch_date):

        query = text("""
            INSERT INTO ai_news_scores (
                stock_id, article_hash, title, source, published_at,
                materiality, sentiment, category, horizon_affected, batch_date
            )
            VALUES (
                :stock_id, :article_hash, :title, :source, :published_at,
                :materiality, :sentiment, :category, :horizon, :batch_date
            )
            ON CONFLICT DO NOTHING
        """)

        async with self._engine.begin() as conn:
            for s in scored:
                await conn.execute(query, {
                    "stock_id": stock_id,
                    "article_hash": s["_hash"],
                    "title": s["title"],
                    "source": s["source"],
                    "published_at": s["published_at"],
                    "materiality": s["materiality"],
                    "sentiment": s["sentiment"],
                    "category": s["category"],
                    "horizon": s["horizon"],
                    "batch_date": batch_date,
                })

    async def _persist_summary(self, stock_id, batch_date, summary):

        query = text("""
            INSERT INTO ai_news_daily_summary (
                stock_id, batch_date, weighted_sentiment,
                article_count, high_impact_count,
                avg_materiality, top_driver_title,
                top_driver_sentiment, category_breakdown
            )
            VALUES (
                :stock_id, :batch_date, :weighted_sentiment,
                :article_count, :high_impact_count,
                :avg_materiality, :top_driver_title,
                :top_driver_sentiment, :category_breakdown
            )
            ON CONFLICT (stock_id, batch_date)
            DO UPDATE SET
                weighted_sentiment = EXCLUDED.weighted_sentiment,
                article_count = EXCLUDED.article_count,
                high_impact_count = EXCLUDED.high_impact_count,
                avg_materiality = EXCLUDED.avg_materiality,
                top_driver_title = EXCLUDED.top_driver_title,
                top_driver_sentiment = EXCLUDED.top_driver_sentiment,
                category_breakdown = EXCLUDED.category_breakdown
        """)

        async with self._engine.begin() as conn:
            await conn.execute(query, {
                "stock_id": stock_id,
                "batch_date": batch_date,
                **summary,
            })
