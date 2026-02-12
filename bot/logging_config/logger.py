"""集中式日誌工廠。"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

import colorlog

from bot.config.settings import PROJECT_ROOT

if TYPE_CHECKING:
    from bot.db.supabase_client import SupabaseWriter

_configured = False

# ---------------------------------------------------------------------------
# 敏感資料遮罩 — 本地日誌（console + file）遮罩精確財務數字
# ---------------------------------------------------------------------------

# 遮罩 qty=0.00008932 → qty=***
_QTY_PATTERN = re.compile(r'(qty|quantity|filled|數量)[=:]\s*[\d.]+', re.IGNORECASE)
# 遮罩精確餘額 如 "餘額 12345.67" 或 "balance=12345.67" 或 "USDT 餘額: 12345.67"
_BALANCE_PATTERN = re.compile(
    r'(餘額|balance|available|可用)\s*[=:]\s*[\d,.]+', re.IGNORECASE
)


def _mask_sensitive(message: str) -> str:
    """遮罩日誌中的精確財務數據（qty、balance 等）。"""
    message = _QTY_PATTERN.sub(lambda m: m.group(1) + '=***', message)
    message = _BALANCE_PATTERN.sub(lambda m: m.group(1) + '=***', message)
    return message


class _MaskingFormatter(logging.Formatter):
    """在格式化後對訊息做敏感資料遮罩。"""

    def format(self, record: logging.LogRecord) -> str:
        return _mask_sensitive(super().format(record))


class _MaskingColoredFormatter(colorlog.ColoredFormatter):
    """彩色 + 遮罩的 Formatter（用於 console handler）。"""

    def format(self, record: logging.LogRecord) -> str:
        return _mask_sensitive(super().format(record))


def setup_logging(level: str = "INFO", file_enabled: bool = True, log_dir: str = "data/logs") -> None:
    """初始化全域日誌設定。"""
    global _configured
    if _configured:
        return

    root_logger = logging.getLogger("bot")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler（彩色 + 遮罩敏感資料）
    console_handler = colorlog.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        _MaskingColoredFormatter(
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

    # File handler（遮罩敏感資料）
    if file_enabled:
        log_path = PROJECT_ROOT / log_dir
        log_path.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path / "bot.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            _MaskingFormatter(
                "%(asctime)s [%(levelname)-8s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """取得具名 logger。使用方式: logger = get_logger(__name__)"""
    return logging.getLogger(f"bot.{name}")


# ---------------------------------------------------------------------------
# Supabase logging handler — 把 Python log 自動送到 bot_logs 表
# ---------------------------------------------------------------------------

class SupabaseLogHandler(logging.Handler):
    """將日誌透過 SupabaseWriter.insert_log 寫入 bot_logs 表。

    Supabase Dashboard 僅限本人存取（service_role key），
    保留完整財務數據方便遠端除錯。
    本地日誌（console + file）則由 _MaskingFormatter 遮罩敏感資料。
    """

    def __init__(self, writer: SupabaseWriter, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # module: 去掉 "bot." 前綴讓前端更好讀
            module = record.name
            if module.startswith("bot."):
                module = module[4:]
            self._writer.insert_log(
                level=record.levelname,
                module=module,
                message=self.format(record),
            )
        except Exception:
            pass  # 避免日誌寫入失敗導致遞迴錯誤


def attach_supabase_handler(writer: SupabaseWriter, level: int = logging.INFO) -> None:
    """建立 SupabaseLogHandler 並掛到 root bot logger。"""
    handler = SupabaseLogHandler(writer, level=level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("bot").addHandler(handler)
