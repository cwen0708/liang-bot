-- 修正歷史資料：合約專屬幣對的舊紀錄從 'spot' 改為 'futures'
-- BTC/USDT, ETH/USDT 同時存在於現貨和合約，舊紀錄保留 'spot'（因為當時確實是現貨交易）
-- PAXG/USDT 只有現貨，不需變更

UPDATE strategy_verdicts
SET market_type = 'futures'
WHERE market_type = 'spot'
  AND symbol IN ('LINK/USDT', 'AVAX/USDT', 'BNB/USDT', 'SOL/USDT', 'DOGE/USDT', 'ADA/USDT');

UPDATE llm_decisions
SET market_type = 'futures'
WHERE market_type = 'spot'
  AND symbol IN ('LINK/USDT', 'AVAX/USDT', 'BNB/USDT', 'SOL/USDT', 'DOGE/USDT', 'ADA/USDT');
