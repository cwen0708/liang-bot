"""重啟 Bot — 找到現有 bot 進程，殺掉後重新啟動。"""

import os
import subprocess
import sys
import time

import psutil


def find_bot_pid() -> int | None:
    """找到 `python -m bot run` 的 PID（排除自身）。

    精確比對 cmdline 列表，避免誤殺 bash 包裝進程。
    預期 cmdline: ['...python.exe', '-m', 'bot', 'run']
    """
    my_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == my_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            if len(cmdline) < 4:
                continue
            # cmdline[0] 是 python 路徑，後面必須是 ['-m', 'bot', 'run']
            if cmdline[-3:] == ["-m", "bot", "run"]:
                name = proc.info.get("name", "").lower()
                if name in ("python.exe", "python3.exe", "python", "python3"):
                    return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def main():
    pid = find_bot_pid()
    if pid:
        print(f"找到 Bot 進程 PID={pid}，正在終止...")
        os.kill(pid, 9)
        time.sleep(1)
        print("已終止。")
    else:
        print("未找到運行中的 Bot。")

    print("啟動 Bot...")
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    subprocess.Popen(
        [sys.executable, "-m", "bot", "run"],
        cwd=bot_dir,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=open(os.path.join(bot_dir, "data", "logs", "bot_stdout.log"), "a"),
        stderr=subprocess.STDOUT,
    )
    time.sleep(2)

    new_pid = find_bot_pid()
    if new_pid:
        print(f"Bot 已啟動，PID={new_pid}")
    else:
        print("警告：Bot 可能未成功啟動，請檢查 data/logs/")


if __name__ == "__main__":
    main()
