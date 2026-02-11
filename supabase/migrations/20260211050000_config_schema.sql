-- config_schema: 前端設定頁面的欄位 schema（標籤、描述、型別、選項）
-- 前端啟動時從此表載入，硬編碼保留為 fallback

CREATE TABLE IF NOT EXISTS config_schema (
    path        TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT DEFAULT '',
    field_type  TEXT,                       -- 'string'|'number'|'integer'|'boolean'|'select'|'percent'|'tags'| NULL(group)
    options     JSONB,                      -- select: ["paper","live"]
    step        NUMERIC,                    -- number/percent step
    sort_order  INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- RLS: anon 只讀
ALTER TABLE config_schema ENABLE ROW LEVEL SECURITY;

CREATE POLICY "config_schema_anon_select"
    ON config_schema FOR SELECT
    TO anon
    USING (true);

-- Seed 資料
INSERT INTO config_schema (path, label, description, field_type, options, step, sort_order) VALUES
-- Trading
('trading',                        '交易設定',       '核心交易參數，包含模式、交易對與 K 線週期設定',                                                                   NULL,      NULL,                                              NULL,  100),
('trading.mode',                   '交易模式',       'paper = 模擬交易（不實際下單），live = 真實交易。切換前請確認資金與策略已就緒',                                      'select',  '["paper","live"]'::jsonb,                         NULL,  110),
('trading.pairs',                  '交易對',         'Bot 監控的幣安現貨交易對清單，格式為 BTC/USDT。每輪 cycle 會依序掃描所有交易對',                                     'tags',    NULL,                                              NULL,  120),
('trading.timeframe',              'K 線週期',       '策略分析使用的 K 線時間框架。較短週期信號多但雜訊大，較長週期信號穩但反應慢',                                         'select',  '["1m","5m","15m","30m","1h","4h","1d"]'::jsonb,   NULL,  130),
('trading.check_interval_seconds', '檢查間隔',       '每輪交易循環之間的等待秒數。過短會增加 API 請求頻率，過長可能錯過進場時機',                                           'integer', NULL,                                              NULL,  140),
-- Risk
('risk',                           '風險管理',       '控制每筆交易的風險敞口、停損停利與每日虧損上限',                                                                     NULL,      NULL,                                              NULL,  200),
('risk.max_position_pct',          '最大部位',       '單筆交易最多使用可用餘額的百分比。例如 10% 表示每筆最多投入總資金的 10%',                                             'percent', NULL,                                              0.01,  210),
('risk.stop_loss_pct',             '停損',           '持倉虧損達此百分比時自動賣出止損。例如 3% 表示虧損超過買入價的 3% 即觸發停損',                                        'percent', NULL,                                              0.01,  220),
('risk.take_profit_pct',           '停利',           '持倉獲利達此百分比時自動賣出止盈。例如 6% 表示獲利超過買入價的 6% 即觸發停利',                                        'percent', NULL,                                              0.01,  230),
('risk.max_open_positions',        '最大持倉數',     '同時持有的最大倉位數量。超過此數量時不會開新倉，避免過度分散資金',                                                     'integer', NULL,                                              NULL,  240),
('risk.max_daily_loss_pct',        '每日虧損上限',   '當日累計虧損達此百分比時，Bot 停止開新倉直到隔日。防止單日過度損失',                                                   'percent', NULL,                                              0.01,  250),
-- Backtest
('backtest',                       '回測設定',       '歷史回測引擎的參數，用於驗證策略在過往數據上的表現',                                                                 NULL,      NULL,                                              NULL,  300),
('backtest.start_date',            '起始日期',       '回測的開始日期，格式 YYYY-MM-DD',                                                                                   'string',  NULL,                                              NULL,  310),
('backtest.end_date',              '結束日期',       '回測的結束日期，格式 YYYY-MM-DD',                                                                                   'string',  NULL,                                              NULL,  320),
('backtest.initial_balance',       '初始餘額',       '回測起始的模擬資金（USDT），用於計算報酬率與最大回撤',                                                               'number',  NULL,                                              NULL,  330),
('backtest.commission_pct',        '手續費率',       '回測中模擬的每筆交易手續費率。幣安現貨一般為 0.1%，使用 BNB 扣抵可降至 0.075%',                                      'percent', NULL,                                              0.001, 340),
-- Order Flow
('orderflow',                      'Order Flow 設定', 'Order Flow 分析引擎的參數，透過逐筆成交分析市場微結構',                                                            NULL,      NULL,                                              NULL,  400),
('orderflow.bar_interval_seconds', 'Bar 間隔',        'Order Flow Bar 的時間間隔（秒）。較短間隔可捕捉更細微的成交變化',                                                   'integer', NULL,                                              NULL,  410),
('orderflow.tick_size',            'Tick 大小',       '價格聚合的最小刻度。影響 footprint chart 的價格分層精細度',                                                         'number',  NULL,                                              0.01,  420),
('orderflow.cvd_lookback',         'CVD 回看期',      '累積成交量差（Cumulative Volume Delta）的回看 Bar 數。用於判斷買賣力道趨勢',                                        'integer', NULL,                                              NULL,  430),
('orderflow.zscore_lookback',      'Z-score 回看期',  '計算成交量 Z-score 的回看期，用於偵測異常成交量（大單吸收等）',                                                     'integer', NULL,                                              NULL,  440),
('orderflow.divergence_peak_order','背離峰值階數',    '偵測價格與 CVD 背離時，尋找局部峰值的階數。數值越大要求越明確的峰值',                                               'integer', NULL,                                              NULL,  450),
('orderflow.sfp_swing_lookback',   'SFP 擺動回看',   'Swing Failure Pattern 偵測的回看 Bar 數。SFP 用於識別假突破後的反轉信號',                                            'integer', NULL,                                              NULL,  460),
('orderflow.absorption_lookback',  '吸收量回看',     '大單吸收偵測的回看期。當價格不動但成交量異常放大，可能代表大戶在吸收賣壓',                                             'integer', NULL,                                              NULL,  470),
('orderflow.signal_threshold',     '信號閾值',       'Order Flow 綜合信號的觸發閾值。數值越高要求越強的信號才會發出 BUY/SELL',                                              'number',  NULL,                                              0.1,   480),
-- Strategies
('strategies',                     '多策略列表',     '啟用的策略清單，每個策略獨立執行並產生信號，最終由路由器彙整',                                                         NULL,      NULL,                                              NULL,  500),
-- LLM
('llm',                            'LLM 決策引擎',   '所有非 HOLD 信號強制經過 LLM（Claude）審核，作為最終買賣決策的把關者',                                               NULL,      NULL,                                              NULL,  600),
('llm.enabled',                    '啟用 LLM',       '開啟後所有 BUY/SELL 信號會送交 Claude 進行 AI 審核。關閉則直接執行策略信號',                                         'boolean', NULL,                                              NULL,  610),
('llm.cli_path',                   'CLI 路徑',       'Claude CLI 的可執行檔路徑，Bot 透過此路徑呼叫 LLM 進行決策',                                                        'string',  NULL,                                              NULL,  620),
('llm.model',                      '模型',           '使用的 Claude 模型名稱，例如 claude-sonnet-4-5-20250929',                                                            'string',  NULL,                                              NULL,  630),
('llm.timeout',                    '逾時',           'LLM 呼叫的最長等待秒數。超時則視為 HOLD（不執行交易），避免阻塞交易循環',                                             'integer', NULL,                                              NULL,  640),
('llm.min_confidence',             '最低信心',       'LLM 回覆的信心分數門檻（0-1）。低於此值的決策會被降級為 HOLD',                                                       'number',  NULL,                                              0.1,   650),
-- Loan Guard
('loan_guard',                     '借貸守衛',       '借貸再平衡機制，依 LTV（貸款價值比）自動執行保護操作',                                                               NULL,      NULL,                                              NULL,  700),
('loan_guard.enabled',             '啟用',           '開啟借貸守衛功能。啟用後會定期檢查 LTV 並在必要時自動買入質押或減倉',                                                 'boolean', NULL,                                              NULL,  710),
('loan_guard.target_ltv',          '目標 LTV',       '目標貸款價值比。LTV 超過此值時發出警告，接近 danger 時觸發保護',                                                     'percent', NULL,                                              0.01,  720),
('loan_guard.danger_ltv',          '危險 LTV',       'LTV 達此值時觸發緊急保護：自動買入並質押（低買策略），防止被強制清算',                                                'percent', NULL,                                              0.01,  730),
('loan_guard.low_ltv',             '低 LTV',         'LTV 低於此值時觸發獲利：自動減質押並賣出（高賣策略），釋放多餘抵押品',                                                'percent', NULL,                                              0.01,  740),
('loan_guard.dry_run',             '模擬模式',       '開啟後只記錄操作意圖但不實際執行交易，用於測試 LTV 判斷邏輯是否正確',                                                 'boolean', NULL,                                              NULL,  750),
-- Logging
('logging',                        '日誌設定',       'Bot 日誌輸出的等級與儲存方式',                                                                                       NULL,      NULL,                                              NULL,  800),
('logging.level',                  '日誌等級',       'DEBUG = 所有細節，INFO = 一般運行，WARNING = 警告，ERROR = 僅錯誤。生產環境建議 INFO',                                'select',  '["DEBUG","INFO","WARNING","ERROR"]'::jsonb,       NULL,  810),
('logging.file_enabled',           '檔案日誌',       '是否將日誌同時寫入本地檔案。開啟後可用於離線排查問題',                                                                'boolean', NULL,                                              NULL,  820),
('logging.log_dir',                '日誌目錄',       '日誌檔案的儲存資料夾路徑。僅在檔案日誌啟用時有效',                                                                   'string',  NULL,                                              NULL,  830)
ON CONFLICT (path) DO UPDATE SET
    label       = EXCLUDED.label,
    description = EXCLUDED.description,
    field_type  = EXCLUDED.field_type,
    options     = EXCLUDED.options,
    step        = EXCLUDED.step,
    sort_order  = EXCLUDED.sort_order,
    updated_at  = now();
