-- ============================================================
-- futures_margin / futures_funding 加 mode 欄位
-- 區分 live / paper（含 testnet）資料
-- ============================================================

ALTER TABLE futures_margin ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE futures_funding ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';

-- 更新索引（加入 mode 篩選）
DROP INDEX IF EXISTS idx_futures_margin_created;
CREATE INDEX idx_futures_margin_mode_created ON futures_margin (mode, created_at DESC);

DROP INDEX IF EXISTS idx_futures_funding_symbol;
CREATE INDEX idx_futures_funding_mode_symbol ON futures_funding (mode, symbol, created_at DESC);
