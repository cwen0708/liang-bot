-- 為合約設定獨立的策略清單（不含現貨專用的 tia_orderflow）
UPDATE bot_config
SET config_json = jsonb_set(
  config_json,
  '{futures,strategies}',
  '[
    {"name": "sma_crossover", "interval_n": 60, "params": {"fast_period": 10, "slow_period": 30}},
    {"name": "rsi_oversold", "interval_n": 60, "params": {"period": 14, "oversold": 30, "overbought": 70}},
    {"name": "bollinger_breakout", "interval_n": 60, "params": {"period": 20, "std_dev": 2.0}},
    {"name": "macd_momentum", "interval_n": 60, "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9}}
  ]'::jsonb
)
WHERE version = (SELECT MAX(version) FROM bot_config);
