"""
worker/main.py — Integration snippet for AI News Scorer (Step 6.5)

Add this block AFTER Step 6 (price ingestion) and BEFORE Step 7 (scoring).
The block is fully wrapped in try/except — it will NEVER crash your pipeline.
"""

# ──────────────────────────────────────────────
# STEP 6.5: AI News Scoring (non-fatal, shadow mode)
# ──────────────────────────────────────────────
from app.services.ai_news_scorer import AINewsScorer

_ai_scorer: AINewsScorer | None = None

if settings.ANTHROPIC_API_KEY:
    _ai_scorer = AINewsScorer(engine)

async def run_ai_news_scoring(stock, news_articles: list[dict], batch_date) -> dict | None:
    """
    Score news for a single stock via Claude Haiku.
    Returns summary dict or None on any failure.
    Always safe to call — never raises.
    """
    if _ai_scorer is None or not news_articles:
        return None

    try:
        summary = await _ai_scorer.score_stock_news(
            stock_id=stock.id,
            symbol=stock.symbol,
            company_name=stock.name,
            market_cap_b=getattr(stock, "market_cap_b", 0.0) or 0.0,
            articles=news_articles,
            batch_date=batch_date,
        )
        return summary

    except Exception as exc:
        logger.warning(
            "AI news scoring failed for %s (non-fatal): %s",
            stock.symbol,
            exc,
        )
        return None


# ──────────────────────────────────────────────
# Inside your stock processing loop:
# ──────────────────────────────────────────────

# for stock in stocks:
#     ...
#     # Step 6: price ingestion (existing)
#     ...
#
#     # Step 6.5: AI news scoring (new)
#     ai_summary = await run_ai_news_scoring(stock, news_articles, batch_date)
#
#     # Step 7: factor scoring (existing)
#     # Pass ai_summary into factor engine if available
#     ...