-- 配置重構：trading+risk → spot, futures ATR 巢狀化
-- 1. 刪除舊 trading.* 和 risk.* schema
-- 2. 新增 spot.* schema
-- 3. 更新 futures ATR schema（平鋪 → 巢狀）
-- 4. 新增 futures.strategies, futures.min_confidence

-- ── 1. 刪除舊 schema ──
DELETE FROM config_schema WHERE path LIKE 'trading%' OR path LIKE 'risk%';

-- ── 2. 刪除舊 futures ATR 平鋪 schema ──
DELETE FROM config_schema WHERE path IN (
    'futures.atr_period',
    'futures.atr_sl_multiplier',
    'futures.atr_tp_multiplier',
    'futures.use_atr_stops'
);

-- ── 3. 新增 spot.* + 更新 futures ATR 巢狀 + 新欄位 ──
INSERT INTO config_schema (path, label, description, field_type, options, step, sort_order) VALUES
-- Spot（現貨交易 + 風控，取代 trading + risk）
('spot',                            '現貨交易',       '現貨交易參數與風控設定（合併交易與風險管理）',                                                                           NULL,      NULL,                                              NULL,  100),
('spot.mode',                       '交易模式',       'paper = 模擬交易（不實際下單），live = 真實交易。切換前請確認資金與策略已就緒',                                            'select',  '["paper","live"]'::jsonb,                         NULL,  110),
('spot.pairs',                      '交易對',         'Bot 監控的幣安現貨交易對清單，格式為 BTC/USDT。每輪 cycle 會依序掃描所有交易對',                                           'tags',    NULL,                                              NULL,  120),
('spot.timeframe',                  'K 線週期',       '策略分析使用的 K 線時間框架。較短週期信號多但雜訊大，較長週期信號穩但反應慢',                                               'select',  '["1m","5m","15m","30m","1h","4h","1d"]'::jsonb,   NULL,  130),
('spot.check_interval_seconds',     '檢查間隔',       '每輪交易循環之間的等待秒數。過短會增加 API 請求頻率，過長可能錯過進場時機',                                                 'integer', NULL,                                              NULL,  140),
('spot.max_position_pct',           '最大部位',       '單筆交易最多使用可用餘額的百分比。例如 10% 表示每筆最多投入總資金的 10%',                                                   'percent', NULL,                                              0.01,  150),
('spot.stop_loss_pct',              '停損',           '持倉虧損達此百分比時自動賣出止損。例如 3% 表示虧損超過買入價的 3% 即觸發停損',                                              'percent', NULL,                                              0.01,  160),
('spot.take_profit_pct',            '停利',           '持倉獲利達此百分比時自動賣出止盈。例如 6% 表示獲利超過買入價的 6% 即觸發停利',                                              'percent', NULL,                                              0.01,  170),
('spot.max_open_positions',         '最大持倉數',     '同時持有的最大倉位數量。超過此數量時不會開新倉，避免過度分散資金',                                                           'integer', NULL,                                              NULL,  180),
('spot.max_daily_loss_pct',         '每日虧損上限',   '當日累計虧損達此百分比時，Bot 停止開新倉直到隔日。防止單日過度損失',                                                         'percent', NULL,                                              0.01,  190),
-- Futures: ATR 巢狀
('futures.atr',                     'ATR 動態停損',   'ATR（平均真實波幅）動態停損停利配置',                                                                                      NULL,      NULL,                                              NULL,  1070),
('futures.atr.period',              '計算週期',       'ATR 計算的 K 線回看週期。用於動態計算停損停利距離',                                                                         'integer', NULL,                                              NULL,  1071),
('futures.atr.sl_multiplier',       '停損倍率',       '停損距離 = ATR × 此倍率。例如 1.5 表示停損設在 1.5 倍 ATR 距離',                                                           'number',  NULL,                                              0.1,   1072),
('futures.atr.tp_multiplier',       '停利倍率',       '停利距離 = ATR × 此倍率。與停損倍率的比值決定盈虧比（例如 3.0/1.5=2.0）',                                                  'number',  NULL,                                              0.1,   1073),
('futures.atr.enabled',             '啟用動態 SL/TP', '啟用 ATR 動態停損停利。關閉則使用固定百分比。建議開啟以適應不同市場波動度',                                                 'boolean', NULL,                                              NULL,  1074),
-- Futures: 新欄位
('futures.strategies',              '合約策略清單',   '合約專屬策略清單，為空則共用現貨策略',                                                                                      NULL,      NULL,                                              NULL,  1080),
('futures.min_confidence',          'LLM 信心門檻',   '合約 LLM 決策的最低信心分數（0-1）。低於此值降級為 HOLD',                                                                  'number',  NULL,                                              0.1,   1090)
ON CONFLICT (path) DO UPDATE SET
    label       = EXCLUDED.label,
    description = EXCLUDED.description,
    field_type  = EXCLUDED.field_type,
    options     = EXCLUDED.options,
    step        = EXCLUDED.step,
    sort_order  = EXCLUDED.sort_order,
    updated_at  = now();

-- ── 4. 轉換最新 bot_config 記錄（若存在舊格式 trading+risk → spot）──
-- 使用 DO BLOCK 檢查並轉換
DO $$
DECLARE
    latest_config JSONB;
    latest_id INT;
    new_config JSONB;
    spot_obj JSONB;
    futures_obj JSONB;
    atr_obj JSONB;
BEGIN
    -- 取最新配置
    SELECT id, config_json INTO latest_id, latest_config
    FROM bot_config ORDER BY version DESC LIMIT 1;

    IF latest_id IS NULL THEN RETURN; END IF;

    -- 已有 spot key 則跳過
    IF latest_config ? 'spot' THEN RETURN; END IF;

    -- 只有舊格式（trading + risk）才需要轉換
    IF NOT (latest_config ? 'trading') THEN RETURN; END IF;

    -- 合併 trading + risk → spot
    spot_obj := COALESCE(latest_config->'trading', '{}'::jsonb) || COALESCE(latest_config->'risk', '{}'::jsonb);

    -- 轉換 futures ATR（平鋪 → 巢狀）
    futures_obj := COALESCE(latest_config->'futures', '{}'::jsonb);
    IF futures_obj ? 'atr_period' THEN
        atr_obj := jsonb_build_object(
            'period', futures_obj->'atr_period',
            'sl_multiplier', futures_obj->'atr_sl_multiplier',
            'tp_multiplier', futures_obj->'atr_tp_multiplier',
            'enabled', futures_obj->'use_atr_stops'
        );
        futures_obj := futures_obj - 'atr_period' - 'atr_sl_multiplier' - 'atr_tp_multiplier' - 'use_atr_stops';
        futures_obj := futures_obj || jsonb_build_object('atr', atr_obj);
    END IF;

    -- 加入 futures.strategies 和 futures.min_confidence（若不存在）
    IF NOT (futures_obj ? 'strategies') THEN
        futures_obj := futures_obj || '{"strategies": []}'::jsonb;
    END IF;
    IF NOT (futures_obj ? 'min_confidence') THEN
        futures_obj := futures_obj || '{"min_confidence": 0.3}'::jsonb;
    END IF;

    -- 組建新配置
    new_config := latest_config - 'trading' - 'risk';
    new_config := new_config || jsonb_build_object('spot', spot_obj);
    new_config := jsonb_set(new_config, '{futures}', futures_obj);

    -- 插入新版本
    INSERT INTO bot_config (version, config_json, changed_by, change_note)
    VALUES (
        (SELECT COALESCE(MAX(version), 0) + 1 FROM bot_config),
        new_config,
        'migration',
        'refactor: trading+risk → spot, futures ATR nested'
    );
END $$;
