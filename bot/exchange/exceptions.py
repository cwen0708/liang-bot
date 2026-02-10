"""交易所自訂例外。"""


class ExchangeError(Exception):
    """交易所通用錯誤。"""


class InsufficientBalanceError(ExchangeError):
    """餘額不足。"""


class OrderError(ExchangeError):
    """下單失敗。"""


class RateLimitError(ExchangeError):
    """API 頻率限制。"""


class AuthenticationError(ExchangeError):
    """API 認證失敗。"""
