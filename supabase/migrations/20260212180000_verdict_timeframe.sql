-- 策略結論加入 timeframe 欄位
ALTER TABLE strategy_verdicts ADD COLUMN IF NOT EXISTS timeframe TEXT DEFAULT '';
