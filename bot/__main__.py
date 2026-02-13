"""CLI 入口 — python -m bot [command]"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Binance Spot Trading Bot",
        prog="python -m bot",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用指令")

    # run
    run_parser = subparsers.add_parser("run", help="啟動交易機器人")
    run_parser.add_argument("--config", default=None, help="配置檔路徑")

    # run-async
    async_parser = subparsers.add_parser("run-async", help="WebSocket 非同步交易")
    async_parser.add_argument("--config", default=None, help="配置檔路徑")

    # backtest
    bt_parser = subparsers.add_parser("backtest", help="執行回測")
    bt_parser.add_argument("--config", default=None, help="配置檔路徑")
    bt_parser.add_argument("--symbol", default="BTC/USDT", help="交易對")
    bt_parser.add_argument("--no-plot", action="store_true", help="不生成圖表")
    bt_parser.add_argument(
        "--strategy", default=None,
        help="策略名稱 (sma_crossover / tia_orderflow)",
    )
    bt_parser.add_argument(
        "--aggtrade-file", default=None,
        help="aggTrade CSV 檔案路徑（用於 tick 級回測）",
    )

    # balance
    subparsers.add_parser("balance", help="查詢帳戶餘額")

    # loan
    subparsers.add_parser("loan", help="查詢借款狀態")

    # loan-guard
    lg_parser = subparsers.add_parser("loan-guard", help="借款 LTV 監控與自動保護")
    lg_parser.add_argument("--warn", type=float, default=0.65, help="目標 LTV 閾值 (預設 0.65)")
    lg_parser.add_argument("--danger", type=float, default=0.75, help="危險 LTV 閾值，觸發自動買入質押物 (預設 0.75)")
    lg_parser.add_argument("--low", type=float, default=0.40, help="低 LTV 閾值，觸發獲利了結賣出 (預設 0.40)")
    lg_parser.add_argument("--interval", type=int, default=60, help="檢查間隔秒數 (預設 60)")
    lg_parser.add_argument("--dry-run", action="store_true", help="模擬模式，不實際執行")

    # futures-balance
    subparsers.add_parser("futures-balance", help="查詢合約帳戶餘額")

    # validate
    subparsers.add_parser("validate", help="驗證配置")

    # config-push
    cp_parser = subparsers.add_parser("config-push", help="推送本地 config.yaml 到 Supabase bot_config")
    cp_parser.add_argument("--config", default=None, help="配置檔路徑")
    cp_parser.add_argument("--note", default="", help="變更說明")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        cmd_run(args)
    elif args.command == "run-async":
        cmd_run_async(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "balance":
        cmd_balance()
    elif args.command == "loan":
        cmd_loan()
    elif args.command == "loan-guard":
        cmd_loan_guard(args)
    elif args.command == "futures-balance":
        cmd_futures_balance()
    elif args.command == "validate":
        cmd_validate()
    elif args.command == "config-push":
        cmd_config_push(args)


def cmd_run(args) -> None:
    _kill_existing_bot()

    from bot.app import TradingBot

    bot = TradingBot(config_path=args.config)
    bot.run()


def _kill_existing_bot() -> None:
    """啟動前自動關閉已在運行的 Bot 進程（讓 run 等同於 restart）。"""
    import os

    try:
        import psutil
    except ImportError:
        return

    my_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == my_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            if len(cmdline) < 4:
                continue
            if cmdline[-3:] == ["-m", "bot", "run"]:
                name = (proc.info.get("name") or "").lower()
                if name in ("python.exe", "python3.exe", "python", "python3"):
                    print(f"偵測到舊 Bot 進程 PID={proc.info['pid']}，正在關閉...")
                    os.kill(proc.info["pid"], 9)
                    import time
                    time.sleep(1)
                    print("已關閉舊進程。")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def cmd_run_futures(args) -> None:
    _kill_existing_bot()

    from bot.app_futures import FuturesTradingBot

    bot = FuturesTradingBot(config_path=args.config)
    bot.run()


def cmd_futures_balance() -> None:
    from bot.config.settings import Settings
    from bot.exchange.futures_native_client import FuturesBinanceClient

    settings = Settings.load()
    client = FuturesBinanceClient(settings.exchange, settings.futures)

    balance = client.get_futures_balance()
    positions = client.get_positions()

    print("\n=== 合約帳戶餘額 ===")
    print(f"  錢包餘額:     {balance['total_wallet_balance']:.4f} USDT")
    print(f"  可用餘額:     {balance['available_balance']:.4f} USDT")
    print(f"  未實現盈虧:   {balance['total_unrealized_pnl']:.4f} USDT")
    print(f"  保證金餘額:   {balance['total_margin_balance']:.4f} USDT")

    ratio = client.get_margin_ratio()
    print(f"  保證金比率:   {ratio:.2%}")

    if positions:
        print(f"\n=== 持倉 ({len(positions)}) ===")
        for pos in positions:
            side_label = "多" if pos["side"] == "long" else "空"
            print(
                f"  {pos['symbol']} [{side_label}] "
                f"數量={pos['contracts']:.8f} "
                f"入場={pos['entry_price']:.2f} "
                f"標記={pos['mark_price']:.2f} "
                f"PnL={pos['unrealized_pnl']:.4f} "
                f"清算={pos['liquidation_price']:.2f} "
                f"槓桿={pos['leverage']}x"
            )
    else:
        print("\n  無持倉")


def cmd_run_async(args) -> None:
    import asyncio

    from bot.app_async import AsyncTradingBot

    bot = AsyncTradingBot(config_path=args.config)
    asyncio.run(bot.run())


def cmd_backtest(args) -> None:
    strategy_name = args.strategy or "sma_crossover"

    if strategy_name == "tia_orderflow":
        _cmd_backtest_orderflow(args)
    else:
        _cmd_backtest_ohlcv(args, strategy_name)


def _cmd_backtest_ohlcv(args, strategy_name: str) -> None:
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.report import save_report
    from bot.backtest.simulator import BacktestSimulator
    from bot.config.settings import Settings
    from bot.data.fetcher import DataFetcher
    from bot.exchange.binance_native_client import BinanceClient
    from bot.logging_config.logger import setup_logging
    from bot.strategy.sma_crossover import SMACrossoverStrategy

    settings = Settings.load(args.config)
    setup_logging(level=settings.logging.level)

    exchange = BinanceClient(settings.exchange)
    fetcher = DataFetcher(exchange)

    symbol = args.symbol
    df = fetcher.fetch_historical(
        symbol=symbol,
        timeframe=settings.spot.timeframe,
        start_date=settings.backtest.start_date,
        end_date=settings.backtest.end_date,
    )

    print(f"已載入 {len(df)} 根 K 線")

    strategy = SMACrossoverStrategy(settings.strategy.params)
    engine = BacktestEngine(settings.backtest, settings.spot)

    metrics = engine.run(strategy, df, symbol)
    print(metrics)

    sim = BacktestSimulator(settings.backtest.initial_balance, settings.backtest.commission_pct)
    save_report(metrics, sim, strategy.name, symbol, plot=not args.no_plot)


def _cmd_backtest_orderflow(args) -> None:
    from bot.backtest.tick_engine import TickBacktestEngine
    from bot.config.settings import Settings
    from bot.logging_config.logger import setup_logging
    from bot.strategy.tia_orderflow import TiaBTCOrderFlowStrategy

    settings = Settings.load(args.config)
    setup_logging(level=settings.logging.level)

    if not args.aggtrade_file:
        print("錯誤: tia_orderflow 策略需要 --aggtrade-file 參數")
        print("用法: python -m bot backtest --strategy tia_orderflow --aggtrade-file data/aggtrades/BTCUSDT-2024-01-15.csv")
        sys.exit(1)

    symbol = args.symbol

    # 從 orderflow config 取得策略參數
    of_params = {
        "bar_interval_seconds": settings.orderflow.bar_interval_seconds,
        "tick_size": settings.orderflow.tick_size,
        "cvd_lookback": settings.orderflow.cvd_lookback,
        "zscore_lookback": settings.orderflow.zscore_lookback,
        "divergence_peak_order": settings.orderflow.divergence_peak_order,
        "sfp_swing_lookback": settings.orderflow.sfp_swing_lookback,
        "absorption_lookback": settings.orderflow.absorption_lookback,
        "signal_threshold": settings.orderflow.signal_threshold,
    }
    strategy = TiaBTCOrderFlowStrategy(of_params)

    engine = TickBacktestEngine(
        config=settings.backtest,
        risk_config=settings.spot,
        orderflow_config=settings.orderflow,
    )

    metrics = engine.run(strategy, args.aggtrade_file, symbol)
    print(metrics)


def cmd_balance() -> None:
    from bot.config.settings import Settings
    from bot.exchange.binance_native_client import BinanceClient
    from bot.logging_config.logger import setup_logging

    settings = Settings.load()
    setup_logging(level=settings.logging.level)

    exchange = BinanceClient(settings.exchange)
    balance = exchange.get_balance()

    # 查詢各幣種 USDT 價格
    usdt_values = {}
    total_usdt = 0.0
    for currency, amount in sorted(balance.items()):
        # 去掉 LD 前綴（Binance Earn 資產）
        base = currency[2:] if currency.startswith("LD") else currency
        if base in ("USDT", "USDC", "BUSD", "FDUSD"):
            usdt_values[currency] = amount
            total_usdt += amount
        else:
            try:
                ticker = exchange.get_ticker(f"{base}/USDT")
                usd_val = amount * ticker["last"]
                usdt_values[currency] = usd_val
                total_usdt += usd_val
            except Exception:
                usdt_values[currency] = None

    print(f"\n帳戶餘額:  (總計 ≈ {total_usdt:,.2f} USDT)")
    print("-" * 55)
    for currency, amount in sorted(balance.items()):
        usd_val = usdt_values.get(currency)
        if usd_val is not None:
            print(f"  {currency:<10s} {amount:>15.8f}  ≈ {usd_val:>10.2f} USDT")
        else:
            print(f"  {currency:<10s} {amount:>15.8f}  ≈       N/A")
    print("-" * 55)


def _ltv_risk_label(ltv: float) -> str:
    """根據 LTV 回傳風險等級標籤。"""
    if ltv >= 0.83:
        return "[!!!  清算中  !!!]"
    elif ltv >= 0.75:
        return "[!!!   危險   !!!]"
    elif ltv >= 0.65:
        return "[!    警告     !]"
    elif ltv >= 0.50:
        return "[     注意      ]"
    else:
        return "[     安全      ]"


def _format_loan_orders(orders: list[dict]) -> None:
    """格式化顯示借款訂單。"""
    # 按 LTV 由高到低排序
    orders_sorted = sorted(orders, key=lambda o: float(o.get("currentLTV", 0)), reverse=True)

    total_debt = sum(float(o.get("totalDebt", 0)) for o in orders_sorted)
    print(f"\n進行中的借款: {len(orders_sorted)} 筆，總借入 ≈ {total_debt:,.2f} USDT")
    print("=" * 70)

    for o in orders_sorted:
        loan_coin = o.get("loanCoin", "?")
        debt = float(o.get("totalDebt", 0))
        interest = float(o.get("residualInterest", 0))
        collateral_coin = o.get("collateralCoin", "?")
        collateral_amt = float(o.get("collateralAmount", 0))
        ltv = float(o.get("currentLTV", 0))
        risk = _ltv_risk_label(ltv)

        # 計算距離 margin call (75%) 和清算 (83%) 的緩衝
        buffer_margin = max(0, (0.75 - ltv) / ltv * 100) if ltv > 0 else 0
        buffer_liq = max(0, (0.83 - ltv) / ltv * 100) if ltv > 0 else 0

        print(f"  {collateral_coin} 抵押  →  借 {debt:,.2f} {loan_coin}")
        print(f"    LTV:    {ltv:.1%}  {risk}")
        print(f"    抵押:   {collateral_amt:.8f} {collateral_coin}")
        if interest > 0:
            print(f"    利息:   {interest:.8f} {loan_coin}")
        if ltv >= 0.65:
            print(f"    距 Margin Call (75%): 抵押物還能跌 {buffer_margin:.1f}%")
            print(f"    距 清算 (83%):        抵押物還能跌 {buffer_liq:.1f}%")
        print("-" * 70)


def cmd_loan() -> None:
    from bot.config.settings import Settings
    from bot.exchange.binance_native_client import BinanceClient
    from bot.logging_config.logger import setup_logging

    settings = Settings.load()
    setup_logging(level=settings.logging.level)

    exchange = BinanceClient(settings.exchange)

    try:
        orders = exchange.fetch_loan_ongoing_orders()
    except Exception as e:
        print(f"\n查詢借款失敗: {e}")
        return

    if not orders:
        print("\n目前沒有進行中的借款。")
        return

    _format_loan_orders(orders)


def cmd_loan_guard(args) -> None:
    import json
    import signal as sig
    import time

    from bot.config.settings import Settings
    from bot.exchange.binance_native_client import BinanceClient
    from bot.llm.client import ClaudeCLIClient
    from bot.logging_config import get_logger
    from bot.logging_config.logger import setup_logging

    settings = Settings.load()
    setup_logging(level=settings.logging.level)
    logger = get_logger("loan_guard")

    exchange = BinanceClient(settings.exchange)
    llm_client = ClaudeCLIClient(settings.llm)

    target_ltv = args.warn
    danger_ltv = args.danger
    low_ltv = args.low
    interval = args.interval
    dry_run = args.dry_run

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    sig.signal(sig.SIGINT, _stop)

    mode_label = "[模擬] " if dry_run else ""
    print(f"\n{mode_label}Loan Guard 啟動")
    print(f"  目標閾值:   LTV = {target_ltv:.0%}")
    print(f"  危險閾值:   LTV >= {danger_ltv:.0%} → AI 審核 → 買入質押物")
    print(f"  低 LTV 閾值: LTV <= {low_ltv:.0%} → AI 審核 → 賣出質押物")
    print(f"  檢查間隔:   {interval} 秒")
    print(f"  Ctrl+C 停止\n")

    cycle = 0
    while running:
        cycle += 1
        try:
            orders = exchange.fetch_loan_ongoing_orders()
        except Exception as e:
            logger.error("查詢借款失敗: %s", e)
            time.sleep(interval)
            continue

        if not orders:
            logger.info("[第 %d 輪] 無進行中借款", cycle)
            time.sleep(interval)
            continue

        for o in orders:
            loan_coin = o.get("loanCoin", "?")
            collateral_coin = o.get("collateralCoin", "?")
            ltv = float(o.get("currentLTV", 0))
            debt = float(o.get("totalDebt", 0))
            collateral_amt = float(o.get("collateralAmount", 0))
            label = f"{collateral_coin}→{loan_coin}"

            if ltv >= danger_ltv:
                # 計算需增加多少質押物
                collateral_value = debt / ltv if ltv > 0 else 0
                target_value = debt / target_ltv
                additional_value = target_value - collateral_value

                if additional_value <= 0:
                    continue

                # 查質押幣現價
                pair = f"{collateral_coin}/USDT"
                try:
                    ticker = exchange.get_ticker(pair)
                    coin_price = ticker["last"]
                except Exception as e:
                    logger.error("無法取得 %s 報價: %s", pair, e)
                    continue

                additional_qty = additional_value / coin_price
                buy_cost = additional_qty * coin_price

                # 查可用餘額
                try:
                    balance = exchange.get_balance()
                    usdt_available = balance.get("USDT", 0.0)
                except Exception:
                    usdt_available = 0.0

                logger.warning(
                    "[第 %d 輪] %s LTV=%.1f%% >= %.0f%%！需增加 %.8f %s (≈%.2f USDT)",
                    cycle, label, ltv * 100, danger_ltv * 100,
                    additional_qty, collateral_coin, buy_cost,
                )

                # AI 審核
                summary = (
                    f"# 借款保護 — AI 審核請求\n\n"
                    f"## 現況\n"
                    f"- 借款: {debt:.2f} {loan_coin}\n"
                    f"- 質押: {collateral_amt:.8f} {collateral_coin} (≈ {collateral_value:.2f} USDT)\n"
                    f"- 當前 LTV: {ltv:.1%}（危險閾值: {danger_ltv:.0%}）\n"
                    f"- {collateral_coin} 現價: {coin_price:.4f} USDT\n\n"
                    f"## 提議操作\n"
                    f"1. 市價買入 {additional_qty:.8f} {collateral_coin} (≈ {buy_cost:.2f} USDT)\n"
                    f"2. 將買入的 {collateral_coin} 追加為質押物\n"
                    f"3. 預期 LTV 降至 ≈ {target_ltv:.0%}\n\n"
                    f"## 帳戶狀態\n"
                    f"- 可用 USDT: {usdt_available:.2f}\n"
                    f"- 買入所需: {buy_cost:.2f} USDT\n"
                    f"- 餘額{'充足' if usdt_available >= buy_cost else '不足！'}\n\n"
                    f"## 請回覆 JSON\n"
                    f'{{"approved": true/false, "reason": "理由"}}\n'
                    f"只回覆 JSON，不要其他文字。\n"
                )

                try:
                    ai_response = llm_client.call_sync(summary)
                    logger.info("AI 回覆: %s", ai_response[:200])
                except Exception as e:
                    logger.error("AI 審核失敗: %s，不執行操作", e)
                    continue

                # 解析
                try:
                    text = ai_response.strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    decision = json.loads(text)
                except (json.JSONDecodeError, IndexError):
                    logger.warning("AI 回覆非 JSON，視為拒絕: %s", ai_response[:100])
                    continue

                if not decision.get("approved", False):
                    logger.info("AI 拒絕: %s", decision.get("reason", "無理由"))
                    continue

                logger.info("AI 同意: %s", decision.get("reason", ""))

                if dry_run:
                    logger.info("[模擬] 將買入 %.8f %s 並追加質押（未實際執行）",
                                additional_qty, collateral_coin)
                else:
                    # 買入
                    try:
                        buy_order = exchange.place_market_order(pair, "buy", additional_qty)
                        filled_qty = buy_order.get("filled", additional_qty)
                        logger.info("已買入 %.8f %s", filled_qty, collateral_coin)
                    except Exception as e:
                        logger.error("買入 %s 失敗: %s", collateral_coin, e)
                        continue

                    # 追加質押
                    try:
                        exchange.loan_adjust_ltv(
                            loan_coin, collateral_coin, filled_qty, "ADDITIONAL"
                        )
                        logger.info("已追加質押 %.8f %s", filled_qty, collateral_coin)
                    except Exception as e:
                        logger.error("追加質押失敗: %s（幣留在現貨）", e)

            elif ltv >= target_ltv:
                logger.warning(
                    "[第 %d 輪] %s LTV=%.1f%% 接近危險，持續監控",
                    cycle, label, ltv * 100,
                )

            elif ltv <= low_ltv:
                # ── 低 LTV 獲利了結 ──
                collateral_value = debt / ltv if ltv > 0 else 0
                target_value = debt / target_ltv
                removable_value = collateral_value - target_value

                if removable_value <= 0:
                    continue

                pair = f"{collateral_coin}/USDT"
                try:
                    ticker = exchange.get_ticker(pair)
                    coin_price = ticker["last"]
                except Exception as e:
                    logger.error("無法取得 %s 報價: %s", pair, e)
                    continue

                removable_qty = removable_value / coin_price
                sell_revenue = removable_qty * coin_price
                new_collateral_value = collateral_value - removable_value
                expected_ltv = debt / new_collateral_value if new_collateral_value > 0 else 1.0

                logger.info(
                    "[第 %d 輪] %s LTV=%.1f%% <= %.0f%%，可減少 %.8f %s (≈%.2f USDT)",
                    cycle, label, ltv * 100, low_ltv * 100,
                    removable_qty, collateral_coin, sell_revenue,
                )

                # AI 審核
                summary = (
                    f"# 借款獲利了結 — AI 審核請求\n\n"
                    f"## 現況\n"
                    f"- 借款: {debt:.2f} {loan_coin}\n"
                    f"- 質押: {collateral_amt:.8f} {collateral_coin} (≈ {collateral_value:.2f} USDT)\n"
                    f"- 當前 LTV: {ltv:.1%}（低 LTV 閾值: {low_ltv:.0%}）\n"
                    f"- {collateral_coin} 現價: {coin_price:.4f} USDT\n\n"
                    f"## 分析\n"
                    f"- LTV 偏低代表質押物大幅升值，可取回部分獲利\n"
                    f"- 目標 LTV: {target_ltv:.0%}\n\n"
                    f"## 提議操作\n"
                    f"1. 減少質押 {removable_qty:.8f} {collateral_coin} (≈ {sell_revenue:.2f} USDT)\n"
                    f"2. 市價賣出取回的 {collateral_coin}\n"
                    f"3. 預期 LTV 從 {ltv:.1%} 升至 ≈ {expected_ltv:.1%}\n\n"
                    f"## 請回覆 JSON\n"
                    f'{{"approved": true/false, "reason": "理由"}}\n'
                    f"只回覆 JSON，不要其他文字。\n"
                )

                try:
                    ai_response = llm_client.call_sync(summary)
                    logger.info("AI 回覆: %s", ai_response[:200])
                except Exception as e:
                    logger.error("AI 審核失敗: %s，不執行操作", e)
                    continue

                try:
                    text = ai_response.strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    decision = json.loads(text)
                except (json.JSONDecodeError, IndexError):
                    logger.warning("AI 回覆非 JSON，視為拒絕: %s", ai_response[:100])
                    continue

                if not decision.get("approved", False):
                    logger.info("AI 拒絕: %s", decision.get("reason", "無理由"))
                    continue

                logger.info("AI 同意獲利了結: %s", decision.get("reason", ""))

                if dry_run:
                    logger.info("[模擬] 將減少質押 %.8f %s 並賣出（未實際執行）",
                                removable_qty, collateral_coin)
                else:
                    # 減少質押
                    try:
                        exchange.loan_adjust_ltv(
                            loan_coin, collateral_coin, removable_qty, "REDUCED"
                        )
                        logger.info("已減少質押 %.8f %s", removable_qty, collateral_coin)
                    except Exception as e:
                        logger.error("減少質押失敗: %s", e)
                        continue

                    # 賣出
                    try:
                        sell_order = exchange.place_market_order(pair, "sell", removable_qty)
                        filled_qty = sell_order.get("filled", removable_qty)
                        logger.info("已賣出 %.8f %s", filled_qty, collateral_coin)
                    except Exception as e:
                        logger.error("賣出 %s 失敗: %s（幣留在現貨）", collateral_coin, e)

            else:
                logger.info("[第 %d 輪] %s LTV=%.1f%% 安全", cycle, label, ltv * 100)

        if running:
            time.sleep(interval)

    print("\nLoan Guard 已停止。")


def cmd_validate() -> None:
    from bot.config.settings import Settings
    from bot.logging_config.logger import setup_logging

    try:
        settings = Settings.load()
        setup_logging(level="INFO")
        print("配置驗證通過!")
        print(f"  交易模式: {settings.spot.mode.value}")
        print(f"  交易對:   {', '.join(settings.spot.pairs)}")
        print(f"  時間框架: {settings.spot.timeframe}")
        print(f"  策略:     {settings.strategy.name}")
        print(f"  LLM:      {'啟用' if settings.llm.enabled else '停用'}")
        print(f"  測試網:   {settings.exchange.testnet}")
    except Exception as e:
        print(f"配置驗證失敗: {e}")
        sys.exit(1)


def cmd_config_push(args) -> None:
    import yaml

    from dotenv import load_dotenv
    load_dotenv()

    from bot.db.supabase_client import SupabaseWriter

    config_path = args.config or "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_json = yaml.safe_load(f)
    except Exception as e:
        print(f"讀取 {config_path} 失敗: {e}")
        sys.exit(1)

    db = SupabaseWriter()
    if not db.enabled:
        print("Supabase 未連線，無法推送")
        sys.exit(1)

    # 取得目前最新版本號
    try:
        resp = (
            db._client.table("bot_config")
            .select("version")
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        current_version = resp.data[0]["version"] if resp.data else 0
    except Exception:
        current_version = 0

    new_version = current_version + 1
    note = args.note or f"config-push from CLI"

    try:
        db._client.table("bot_config").insert({
            "version": new_version,
            "config_json": config_json,
            "changed_by": "cli",
            "change_note": note,
        }).execute()
        print(f"已推送配置 v{new_version} 到 Supabase")
        print(f"  變更說明: {note}")
    except Exception as e:
        print(f"推送失敗: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
