"""create ai news scoring tables

Revision ID: 001_ai_news_scoring
Revises: (set to your latest migration ID)
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "001_ai_news_scoring"
down_revision = None  # <-- Replace with your latest migration ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Article-level AI scored news
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_news_scores (
            id                  SERIAL PRIMARY KEY,
            stock_id            INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
            article_hash        VARCHAR(64) NOT NULL,
            title               TEXT NOT NULL,
            source              VARCHAR(100),
            published_at        TIMESTAMPTZ,

            materiality         SMALLINT NOT NULL CHECK (materiality BETWEEN 1 AND 10),
            sentiment           NUMERIC(4,3) NOT NULL CHECK (sentiment BETWEEN -1 AND 1),
            category            VARCHAR(30) NOT NULL,
            horizon_affected    VARCHAR(10) DEFAULT 'all',

            model_used          VARCHAR(50) DEFAULT 'claude-haiku-4.5',
            scored_at           TIMESTAMPTZ DEFAULT NOW(),
            batch_date          DATE NOT NULL,

            UNIQUE(stock_id, article_hash, batch_date)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_news_stock_date
        ON ai_news_scores(stock_id, batch_date);
        """
    )

    # Daily aggregated summary per stock
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_news_daily_summary (
            stock_id                INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
            batch_date              DATE NOT NULL,

            weighted_sentiment      NUMERIC(5,2) NOT NULL,
            article_count           INTEGER NOT NULL,
            high_impact_count       INTEGER DEFAULT 0,
            avg_materiality         NUMERIC(4,2),

            top_driver_title        TEXT,
            top_driver_sentiment    NUMERIC(4,3),

            category_breakdown      JSONB,

            PRIMARY KEY(stock_id, batch_date)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_news_daily_summary;")
    op.execute("DROP INDEX IF EXISTS ix_ai_news_stock_date;")
    op.execute("DROP TABLE IF EXISTS ai_news_scores;")