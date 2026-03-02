"""
factor_engine.py — AI News Score integration snippet

Drop this into your factor engine where you compute the
"News & Media Tone" indicator. Replaces direct lexicon score
lookup with AI-weighted score (with lexicon fallback).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import date


async def get_ai_news_score(
    conn: AsyncConnection,
    stock_id: int,
    batch_date: date,
    legacy_lexicon_score: float = 50.0,
) -> float:
    """
    Return the AI-weighted news sentiment score (0-100) for a stock.

    Falls back to legacy_lexicon_score if:
    - No AI summary exists for this date
    - Article count is 0 (no news)
    - Any DB error

    Args:
        conn: Active async DB connection
        stock_id: Stock primary key
        batch_date: Date to query
        legacy_lexicon_score: Fallback score from existing pipeline

    Returns:
        float: News sentiment score 0-100
    """
    try:
        result = await conn.execute(
            text(
                """
                SELECT weighted_sentiment, article_count
                FROM ai_news_daily_summary
                WHERE stock_id = :stock_id
                  AND batch_date = :batch_date
                LIMIT 1
                """
            ),
            {"stock_id": stock_id, "batch_date": batch_date},
        )
        row = result.fetchone()

        if row and row.article_count > 0:
            # AI score available — clamp to safe range
            ai_score = float(row.weighted_sentiment)
            return max(10.0, min(90.0, ai_score))

    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to fetch AI news score for stock_id=%s: %s", stock_id, exc
        )

    # Fallback to lexicon
    return float(legacy_lexicon_score)


# ──────────────────────────────────────────────
# Shadow mode: store BOTH scores for IC comparison
# ──────────────────────────────────────────────

async def get_news_score_shadow(
    conn: AsyncConnection,
    stock_id: int,
    batch_date: date,
    legacy_lexicon_score: float = 50.0,
) -> dict:
    """
    Returns both AI and lexicon scores for shadow evaluation.
    Use this during the 30-day shadow period instead of get_ai_news_score().

    Returns:
        dict with keys: news_score_ai, news_score_lexicon, source
    """
    ai_score = await get_ai_news_score(conn, stock_id, batch_date, legacy_lexicon_score)

    return {
        "news_score_ai": ai_score,
        "news_score_lexicon": float(legacy_lexicon_score),
        "source": "ai" if ai_score != float(legacy_lexicon_score) else "lexicon",
    }


# ──────────────────────────────────────────────
# Usage in your factor computation:
# ──────────────────────────────────────────────
#
# async with engine.connect() as conn:
#
#     # SHADOW MODE (first 30 days — recommended):
#     scores = await get_news_score_shadow(
#         conn, stock_id, batch_date, legacy_lexicon_score
#     )
#     news_score = scores["news_score_lexicon"]   # still using lexicon in production
#     # Log both for IC analysis:
#     logger.info("News scores | %s", scores)
#
#     # PRODUCTION MODE (after IC validation):
#     news_score = await get_ai_news_score(
#         conn, stock_id, batch_date, legacy_lexicon_score
#     )