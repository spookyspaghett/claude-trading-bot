"""Import and export a profile's settings as CSV.

The dashboard's Config editor is the only way to change most settings, which
makes a set of tuned parameters impossible to back up, review, hand to someone
else, or restore onto a new machine. This module makes them a file.

Format - two columns, one setting per row::

    key,value
    asset_class,stock
    symbols,"SPY,QQQ,AAPL"
    strategy.name,vwap_revert
    strategy.vwap_revert.band_mult,2.25
    risk.risk_per_trade_pct,0.5
    risk.correlation_groups.index_beta,"SPY,QQQ,IWM"

A plain watchlist (a single ``symbol``/``ticker`` column, as exported by most
screeners) is also accepted and read as the symbol list.

Three properties matter more than the parsing:

**It merges, it never replaces.** A three-row CSV changes three settings. The
alternative - rebuilding the profile from the file - would reset every key the
file omits to a model default, which is how a Donchian block silently reverts
while you are importing Trend/SR parameters.

**Unknown keys are an error, not a no-op.** A typo in ``risk_per_trade_pct``
that quietly does nothing is worse than one that fails, because the operator
believes the risk setting took effect.

**``live`` and the Alpaca keys cannot be imported at all.** A file that can
switch a paper bot to real money, or move it to a different account, is not a
convenience. They are refused by name so a file containing them fails loudly
rather than appearing to work.
"""
from __future__ import annotations

import copy
import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from typing import Any, Literal, get_origin

from pydantic import BaseModel, ValidationError

from config_loader import Config

# ── Key namespace ─────────────────────────────────────────────────────────────
# Derived from the Config model rather than listed by hand: a new field on any
# strategy or on RiskConfig becomes importable the moment it is declared, with
# no second list to keep in sync. Every drift bug this file could have had is a
# drift bug it cannot have.


def _leaf_paths(model: type[BaseModel], prefix: str = "") -> dict[str, Any]:
    """Map ``dotted.path -> annotation`` for every scalar field under *model*."""
    out: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        ann = info.annotation
        # get_origin() guards the generics (list[str], dict[str, list[str]],
        # Literal[...]) that are not classes and must not be recursed into.
        if (
            get_origin(ann) is None
            and isinstance(ann, type)
            and issubclass(ann, BaseModel)
        ):
            out.update(_leaf_paths(ann, f"{path}."))
        else:
            out[path] = ann
    return out


SCHEMA: dict[str, Any] = _leaf_paths(Config)

#: Settings a CSV is not allowed to touch, whatever it contains.
BLOCKED: dict[str, str] = {
    "live": (
        "switching between paper and real money is a decision that needs a "
        "person, not a spreadsheet cell. Use the profile editor."
    ),
    "alpaca_api_key": "credentials are never read from or written to CSV.",
    "alpaca_secret_key": "credentials are never read from or written to CSV.",
}

#: ``name`` is the profile's display name - stored beside the Config, not in it.
_EXTRA_PATHS = ("name",)

#: Correlation groups are user-named, so the path tail is data, not schema.
_GROUPS = "risk.correlation_groups"
_GROUPS_PREFIX = f"{_GROUPS}."

IMPORTABLE: frozenset[str] = frozenset(
    [p for p in SCHEMA if p not in BLOCKED] + list(_EXTRA_PATHS)
)

_SYMBOL_HEADERS = frozenset({"symbol", "ticker", "tickers"})
_KEY_HEADERS = frozenset({"key", "setting", "path", "parameter", "field"})

_TRUE = frozenset({"true", "t", "yes", "y", "1", "on"})
_FALSE = frozenset({"false", "f", "no", "n", "0", "off"})

_BOM = "﻿"


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Change:
    """One setting the import would alter."""
    path: str
    old: Any
    new: Any


@dataclass
class ImportResult:
    """The outcome of applying a CSV - inspectable before anything is written."""
    profile: dict[str, Any]                      # merged, validated, not yet saved
    changes: list[Change] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: Literal["settings", "symbols"] = "settings"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "changes": [
                {"path": c.path, "old": _jsonable(c.old), "new": _jsonable(c.new)}
                for c in self.changes
            ],
            "unchanged": self.unchanged,
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
            "warnings": self.warnings,
        }


class CsvImportError(ValueError):
    """Every problem found in the file, not just the first.

    Reported together on purpose: fixing a forty-row CSV one error per
    round-trip is the kind of tooling people stop using.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# ── Value coercion ────────────────────────────────────────────────────────────
# Every CSV value arrives as a string, including the ones a spreadsheet mangled
# on the way out: 21 saved as "21.0", 15000 saved as "$15,000", 2.0 saved as "2%".


def _clean_number(raw: str) -> str:
    out = raw.strip()
    for junk in ("$", ",", "%", " "):
        out = out.replace(junk, "")
    return out


def _symbol_list(raw: str) -> list[str]:
    parts = raw.replace(";", ",").replace("|", ",").split(",")
    return [p.strip().upper() for p in parts if p.strip()]


def _to_bool(path: str, raw: str) -> bool:
    s = raw.strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"{path}: {raw!r} is not true/false")


def _to_int(path: str, raw: str) -> int:
    s = _clean_number(raw)
    try:
        return int(s)
    except ValueError:
        pass
    # Excel writes whole numbers as "21.0"; accept that, reject "21.5".
    try:
        f = float(s)
    except ValueError:
        raise ValueError(f"{path}: {raw!r} is not a whole number") from None
    if not f.is_integer():
        raise ValueError(f"{path}: {raw!r} is not a whole number")
    return int(f)


def _to_decimal(path: str, raw: str) -> Decimal:
    try:
        return Decimal(_clean_number(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{path}: {raw!r} is not a number") from None


def _coerce(path: str, raw: str) -> Any:
    """String -> the type the model declares for *path*."""
    if path.startswith(_GROUPS_PREFIX):
        return _symbol_list(raw)
    ann = SCHEMA.get(path)
    if get_origin(ann) is list:
        return _symbol_list(raw)
    if ann is bool:
        return _to_bool(path, raw)
    if ann is int:
        return _to_int(path, raw)
    if ann in (Decimal, float):
        return _to_decimal(path, raw)
    # Literals and strings (eod_exit_time, entry_order_type, name) pass through
    # and are validated by pydantic, which words the failure better than we can.
    return raw.strip()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


# ── Path helpers ──────────────────────────────────────────────────────────────

def _normalise_key(raw: str) -> str:
    """Forgiving on formatting, strict on identity.

    ``Risk.Max Position USD`` resolves; ``risk.max_positon_usd`` does not, and
    gets a suggestion instead of silence. Correlation-group names keep their
    case, because the tail of that path is a user-chosen label, not schema.
    """
    key = raw.strip()
    if key.lower().startswith(_GROUPS_PREFIX):
        return _GROUPS_PREFIX + key[len(_GROUPS_PREFIX):].strip()
    return key.lower().replace(" ", "_").replace("-", "_")


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = target
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _get_path(src: Any, path: str) -> Any:
    node = src
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _same(a: Any, b: Any) -> bool:
    return bool(_jsonable(a) == _jsonable(b))


# ── Parsing ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Row:
    line: int
    key: str
    value: str


def parse_csv(text: str) -> tuple[list[Row], Literal["settings", "symbols"]]:
    """Split CSV text into ``(rows, mode)``.

    A watchlist is normalised into a single ``symbols`` row so everything
    downstream handles exactly one shape.
    """
    text = text.lstrip(_BOM)            # Excel's UTF-8 byte-order mark
    reader = csv.reader(io.StringIO(text))
    rows: list[Row] = []
    symbols: list[str] = []
    mode: Literal["settings", "symbols"] = "settings"
    header_seen = False

    for lineno, cells in enumerate(reader, start=1):
        if not cells:
            continue
        first = cells[0].strip()
        if not any(c.strip() for c in cells):
            continue
        # '#' rows carry the section headings and explanations that export
        # writes, so a round-tripped file keeps its own documentation.
        if first.startswith("#"):
            continue

        if not header_seen:
            header_seen = True
            low = first.lower()
            filled = [c for c in cells if c.strip()]
            # "symbols,SPY,QQQ" is a legitimate settings row, so the bare word
            # only means watchlist when it is alone on the line. "symbol" and
            # "ticker" are never setting names, so they stay unambiguous even
            # beside the extra columns a screener export carries.
            if low in _SYMBOL_HEADERS or (low == "symbols" and len(filled) == 1):
                mode = "symbols"
                continue
            if low in _KEY_HEADERS:
                continue

        if mode == "symbols":
            symbols.extend(_symbol_list(first))
            continue

        if len(cells) < 2:
            raise CsvImportError(
                [f"line {lineno}: expected two columns (key,value), got {first!r}"]
            )
        rows.append(Row(line=lineno, key=first, value=cells[1]))

    if mode == "symbols":
        if not symbols:
            raise CsvImportError(["the watchlist has no symbols in it"])
        rows = [Row(line=0, key="symbols", value=",".join(symbols))]
    return rows, mode


# ── Import ────────────────────────────────────────────────────────────────────

def _suggest(key: str) -> str:
    near = get_close_matches(key, sorted(IMPORTABLE), n=1, cutoff=0.6)
    return f" - did you mean {near[0]!r}?" if near else ""


def _symbol_warnings(asset_class: str, symbols: list[str]) -> list[str]:
    if asset_class == "crypto":
        odd = [s for s in symbols if "/" not in s]
        if odd:
            return [
                f"asset_class is crypto but {', '.join(odd)} "
                f"{'is' if len(odd) == 1 else 'are'} not in Alpaca's BASE/QUOTE "
                f"form (BTC/USD) - the data feed returns no bars for "
                f"{'it' if len(odd) == 1 else 'them'}."
            ]
    else:
        odd = [s for s in symbols if "/" in s]
        if odd:
            return [
                f"asset_class is stock but {', '.join(odd)} "
                f"{'looks' if len(odd) == 1 else 'look'} like a crypto pair."
            ]
    return []


def apply_csv(existing: dict[str, Any], text: str) -> ImportResult:
    """Merge a CSV over *existing* and validate the result.

    Returns the merged profile without writing it, so a caller can show the
    operator what would change about their risk limits before it changes.
    """
    rows, mode = parse_csv(text)

    errors: list[str] = []
    skipped: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    values: dict[str, Any] = {}

    for row in rows:
        path = _normalise_key(row.key)
        where = f"line {row.line}: " if row.line else ""

        if path in BLOCKED:
            errors.append(f"{where}{path} cannot be set from CSV - {BLOCKED[path]}")
            continue
        if path == _GROUPS:
            errors.append(
                f"{where}set groups one per row, as "
                f"'{_GROUPS_PREFIX}<group_name>,\"SPY,QQQ\"'"
            )
            continue
        if path not in IMPORTABLE and not path.startswith(_GROUPS_PREFIX):
            errors.append(f"{where}unknown setting {row.key.strip()!r}{_suggest(path)}")
            continue
        if not row.value.strip():
            # Blank means "leave it alone", so a template listing every key can
            # be filled in partially. It never clears or removes anything.
            skipped.append((path, "no value given"))
            continue
        if path in seen:
            errors.append(f"{where}{path} is set twice (also on line {seen[path]})")
            continue
        seen[path] = row.line

        try:
            values[path] = _coerce(path, row.value)
        except ValueError as exc:
            errors.append(f"{where}{exc}")

    if errors:
        raise CsvImportError(errors)

    merged: dict[str, Any] = copy.deepcopy(existing)
    for path, value in values.items():
        _set_path(merged, path, value)

    try:
        cfg = Config(**{k: v for k, v in merged.items() if k != "name"})
    except ValidationError as exc:
        raise CsvImportError([_pydantic_message(e) for e in exc.errors()]) from exc
    except ValueError as exc:
        raise CsvImportError([str(exc)]) from exc

    # Write back the *validated* model, not the raw strings, so the profile YAML
    # holds real numbers. This mirrors what migrate_legacy() already does.
    profile = cfg.model_dump()
    profile["name"] = merged.get("name") or existing.get("name") or ""

    before = _dump_or_raw(existing)
    changes: list[Change] = []
    unchanged: list[str] = []
    for path in sorted(values):
        old = _get_path(before, path)
        new = _get_path(profile, path)
        if _same(old, new):
            unchanged.append(path)
        else:
            changes.append(Change(path=path, old=old, new=new))

    warnings = _symbol_warnings(cfg.asset_class, cfg.symbols)
    if any(c.path == "strategy.name" for c in changes):
        warnings.append(
            f"the active strategy becomes {cfg.strategy.name!r}; the other "
            f"strategies' settings are kept but unused."
        )

    return ImportResult(
        profile=profile,
        changes=changes,
        unchanged=unchanged,
        skipped=skipped,
        warnings=warnings,
        mode=mode,
    )


def _pydantic_message(err: Any) -> str:
    """One line out of a ValidationError. Typed loosely because pydantic's
    ErrorDetails is a TypedDict this module has no reason to couple to."""
    loc = ".".join(str(p) for p in err.get("loc", ()))
    msg = err.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else str(msg)


def _dump_or_raw(existing: dict[str, Any]) -> dict[str, Any]:
    """The profile as the model sees it, for an apples-to-apples diff.

    Comparing raw YAML against a validated dump would report 0.75 vs
    Decimal('0.75') as a change. Falls back to the raw dict when the profile on
    disk is already unloadable - a broken profile is exactly when someone
    reaches for an import.
    """
    try:
        cfg = Config(**{k: v for k, v in existing.items() if k != "name"})
    except Exception:
        return existing
    dump = cfg.model_dump()
    # name lives beside the Config, not in it, but it is an importable path -
    # without this it always reads as None and every import "changes" it.
    dump["name"] = existing.get("name", "")
    return dump


# ── Export ────────────────────────────────────────────────────────────────────

_SECTIONS: tuple[tuple[str, str], ...] = (
    ("symbols", "What it trades"),
    ("risk.", "Risk - sizing, heat caps, loss limits, drawdown throttle"),
    ("strategy.", "Strategy parameters (all strategies, not just the active one)"),
    ("ai.", "Optional AI hooks"),
)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):          # before int: a bool IS an int
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def export_csv(slug: str, data: dict[str, Any]) -> str:
    """Render a profile as an importable CSV, secrets excluded."""
    cfg = Config(**{k: v for k, v in data.items() if k != "name"})
    dump = cfg.model_dump()
    name = data.get("name", slug)
    active = cfg.strategy.name

    buf = io.StringIO()
    # \n, not the csv module's default \r\n - otherwise Windows writes \r\r\n.
    out = csv.writer(buf, lineterminator="\n")

    def comment(text: str) -> None:
        """Write a '#' line straight to the buffer.

        Not through the writer: a comment containing a comma would come back
        quoted, which is valid CSV and completely unreadable.
        """
        buf.write(f"# {text}\n")

    comment(f"claude-trading settings - {name} ({slug})")
    comment(f"exported {datetime.now(tz=UTC):%Y-%m-%d %H:%M} UTC")
    comment("Edit the value column and import this file into any profile.")
    comment("Importing MERGES: a row changes that setting, a missing row leaves")
    comment("it alone, and a blank value is skipped. Nothing is ever removed.")
    comment("live mode and the Alpaca keys are excluded and cannot be imported.")
    out.writerow(["key", "value"])
    out.writerow(["name", name])
    out.writerow(["asset_class", cfg.asset_class])

    emitted: set[str] = {"asset_class"}
    for prefix, heading in _SECTIONS:
        out.writerow([])
        comment(f"-- {heading} --")
        for path in SCHEMA:
            if path in BLOCKED or path in emitted:
                continue
            if not (path == prefix or path.startswith(prefix)):
                continue
            emitted.add(path)
            if path == _GROUPS:
                groups: dict[str, list[str]] = _get_path(dump, path) or {}
                if not groups:
                    comment(f'{_GROUPS_PREFIX}<group_name>,"SPY,QQQ"')
                for group, syms in groups.items():
                    out.writerow([f"{_GROUPS_PREFIX}{group}", _fmt(syms)])
                continue
            if (
                path.startswith("strategy.")
                and path != "strategy.name"
                and f".{active}." not in path
            ):
                # Kept, so the export is a complete backup - but flagged, so the
                # rows that actually drive the bot are findable by eye.
                out.writerow([path, _fmt(_get_path(dump, path)), "(inactive)"])
                continue
            out.writerow([path, _fmt(_get_path(dump, path))])

    return buf.getvalue()


# ── Profile-level convenience ─────────────────────────────────────────────────

def export_profile(slug: str) -> str:
    from profiles import load_profile
    return export_csv(slug, load_profile(slug))


def import_into_profile(slug: str, text: str, *, apply: bool = False) -> ImportResult:
    """Apply a CSV to a stored profile. ``apply=False`` (default) only reports."""
    from profiles import load_profile, save_profile
    result = apply_csv(load_profile(slug), text)
    if apply and result.changes:
        save_profile(slug, result.profile)
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
# So a profile can be restored on a fresh machine without starting the dashboard.

def _main() -> int:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Import/export profile settings as CSV.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="write a profile's settings to CSV")
    exp.add_argument("slug")
    exp.add_argument("-o", "--out", help="output file (default: stdout)")

    imp = sub.add_parser("import", help="apply a CSV to a profile")
    imp.add_argument("slug")
    imp.add_argument("csv_file")
    imp.add_argument(
        "--apply", action="store_true",
        help="actually write it (without this, only the diff is reported)",
    )

    args = ap.parse_args()

    if args.cmd == "export":
        text = export_profile(args.slug)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text, end="")
        return 0

    text = Path(args.csv_file).read_text(encoding="utf-8-sig")
    try:
        result = import_into_profile(args.slug, text, apply=args.apply)
    except CsvImportError as exc:
        print(f"Refused - {len(exc.errors)} problem(s):")
        for err in exc.errors:
            print(f"  * {err}")
        return 1

    for change in result.changes:
        print(f"  {change.path}: {_fmt(change.old)} -> {_fmt(change.new)}")
    for path, reason in result.skipped:
        print(f"  (skipped {path}: {reason})")
    for warning in result.warnings:
        print(f"  ! {warning}")
    if not result.changes:
        print("No changes - the profile already matches the file.")
    elif args.apply:
        print(f"Applied {len(result.changes)} change(s) to {args.slug}.")
    else:
        print(f"{len(result.changes)} change(s) - re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
