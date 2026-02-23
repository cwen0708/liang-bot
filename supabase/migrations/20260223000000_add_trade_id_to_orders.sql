-- Add trade_id column to orders for proper trade grouping
-- Links open and close orders of the same trade together
ALTER TABLE orders ADD COLUMN trade_id TEXT DEFAULT '';
CREATE INDEX idx_orders_trade_id ON orders(trade_id) WHERE trade_id != '';
