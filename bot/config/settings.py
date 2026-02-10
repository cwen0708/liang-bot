"""配置管理 — 載入 .env 和 config.yaml，合併為型別安全的 dataclass。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bot.config.constants import VALID_TIMEFRAMES, TradingMode

# 專案根目錄
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class ExchangeConfig:
    api_key: str
    api_secret: str
    testnet: bool = True


@dataclass(frozen=True)
class TradingConfig:
    mode: TradingMode = TradingMode.PAPER
    pairs: tuple[str, ...] = ("BTC/USDT",)
    timeframe: str = "1h"
    check_interval_seconds: int = 60


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "sma_crossover"
    params: dict = field(default_factory=lambda: {"fast_period": 10, "slow_period": 30})


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.02
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06
    max_open_positions: int = 3
    max_daily_loss_pct: float = 0.05


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2024-01-01"
    end_date: str = "2025-01-01"
    initial_balance: float = 10000.0
    commission_pct: float = 0.001


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file_enabled: bool = True
    log_dir: str = "data/logs"


@dataclass(frozen=True)
class OrderFlowConfig:
    """訂單流策略配置。"""
    bar_interval_seconds: int = 60
    tick_size: float = 0.01
    cvd_lookback: int = 500
    zscore_lookback: int = 20
    divergence_peak_order: int = 5
    sfp_swing_lookback: int = 5
    absorption_lookback: int = 10
    signal_threshold: float = 0.5


@dataclass(frozen=True)
class LLMConfig:
    """LLM 決策引擎配置。"""
    enabled: bool = True
    cli_path: str = "claude"
    model: str = "claude-sonnet-4-5-20250929"
    timeout: int = 60
    min_confidence: float = 0.3
    fallback: str = "weighted_vote"
    fallback_weights: dict = field(default_factory=lambda: {
        "sma_crossover": 0.3,
        "tia_orderflow": 0.7,
    })


@dataclass(frozen=True)
class LoanGuardConfig:
    """借貸再平衡配置。"""
    enabled: bool = False
    target_ltv: float = 0.65
    danger_ltv: float = 0.75
    low_ltv: float = 0.40
    dry_run: bool = True


@dataclass(frozen=True)
class StrategiesConfig:
    """多策略配置。"""
    strategies: list[dict] = field(default_factory=lambda: [
        {"name": "sma_crossover", "params": {"fast_period": 10, "slow_period": 30}},
    ])


@dataclass(frozen=True)
class Settings:
    exchange: ExchangeConfig
    trading: TradingConfig
    strategy: StrategyConfig
    risk: RiskConfig
    backtest: BacktestConfig
    logging: LoggingConfig
    orderflow: OrderFlowConfig = field(default_factory=OrderFlowConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    strategies_config: StrategiesConfig = field(default_factory=StrategiesConfig)
    loan_guard: LoanGuardConfig = field(default_factory=LoanGuardConfig)

    def __repr__(self) -> str:
        return (
            f"Settings(exchange=ExchangeConfig(api_key='***', api_secret='***', "
            f"testnet={self.exchange.testnet}), ...)"
        )

    @classmethod
    def from_dict(cls, cfg: dict, current: "Settings") -> "Settings":
        """從 dict（Supabase config_json）建立新 Settings，保留 exchange 不變。"""
        return cls(
            exchange=current.exchange,  # API 金鑰不從 DB 載入
            trading=cls._load_trading(cfg.get("trading", {})),
            strategy=cls._load_strategy(cfg.get("strategy", {})),
            risk=cls._load_risk(cfg.get("risk", {})),
            backtest=current.backtest,
            logging=cls._load_logging(cfg.get("logging", {})),
            orderflow=cls._load_orderflow(cfg.get("orderflow", {})),
            llm=cls._load_llm(cfg.get("llm", {})),
            strategies_config=cls._load_strategies_config(cfg.get("strategies", [])),
            loan_guard=cls._load_loan_guard(cfg.get("loan_guard", {})),
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Settings":
        """載入 .env 環境變數和 config.yaml 配置檔。"""
        load_dotenv(PROJECT_ROOT / ".env")

        if config_path is None:
            config_path = PROJECT_ROOT / "config.yaml"
        config_path = Path(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        exchange = cls._load_exchange()
        trading = cls._load_trading(cfg.get("trading", {}))
        strategy = cls._load_strategy(cfg.get("strategy", {}))
        risk = cls._load_risk(cfg.get("risk", {}))
        backtest = cls._load_backtest(cfg.get("backtest", {}))
        logging_cfg = cls._load_logging(cfg.get("logging", {}))
        orderflow = cls._load_orderflow(cfg.get("orderflow", {}))
        llm = cls._load_llm(cfg.get("llm", {}))
        strategies_config = cls._load_strategies_config(cfg.get("strategies", []))
        loan_guard = cls._load_loan_guard(cfg.get("loan_guard", {}))

        return cls(
            exchange=exchange,
            trading=trading,
            strategy=strategy,
            risk=risk,
            backtest=backtest,
            logging=logging_cfg,
            orderflow=orderflow,
            llm=llm,
            strategies_config=strategies_config,
            loan_guard=loan_guard,
        )

    @staticmethod
    def _load_exchange() -> ExchangeConfig:
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        testnet = os.getenv("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")

        if not api_key or not api_secret:
            raise ValueError(
                "BINANCE_API_KEY 和 BINANCE_API_SECRET 必須在 .env 檔案中設定。"
                "請參考 .env.example。"
            )

        return ExchangeConfig(api_key=api_key, api_secret=api_secret, testnet=testnet)

    @staticmethod
    def _load_trading(cfg: dict) -> TradingConfig:
        mode = TradingMode(cfg.get("mode", "paper"))
        pairs = tuple(cfg.get("pairs", ["BTC/USDT"]))
        timeframe = cfg.get("timeframe", "1h")

        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f"不支援的時間框架: {timeframe}，有效值: {VALID_TIMEFRAMES}")

        return TradingConfig(
            mode=mode,
            pairs=pairs,
            timeframe=timeframe,
            check_interval_seconds=cfg.get("check_interval_seconds", 60),
        )

    @staticmethod
    def _load_strategy(cfg: dict) -> StrategyConfig:
        return StrategyConfig(
            name=cfg.get("name", "sma_crossover"),
            params=cfg.get("params", {"fast_period": 10, "slow_period": 30}),
        )

    @staticmethod
    def _load_risk(cfg: dict) -> RiskConfig:
        return RiskConfig(
            max_position_pct=cfg.get("max_position_pct", 0.02),
            stop_loss_pct=cfg.get("stop_loss_pct", 0.03),
            take_profit_pct=cfg.get("take_profit_pct", 0.06),
            max_open_positions=cfg.get("max_open_positions", 3),
            max_daily_loss_pct=cfg.get("max_daily_loss_pct", 0.05),
        )

    @staticmethod
    def _load_backtest(cfg: dict) -> BacktestConfig:
        return BacktestConfig(
            start_date=cfg.get("start_date", "2024-01-01"),
            end_date=cfg.get("end_date", "2025-01-01"),
            initial_balance=cfg.get("initial_balance", 10000.0),
            commission_pct=cfg.get("commission_pct", 0.001),
        )

    @staticmethod
    def _load_logging(cfg: dict) -> LoggingConfig:
        return LoggingConfig(
            level=cfg.get("level", "INFO"),
            file_enabled=cfg.get("file_enabled", True),
            log_dir=cfg.get("log_dir", "data/logs"),
        )

    @staticmethod
    def _load_orderflow(cfg: dict) -> "OrderFlowConfig":
        return OrderFlowConfig(
            bar_interval_seconds=cfg.get("bar_interval_seconds", 60),
            tick_size=cfg.get("tick_size", 0.01),
            cvd_lookback=cfg.get("cvd_lookback", 500),
            zscore_lookback=cfg.get("zscore_lookback", 20),
            divergence_peak_order=cfg.get("divergence_peak_order", 5),
            sfp_swing_lookback=cfg.get("sfp_swing_lookback", 5),
            absorption_lookback=cfg.get("absorption_lookback", 10),
            signal_threshold=cfg.get("signal_threshold", 0.5),
        )

    @staticmethod
    def _load_llm(cfg: dict) -> "LLMConfig":
        return LLMConfig(
            enabled=cfg.get("enabled", True),
            cli_path=cfg.get("cli_path", "claude"),
            model=cfg.get("model", "claude-sonnet-4-5-20250929"),
            timeout=cfg.get("timeout", 60),
            min_confidence=cfg.get("min_confidence", 0.3),
            fallback=cfg.get("fallback", "weighted_vote"),
            fallback_weights=cfg.get("fallback_weights", {
                "sma_crossover": 0.3,
                "tia_orderflow": 0.7,
            }),
        )

    @staticmethod
    def _load_strategies_config(cfg: list) -> "StrategiesConfig":
        if not cfg:
            return StrategiesConfig()
        return StrategiesConfig(strategies=cfg)

    @staticmethod
    def _load_loan_guard(cfg: dict) -> "LoanGuardConfig":
        return LoanGuardConfig(
            enabled=cfg.get("enabled", False),
            target_ltv=cfg.get("target_ltv", 0.65),
            danger_ltv=cfg.get("danger_ltv", 0.75),
            low_ltv=cfg.get("low_ltv", 0.40),
            dry_run=cfg.get("dry_run", True),
        )
