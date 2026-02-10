"""Claude CLI subprocess 客戶端 — 參考 Claude-PM ClaudeExecutor 實作。"""

import asyncio
import json

from bot.config.settings import LLMConfig
from bot.logging_config import get_logger

logger = get_logger("llm.client")


class ClaudeCLIClient:
    """
    透過 Claude CLI (`claude -p`) 非交互模式呼叫 LLM。

    參考 Claude-PM/claude_pm/executor.py 的 ClaudeExecutor：
    - 使用 asyncio.create_subprocess_exec
    - --output-format json 取得結構化回傳
    - 設定超時機制
    """

    def __init__(self, config: LLMConfig) -> None:
        self.cli_path = config.cli_path
        self.model = config.model
        self.timeout = config.timeout

    async def call(self, prompt: str) -> str:
        """
        呼叫 Claude CLI 並回傳結果文字。

        Args:
            prompt: 完整提示詞（Markdown 格式）。

        Returns:
            LLM 回傳的文字內容。

        Raises:
            TimeoutError: 超時。
            RuntimeError: CLI 執行失敗。
        """
        cmd = [
            self.cli_path,
            "-p",
            prompt,
            "--output-format", "json",
            "--model", self.model,
        ]

        logger.debug("呼叫 Claude CLI: model=%s, timeout=%d", self.model, self.timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.error("Claude CLI 超時 (%d 秒)", self.timeout)
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise TimeoutError(f"Claude CLI 超時 ({self.timeout}s)")

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.error("Claude CLI 失敗 (rc=%d): %s", proc.returncode, stderr_text)
            raise RuntimeError(f"Claude CLI 失敗 (rc={proc.returncode}): {stderr_text}")

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()

        # 解析 JSON 輸出格式
        result_text = self._parse_output(stdout_text)
        logger.debug("Claude CLI 回傳 %d 字元", len(result_text))

        return result_text

    @staticmethod
    def _parse_output(raw: str) -> str:
        """解析 Claude CLI 的 JSON 輸出，提取 result 欄位。"""
        try:
            data = json.loads(raw)
            # Claude CLI --output-format json 的回傳格式
            if isinstance(data, dict):
                return data.get("result", raw)
            return raw
        except json.JSONDecodeError:
            # 非 JSON 格式，直接回傳原始文字
            return raw

    def call_sync(self, prompt: str) -> str:
        """同步版本的 call（在既有的同步程式碼中使用）。"""
        return asyncio.run(self.call(prompt))
