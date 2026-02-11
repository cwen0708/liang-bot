-- ============================================================
-- USDT-M 永續合約支援
-- ============================================================

-- 1. positions 表新增合約欄位
ALTER TABLE positions ADD COLUMN IF NOT EXISTS side TEXT NOT NULL DEFAULT 'long';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS leverage INT NOT NULL DEFAULT 1;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS liquidation_price DOUBLE PRECISION;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS market_type TEXT NOT NULL DEFAULT 'spot';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS margin_type TEXT DEFAULT 'cross';

-- 更新唯一約束：允許同幣種同時有現貨和合約持倉
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_symbol_mode_key;
ALTER TABLE positions ADD CONSTRAINT positions_symbol_mode_market_side_key
    UNIQUE (symbol, mode, market_type, side);

-- 2. orders 表新增合約欄位
ALTER TABLE orders ADD COLUMN IF NOT EXISTS market_type TEXT NOT NULL DEFAULT 'spot';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS position_side TEXT DEFAULT 'long';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS leverage INT DEFAULT 1;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reduce_only BOOLEAN DEFAULT false;

-- 3. 資金費率紀錄表
CREATE TABLE IF NOT EXISTS futures_funding (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol          TEXT NOT NULL,
    funding_rate    DOUBLE PRECISION NOT NULL,
    funding_fee     DOUBLE PRECISION NOT NULL,
    position_size   DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. 合約保證金帳戶快照
CREATE TABLE IF NOT EXISTS futures_margin (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    total_wallet_balance  DOUBLE PRECISION NOT NULL,
    available_balance     DOUBLE PRECISION NOT NULL,
    total_unrealized_pnl  DOUBLE PRECISION NOT NULL,
    total_margin_balance  DOUBLE PRECISION NOT NULL,
    margin_ratio          DOUBLE PRECISION NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_positions_market_type ON positions (market_type);
CREATE INDEX IF NOT EXISTS idx_orders_market_type ON orders (market_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_futures_funding_symbol ON futures_funding (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_futures_margin_created ON futures_margin (created_at DESC);

-- RLS
ALTER TABLE futures_funding ENABLE ROW LEVEL SECURITY;
ALTER TABLE futures_margin ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read_futures_funding" ON futures_funding FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_futures_margin" ON futures_margin FOR SELECT TO anon USING (true);

-- Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE futures_funding;
ALTER PUBLICATION supabase_realtime ADD TABLE futures_margin;
