-- 更新交易對配置：
-- 現貨: BTC/USDT, ETH/USDT, PAXG/USDT
-- 合約: BTC/USDT, ETH/USDT, LINK/USDT, AVAX/USDT, BNB/USDT, SOL/USDT, DOGE/USDT, ADA/USDT

DO $$
DECLARE
    latest_config JSONB;
    latest_id INT;
    new_config JSONB;
    spot_pairs JSONB := '["BTC/USDT","ETH/USDT","PAXG/USDT"]'::jsonb;
    futures_pairs JSONB := '["BTC/USDT","ETH/USDT","LINK/USDT","AVAX/USDT","BNB/USDT","SOL/USDT","DOGE/USDT","ADA/USDT"]'::jsonb;
BEGIN
    SELECT id, config_json INTO latest_id, latest_config
    FROM bot_config ORDER BY version DESC LIMIT 1;

    IF latest_id IS NULL THEN RETURN; END IF;

    new_config := latest_config;

    -- 更新 spot.pairs（或 trading.pairs）
    IF new_config ? 'spot' THEN
        new_config := jsonb_set(new_config, '{spot,pairs}', spot_pairs);
    ELSIF new_config ? 'trading' THEN
        new_config := jsonb_set(new_config, '{trading,pairs}', spot_pairs);
    END IF;

    -- 更新 futures.pairs
    IF new_config ? 'futures' THEN
        new_config := jsonb_set(new_config, '{futures,pairs}', futures_pairs);
    END IF;

    -- 插入新版本
    INSERT INTO bot_config (version, config_json, changed_by, change_note)
    VALUES (
        (SELECT COALESCE(MAX(version), 0) + 1 FROM bot_config),
        new_config,
        'migration',
        '現貨: BTC+ETH+PAXG, 合約: 8 pairs'
    );
END $$;
