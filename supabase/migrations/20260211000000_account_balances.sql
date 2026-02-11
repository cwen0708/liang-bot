-- ============================================================
-- Migration: account_balances — 帳戶餘額快照
-- ============================================================

-- 10. account_balances — 帳戶餘額快照
CREATE TABLE IF NOT EXISTS account_balances (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    currency    TEXT NOT NULL,
    free        DOUBLE PRECISION NOT NULL DEFAULT 0,
    usdt_value  DOUBLE PRECISION NOT NULL DEFAULT 0,
    snapshot_id TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_account_balances_created ON account_balances (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_account_balances_snapshot ON account_balances (snapshot_id);

ALTER TABLE account_balances ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_read_balances" ON account_balances
    FOR SELECT TO anon USING (true);

ALTER PUBLICATION supabase_realtime ADD TABLE account_balances;
