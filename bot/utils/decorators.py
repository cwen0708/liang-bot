"""裝飾器工具。"""

import time
import functools
import logging

logger = logging.getLogger("bot.utils")


def retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    no_retry_on: tuple[type[Exception], ...] = (),
):
    """指數退避重試裝飾器。

    Args:
        max_retries: 最大重試次數
        delay: 初始延遲秒數
        backoff: 退避倍數
        no_retry_on: 不重試的例外類型（例如認證錯誤）
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # 指定的例外類型直接拋出，不重試
                    if no_retry_on and isinstance(e, no_retry_on):
                        raise
                    if attempt < max_retries:
                        logger.warning(
                            "%s 失敗 (嘗試 %d/%d): %s，%0.1f 秒後重試",
                            func.__name__, attempt + 1, max_retries, e, current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff

            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
