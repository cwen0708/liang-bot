"""集中式日誌工廠。"""

import logging
import sys
from pathlib import Path

import colorlog

from bot.config.settings import PROJECT_ROOT

_configured = False


def setup_logging(level: str = "INFO", file_enabled: bool = True, log_dir: str = "data/logs") -> None:
    """初始化全域日誌設定。"""
    global _configured
    if _configured:
        return

    root_logger = logging.getLogger("bot")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler（彩色）
    console_handler = colorlog.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s [%(levelname)-8s]%(reset)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    root_logger.addHandler(console_handler)

    # File handler
    if file_enabled:
        log_path = PROJECT_ROOT / log_dir
        log_path.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(
            log_path / "bot.log", encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """取得具名 logger。使用方式: logger = get_logger(__name__)"""
    return logging.getLogger(f"bot.{name}")
