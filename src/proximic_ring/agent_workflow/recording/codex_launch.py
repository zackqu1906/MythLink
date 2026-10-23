"""Open one existing Codex conversation; never submit input or create a thread."""
import argparse
import os
import subprocess
import sys

from .codex_link import codex_thread_url


def open_codex_thread(thread_id, *, opener=None):
    url = codex_thread_url(thread_id)
    if not url:
        raise ValueError("Codex 会话 ID 无效")
    if opener is not None:
        if not opener(url):
            raise RuntimeError("无法打开 Codex 会话，请确认已安装 Codex")
        return url
    try:
        if sys.platform == "win32":
            os.startfile(url)
        else:
            command = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([command, url], check=True, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("无法打开 Codex 会话，请确认已安装 Codex") from exc
    return url


def main(argv=None):
    parser = argparse.ArgumentParser(description="打开指定 Codex 会话，不创建新对话或发送消息")
    parser.add_argument("--thread-id", required=True, help="原会话的真实 UUID")
    parser.add_argument("--print-url", action="store_true", help="仅输出跳转链接，不打开应用")
    args = parser.parse_args(argv)
    try:
        url = open_codex_thread(args.thread_id, opener=(lambda _: True) if args.print_url else None)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
