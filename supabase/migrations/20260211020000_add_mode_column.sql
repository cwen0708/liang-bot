-- positions: 加 mode 欄位（預設 paper，因為現有資料皆為模擬）
ALTER TABLE positions ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';
-- 改 unique 約束為 (symbol, mode)，允許同幣種同時有 paper 和 live 持倉
ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_symbol_key;
ALTER TABLE positions ADD CONSTRAINT positions_symbol_mode_key UNIQUE (symbol, mode);

-- orders: 加 mode 欄位
-- 現有訂單透過 exchange_id 判斷：paper_ 開頭為 paper，其餘為 live
ALTER TABLE orders ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
UPDATE orders SET mode = 'paper' WHERE exchange_id LIKE 'paper_%';
CREATE INDEX IF NOT EXISTS idx_orders_mode ON orders (mode, created_at DESC);
