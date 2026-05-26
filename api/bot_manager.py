from __future__ import annotations

import subprocess
import sys
from io import TextIOWrapper
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
_LOG_PATH = PROJECT_ROOT / "logs" / "bot_stderr.log"

_process: subprocess.Popen[bytes] | None = None
_log_fh: TextIOWrapper | None = None


def start() -> dict[str, object]:
    global _process, _log_fh
    if is_running():
        return {"ok": False, "error": "Bot is already running."}

    # Remove kill switch file so the bot doesn't exit immediately
    kill_path = PROJECT_ROOT / "KILL"
    if kill_path.exists():
        kill_path.unlink()

    # Ensure logs directory exists
    _LOG_PATH.parent.mkdir(exist_ok=True)

    # Open a fresh log file for this run (overwrites the previous crash log)
    _log_fh = open(_LOG_PATH, "w", buffering=1)  # line-buffered

    try:
        _process = subprocess.Popen(
            [sys.executable, "-u", "main.py"],  # -u = unbuffered stdout
            cwd=str(PROJECT_ROOT),
            stdout=_log_fh,
            stderr=subprocess.STDOUT,  # merge stderr into the same file
        )
    except Exception as exc:
        _log_fh.close()
        _log_fh = None
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "pid": _process.pid}


def stop() -> dict[str, object]:
    """Graceful stop: create KILL file so the bot flattens positions before exiting."""
    global _process, _log_fh
    if not is_running():
        return {"ok": False, "error": "Bot is not running."}

    kill_path = PROJECT_ROOT / "KILL"
    kill_path.touch()
    try:
        assert _process is not None
        _process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        assert _process is not None
        _process.terminate()
        _process.wait(timeout=5)

    _cleanup()
    if kill_path.exists():
        kill_path.unlink()
    return {"ok": True}


def is_running() -> bool:
    global _process
    if _process is None:
        return False
    if _process.poll() is not None:
        _cleanup()
        return False
    return True


def get_pid() -> int | None:
    if _process is not None and is_running():
        return _process.pid
    return None


def get_stderr_log(max_lines: int = 80) -> str:
    """Return the last N lines of the bot's stdout/stderr log."""
    if not _LOG_PATH.exists():
        return ""
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


# ── internal ──────────────────────────────────────────────────────────────────

def _cleanup() -> None:
    global _process, _log_fh
    _process = None
    if _log_fh is not None:
        try:
            _log_fh.close()
        except OSError:
            pass
        _log_fh = None
