-- ============================================================
-- 所有資料表加 mode 欄位
-- 區分 live / paper（含 testnet）資料
-- 前端 Live/Paper 切換必須正確篩選對應資料
-- ============================================================

-- strategy_verdicts
ALTER TABLE strategy_verdicts ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_verdicts_mode_created ON strategy_verdicts (mode, created_at DESC);

-- llm_decisions
ALTER TABLE llm_decisions ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_llm_mode_created ON llm_decisions (mode, created_at DESC);

-- market_snapshots
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_snapshots_mode_created ON market_snapshots (mode, created_at DESC);

-- account_balances
ALTER TABLE account_balances ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_balances_mode_created ON account_balances (mode, created_at DESC);

-- bot_status
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';

-- loan_health
ALTER TABLE loan_health ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_loan_health_mode_created ON loan_health (mode, created_at DESC);
