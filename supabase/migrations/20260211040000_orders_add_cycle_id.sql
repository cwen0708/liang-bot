-- orders: 加入 cycle_id 欄位，關聯 AI 決策
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cycle_id text DEFAULT '';
