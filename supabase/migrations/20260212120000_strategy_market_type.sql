-- 新增 market_type 欄位到 strategy_verdicts 和 llm_decisions
-- 區分現貨/合約的策略結論與 LLM 決策

ALTER TABLE strategy_verdicts ADD COLUMN IF NOT EXISTS market_type TEXT NOT NULL DEFAULT 'spot';
ALTER TABLE llm_decisions ADD COLUMN IF NOT EXISTS market_type TEXT NOT NULL DEFAULT 'spot';

CREATE INDEX IF NOT EXISTS idx_strategy_verdicts_market_type ON strategy_verdicts (market_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_decisions_market_type ON llm_decisions (market_type, created_at DESC);
