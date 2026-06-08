from __future__ import annotations

import json
import subprocess
import sys
from io import TextIOWrapper
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# One bot subprocess per profile slug, each with its own stdout/stderr log file
# and its own KILL file under logs/<slug>/.
_processes: dict[str, subprocess.Popen[bytes]] = {}
_log_fhs: dict[str, TextIOWrapper] = {}

# Slugs the operator wants running. Persisted so an API/service restart can
# relaunch the bots it was supervising (the child subprocesses die with the
# parent on a systemd restart). An intentional stop removes the slug; a crash
# leaves it so status surfaces "was running, now stopped".
_DESIRED_PATH = PROJECT_ROOT / "logs" / "running_bots.json"


def _log_dir(slug: str) -> Path:
    return PROJECT_ROOT / "logs" / slug


def _stderr_path(slug: str) -> Path:
    return _log_dir(slug) / "bot_stderr.log"


def _kill_path(slug: str) -> Path:
    return _log_dir(slug) / "KILL"


# ── desired-state persistence ───────────────────────────────────────────────

def _load_desired() -> set[str]:
    if not _DESIRED_PATH.exists():
        return set()
    try:
        return set(json.loads(_DESIRED_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_desired(slugs: set[str]) -> None:
    try:
        _DESIRED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DESIRED_PATH.write_text(json.dumps(sorted(slugs), indent=2), encoding="utf-8")
    except OSError:
        pass


def _add_desired(slug: str) -> None:
    d = _load_desired()
    if slug not in d:
        d.add(slug)
        _save_desired(d)


def _remove_desired(slug: str) -> None:
    d = _load_desired()
    if slug in d:
        d.discard(slug)
        _save_desired(d)


def start(slug: str) -> dict[str, object]:
    if is_running(slug):
        return {"ok": False, "error": f"Bot for '{slug}' is already running."}

    # Remove this profile's kill file so the bot doesn't exit immediately.
    kill_path = _kill_path(slug)
    if kill_path.exists():
        kill_path.unlink()

    try:
        _log_dir(slug).mkdir(parents=True, exist_ok=True)
        # line-buffered
        log_fh = open(_stderr_path(slug), "w", buffering=1)  # noqa: SIM115
        process = subprocess.Popen(
            [sys.executable, "-u", "main.py", "--profile", slug],
            cwd=str(PROJECT_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        fh = _log_fhs.pop(slug, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        return {"ok": False, "error": str(exc)}

    _processes[slug] = process
    _log_fhs[slug] = log_fh
    _add_desired(slug)
    return {"ok": True, "pid": process.pid}


def stop(slug: str) -> dict[str, object]:
    """Graceful stop: create the profile's KILL file so the bot flattens (stocks)
    or exits cleanly before terminating."""
    if not is_running(slug):
        return {"ok": False, "error": f"Bot for '{slug}' is not running."}

    kill_path = _kill_path(slug)
    kill_path.touch()
    process = _processes.get(slug)
    try:
        assert process is not None
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        assert process is not None
        process.terminate()
        process.wait(timeout=5)

    _cleanup(slug)
    _remove_desired(slug)   # intentional stop → don't relaunch on next API startup
    if kill_path.exists():
        kill_path.unlink()
    return {"ok": True}


def stop_all() -> dict[str, object]:
    for slug in running_slugs():
        stop(slug)
    return {"ok": True}


def is_running(slug: str) -> bool:
    process = _processes.get(slug)
    if process is None:
        return False
    if process.poll() is not None:
        _cleanup(slug)
        return False
    return True


def running_slugs() -> list[str]:
    return [slug for slug in list(_processes.keys()) if is_running(slug)]


def get_pid(slug: str) -> int | None:
    if is_running(slug):
        process = _processes.get(slug)
        return process.pid if process is not None else None
    return None


def status_map() -> dict[str, dict[str, object]]:
    """Running state for every slug we have launched this session or that is
    persisted as desired-running. A desired slug that isn't running is flagged
    ``stopped_unexpectedly`` so the operator sees a bot that died/was lost."""
    desired = _load_desired()
    slugs = set(_processes.keys()) | desired
    out: dict[str, dict[str, object]] = {}
    for slug in slugs:
        running = is_running(slug)
        out[slug] = {
            "running": running,
            "pid": get_pid(slug),
            "desired": slug in desired,
            "stopped_unexpectedly": (slug in desired) and not running,
        }
    return out


def relaunch_persisted() -> dict[str, object]:
    """Relaunch bots that were running before an API/service restart. Skips any
    that are already running or have a KILL file present (operator stopped them).
    Paper-mode safety is unchanged — each bot is just ``main.py --profile`` and
    the broker still enforces the live-trading confirmation."""
    relaunched: list[str] = []
    skipped: list[str] = []
    for slug in sorted(_load_desired()):
        if is_running(slug):
            continue
        if _kill_path(slug).exists():
            skipped.append(slug)
            continue
        result = start(slug)
        (relaunched if result.get("ok") else skipped).append(slug)
    return {"ok": True, "relaunched": relaunched, "skipped": skipped}


def get_stderr_log(slug: str, max_lines: int = 80) -> str:
    """Return the last N lines of the bot's stdout/stderr log."""
    path = _stderr_path(slug)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


# ── internal ──────────────────────────────────────────────────────────────────

def _cleanup(slug: str) -> None:
    _processes.pop(slug, None)
    fh = _log_fhs.pop(slug, None)
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass
