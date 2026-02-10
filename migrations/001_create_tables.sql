-- ============================================================
-- Binance Spot Bot — Supabase Migration
-- 建立所有資料表 + RLS + Realtime
-- ============================================================

-- 1. bot_config — 配置版本管理（取代 config.yaml）
CREATE TABLE IF NOT EXISTS bot_config (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version     INT NOT NULL,
    config_json JSONB NOT NULL,
    changed_by  TEXT DEFAULT 'frontend',
    change_note TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. strategy_verdicts — 策略結論
CREATE TABLE IF NOT EXISTS strategy_verdicts (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    signal      TEXT NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 0,
    reasoning   TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. llm_decisions — LLM 最終決策
CREATE TABLE IF NOT EXISTS llm_decisions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 0,
    reasoning   TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. orders — 訂單紀錄
CREATE TABLE IF NOT EXISTS orders (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    order_type  TEXT NOT NULL DEFAULT 'market',
    quantity    DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION,
    filled      DOUBLE PRECISION DEFAULT 0,
    status      TEXT DEFAULT 'filled',
    exchange_id TEXT DEFAULT '',
    source      TEXT DEFAULT 'bot',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. positions — 當前持倉（upsert by symbol）
CREATE TABLE IF NOT EXISTS positions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      TEXT NOT NULL UNIQUE,
    quantity    DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION DEFAULT 0,
    unrealized_pnl DOUBLE PRECISION DEFAULT 0,
    stop_loss   DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. loan_health — 借款 LTV 快照
CREATE TABLE IF NOT EXISTS loan_health (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_coin       TEXT NOT NULL,
    collateral_coin TEXT NOT NULL,
    ltv             DOUBLE PRECISION NOT NULL,
    total_debt      DOUBLE PRECISION NOT NULL,
    collateral_amount DOUBLE PRECISION NOT NULL,
    action_taken    TEXT DEFAULT 'none',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7. bot_logs — Bot 日誌
CREATE TABLE IF NOT EXISTS bot_logs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level       TEXT NOT NULL DEFAULT 'INFO',
    module      TEXT DEFAULT '',
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. market_snapshots — 價格快照
CREATE TABLE IF NOT EXISTS market_snapshots (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol      TEXT NOT NULL,
    price       DOUBLE PRECISION NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9. bot_status — Bot 運行狀態 / 心跳
CREATE TABLE IF NOT EXISTS bot_status (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_num   INT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    config_ver  INT,
    pairs       TEXT[],
    uptime_sec  INT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_strategy_verdicts_symbol ON strategy_verdicts (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_decisions_symbol ON llm_decisions (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_loan_health_created ON loan_health (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_logs_created ON bot_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_logs_level ON bot_logs (level, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol ON market_snapshots (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_config_version ON bot_config (version DESC);
CREATE INDEX IF NOT EXISTS idx_bot_status_updated ON bot_status (updated_at DESC);

-- ============================================================
-- RLS（Row Level Security）
-- 前端（anon role）：
--   bot_config → 讀寫
--   其他表 → 只讀
-- Bot（service_role）：完全存取（繞過 RLS）
-- ============================================================
ALTER TABLE bot_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_verdicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE loan_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_status ENABLE ROW LEVEL SECURITY;

-- bot_config: 前端可讀可寫
CREATE POLICY "anon_read_config" ON bot_config FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert_config" ON bot_config FOR INSERT TO anon WITH CHECK (true);

-- 其他表: 前端只讀
CREATE POLICY "anon_read_verdicts" ON strategy_verdicts FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_decisions" ON llm_decisions FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_orders" ON orders FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_positions" ON positions FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_loan" ON loan_health FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_logs" ON bot_logs FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_snapshots" ON market_snapshots FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_status" ON bot_status FOR SELECT TO anon USING (true);

-- ============================================================
-- Realtime — 啟用所有表的即時推播
-- ============================================================
ALTER PUBLICATION supabase_realtime ADD TABLE bot_config;
ALTER PUBLICATION supabase_realtime ADD TABLE strategy_verdicts;
ALTER PUBLICATION supabase_realtime ADD TABLE llm_decisions;
ALTER PUBLICATION supabase_realtime ADD TABLE orders;
ALTER PUBLICATION supabase_realtime ADD TABLE positions;
ALTER PUBLICATION supabase_realtime ADD TABLE loan_health;
ALTER PUBLICATION supabase_realtime ADD TABLE bot_logs;
ALTER PUBLICATION supabase_realtime ADD TABLE market_snapshots;
ALTER PUBLICATION supabase_realtime ADD TABLE bot_status;
