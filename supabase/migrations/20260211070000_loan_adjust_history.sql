-- 借貸 LTV 調整歷史（從幣安 API 同步）
CREATE TABLE IF NOT EXISTS loan_adjust_history (
    id              BIGSERIAL PRIMARY KEY,
    loan_coin       TEXT NOT NULL,
    collateral_coin TEXT NOT NULL,
    direction       TEXT NOT NULL,           -- 'ADDITIONAL' or 'REDUCED'
    amount          NUMERIC NOT NULL,        -- 調整數量
    pre_ltv         NUMERIC,                 -- 調整前 LTV
    after_ltv       NUMERIC,                 -- 調整後 LTV
    adjust_time     TIMESTAMPTZ NOT NULL,    -- 幣安原始調整時間
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 唯一約束：防止重複同步
CREATE UNIQUE INDEX IF NOT EXISTS idx_loan_adjust_unique
    ON loan_adjust_history (loan_coin, collateral_coin, adjust_time);

-- RLS: anon 只讀
ALTER TABLE loan_adjust_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read_loan_adjust_history"
    ON loan_adjust_history FOR SELECT TO anon USING (true);

-- Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE loan_adjust_history;
