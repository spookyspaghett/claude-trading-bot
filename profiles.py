from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from config_loader import Config, load_config

PROJECT_ROOT = Path(__file__).parent
PROFILES_DIR = PROJECT_ROOT / "profiles"
ACTIVE_FILE = PROFILES_DIR / "active.txt"
LEGACY_CONFIG = PROJECT_ROOT / "config.yaml"

# Fields persisted in a profile YAML (everything needed to build a Config + a name).
_PROFILE_KEYS = (
    "name", "asset_class", "live", "symbols",
    "risk", "strategy", "ai",
    "alpaca_api_key", "alpaca_secret_key",
)


# ── helpers ────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


def _clean_for_yaml(obj: Any) -> Any:
    """Recursively convert Decimals to floats so PyYAML emits plain scalars."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _clean_for_yaml(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_for_yaml(v) for v in obj]
    return obj


def _ensure_dir() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


# ── active-profile pointer ──────────────────────────────────────────────────────

def get_active_slug() -> str | None:
    if ACTIVE_FILE.exists():
        slug = ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if slug and (PROFILES_DIR / f"{slug}.yaml").exists():
            return slug
    return None


def set_active_slug(slug: str) -> None:
    _ensure_dir()
    if not (PROFILES_DIR / f"{slug}.yaml").exists():
        raise ValueError(f"Profile {slug!r} does not exist.")
    ACTIVE_FILE.write_text(slug, encoding="utf-8")


# ── CRUD ────────────────────────────────────────────────────────────────────────

def _profile_path(slug: str) -> Path:
    return PROFILES_DIR / f"{slug}.yaml"


def load_profile(slug: str) -> dict[str, Any]:
    path = _profile_path(slug)
    if not path.exists():
        raise ValueError(f"Profile {slug!r} not found.")
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def save_profile(slug: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    payload = {k: data[k] for k in _PROFILE_KEYS if k in data}
    payload.setdefault("name", slug)
    payload.setdefault("asset_class", "stock")
    _profile_path(slug).write_text(
        yaml.dump(
            _clean_for_yaml(payload),
            default_flow_style=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def delete_profile(slug: str) -> None:
    path = _profile_path(slug)
    if path.exists():
        path.unlink()
    if get_active_slug() == slug:
        ACTIVE_FILE.unlink(missing_ok=True)


def list_profiles() -> list[dict[str, Any]]:
    """Return profile summaries (no secrets), active flag first."""
    migrate_legacy()
    active = get_active_slug()
    out: list[dict[str, Any]] = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        slug = path.stem
        try:
            data = load_profile(slug)
        except Exception:
            continue
        out.append({
            "slug": slug,
            "name": data.get("name", slug),
            "asset_class": data.get("asset_class", "stock"),
            "live": bool(data.get("live", False)),
            "symbols": data.get("symbols", []),
            "strategy": (data.get("strategy") or {}).get("name", "orb"),
            "has_keys": bool(data.get("alpaca_api_key")),
            "active": slug == active,
        })
    return out


# ── Config construction ──────────────────────────────────────────────────────────

def load_active_config() -> Config:
    """Build a Config from the active profile (migrating the legacy setup if needed)."""
    migrate_legacy()
    slug = get_active_slug()
    if slug is None:
        # No profiles at all and nothing to migrate — fall back to legacy file.
        return load_config(LEGACY_CONFIG)
    data = load_profile(slug)
    return Config(**{k: v for k, v in data.items() if k != "name"})


# ── one-time migration of the pre-profiles setup ──────────────────────────────────

def migrate_legacy() -> None:
    """If no profiles exist yet, seed one from config.yaml + .env keys."""
    _ensure_dir()
    if any(PROFILES_DIR.glob("*.yaml")):
        return
    if not LEGACY_CONFIG.exists():
        return
    try:
        cfg = load_config(LEGACY_CONFIG)  # pulls keys from .env
    except Exception:
        return  # keys not configured yet — nothing to migrate
    data = cfg.model_dump()
    data["name"] = "Default (stocks)"
    slug = "default-stocks"
    save_profile(slug, data)
    set_active_slug(slug)
