-- ============================================================
-- Migration: 為 strategy_verdicts 和 llm_decisions 新增 cycle_id
-- 用於精確配對同一輪 cycle 中的策略結論與 AI 決策
-- ============================================================

ALTER TABLE strategy_verdicts ADD COLUMN IF NOT EXISTS cycle_id TEXT;
ALTER TABLE llm_decisions ADD COLUMN IF NOT EXISTS cycle_id TEXT;

CREATE INDEX IF NOT EXISTS idx_strategy_verdicts_cycle ON strategy_verdicts (cycle_id);
CREATE INDEX IF NOT EXISTS idx_llm_decisions_cycle ON llm_decisions (cycle_id);
