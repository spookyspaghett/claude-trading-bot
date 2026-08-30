"""CSV settings import/export.

The failures worth guarding against here are the quiet ones. A CSV that
silently no-ops a mistyped risk key, or that resets the strategy block it
wasn't editing, leaves an operator confident about settings the bot is not
running.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from settings_csv import (
    BLOCKED,
    IMPORTABLE,
    SCHEMA,
    CsvImportError,
    apply_csv,
    export_csv,
    parse_csv,
)

_CREDS = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}


@pytest.fixture(autouse=True)
def _keys() -> Any:
    with patch.dict(os.environ, _CREDS):
        yield


def _profile(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Test",
        "asset_class": "stock",
        "live": False,
        "symbols": ["SPY", "AAPL"],
        "risk": {
            "max_position_usd": 50000,
            "stop_loss_pct": 1.0,
            "daily_loss_limit_usd": 500,
            "max_open_positions": 4,
            "risk_per_trade_pct": 0.75,
            "derisk_start_dd_pct": 8.0,
            "halt_dd_pct": 20.0,
        },
        "strategy": {
            "name": "orb",
            "donchian": {"lookback_days": 40, "exit_lookback": 20},
        },
        "ai": {"enable_research": False, "enable_claude_filter": False},
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
    }
    data.update(over)
    return data


def _csv(*rows: str) -> str:
    return "key,value\n" + "\n".join(rows) + "\n"


# ── The merge contract ────────────────────────────────────────────────────────

def test_import_changes_only_the_listed_keys() -> None:
    """The whole point: a sparse file is a patch, not a replacement.

    A replace-import would rebuild the profile from model defaults and quietly
    revert exit_lookback to 0, turning a 40-in/20-out Donchian into one that
    exits on its entry channel.
    """
    result = apply_csv(_profile(), _csv("risk.risk_per_trade_pct,0.5"))

    assert [c.path for c in result.changes] == ["risk.risk_per_trade_pct"]
    assert result.profile["strategy"]["donchian"]["exit_lookback"] == 20
    assert result.profile["strategy"]["donchian"]["lookback_days"] == 40
    assert result.profile["risk"]["max_position_usd"] == Decimal("50000")


def test_import_never_touches_the_untouched_profile() -> None:
    """apply_csv returns a new profile; the caller's dict is left alone so a
    dry-run cannot half-apply."""
    before = _profile()
    snapshot = str(before)
    apply_csv(before, _csv("risk.max_open_positions,8"))
    assert str(before) == snapshot


def test_diff_reports_old_and_new() -> None:
    result = apply_csv(_profile(), _csv("risk.max_open_positions,8"))
    change = result.changes[0]
    assert (change.path, change.old, change.new) == ("risk.max_open_positions", 4, 8)


def test_value_equal_to_current_is_reported_as_unchanged() -> None:
    result = apply_csv(_profile(), _csv("risk.max_open_positions,4"))
    assert result.changes == []
    assert "risk.max_open_positions" in result.unchanged


def test_export_import_round_trip_is_a_no_op() -> None:
    profile = _profile()
    result = apply_csv(profile, export_csv("test", profile))
    assert result.changes == [], f"round trip drifted: {result.changes}"


# ── Refusals ──────────────────────────────────────────────────────────────────

def test_unknown_key_is_an_error_not_a_silent_skip() -> None:
    with pytest.raises(CsvImportError, match="unknown setting") as exc:
        apply_csv(_profile(), _csv("risk.risk_per_trade,0.5"))
    # A near-miss should point at the real key rather than just failing.
    assert "risk_per_trade_pct" in exc.value.errors[0]


def test_live_cannot_be_set_from_csv() -> None:
    with pytest.raises(CsvImportError, match="live cannot be set from CSV"):
        apply_csv(_profile(), _csv("live,true"))


@pytest.mark.parametrize("key", ["alpaca_api_key", "alpaca_secret_key"])
def test_credentials_cannot_be_set_from_csv(key: str) -> None:
    with pytest.raises(CsvImportError, match="cannot be set from CSV"):
        apply_csv(_profile(), _csv(f"{key},stolen"))


def test_export_never_writes_secrets_or_live() -> None:
    text = export_csv("test", _profile())
    assert "alpaca_api_key" not in text
    assert "stolen" not in text
    for line in text.splitlines():
        assert not line.startswith("live,")
    for blocked in BLOCKED:
        assert f"\n{blocked}," not in text


def test_all_errors_are_reported_together() -> None:
    """One error per round trip makes a forty-row file unusable."""
    with pytest.raises(CsvImportError) as exc:
        apply_csv(
            _profile(),
            _csv("live,true", "nonsense.key,1", "risk.max_open_positions,banana"),
        )
    assert len(exc.value.errors) == 3


def test_duplicate_key_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="set twice"):
        apply_csv(_profile(), _csv("risk.max_open_positions,4",
                                   "risk.max_open_positions,8"))


# ── Validation still applies ──────────────────────────────────────────────────

def test_field_constraints_still_fire() -> None:
    """max_open_positions is ge=1; a CSV must not be a way around that."""
    with pytest.raises(CsvImportError, match="max_open_positions"):
        apply_csv(_profile(), _csv("risk.max_open_positions,0"))


def test_drawdown_ladder_cross_field_check_fires() -> None:
    """halt must be deeper than derisk_start - a model-level validator, so it
    only fires because the merged profile is rebuilt as a real Config."""
    with pytest.raises(CsvImportError, match="halt_dd_pct"):
        apply_csv(_profile(), _csv("risk.halt_dd_pct,5"))


def test_strategy_asset_class_pairing_is_enforced() -> None:
    """ORB on a crypto feed doesn't crash, it just stops trading - so the
    import has to be what refuses it."""
    with pytest.raises(CsvImportError, match="orb"):
        apply_csv(_profile(), _csv("asset_class,crypto"))


def test_switching_asset_class_and_strategy_together_is_allowed() -> None:
    result = apply_csv(
        _profile(),
        _csv("asset_class,crypto", "strategy.name,trend_sr", "symbols,BTC/USD"),
    )
    assert result.profile["asset_class"] == "crypto"
    assert result.profile["strategy"]["name"] == "trend_sr"


# ── Coercion: what a spreadsheet actually writes out ──────────────────────────

@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("21", 21),
        ("21.0", 21),      # Excel stores every number as a float
        (" 21 ", 21),
    ],
)
def test_int_fields_accept_what_excel_writes(raw: str, want: int) -> None:
    result = apply_csv(_profile(), _csv(f"strategy.trend_sr.ma_fast,{raw}"))
    assert result.profile["strategy"]["trend_sr"]["ma_fast"] == want


def test_fractional_value_for_an_int_field_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="whole number"):
        apply_csv(_profile(), _csv("strategy.trend_sr.ma_fast,21.5"))


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("15000", Decimal("15000")),
        ('"$15,000"', Decimal("15000")),
        ("15000.50", Decimal("15000.50")),
    ],
)
def test_currency_formatting_is_stripped(raw: str, want: Decimal) -> None:
    result = apply_csv(_profile(), _csv(f"risk.max_position_usd,{raw}"))
    assert result.profile["risk"]["max_position_usd"] == want


def test_percent_sign_is_stripped_not_rescaled() -> None:
    """Every pct field here means '2.0 == 2%', so 2% must land as 2.0, not 0.02."""
    result = apply_csv(_profile(), _csv("risk.daily_loss_limit_pct,2%"))
    assert result.profile["risk"]["daily_loss_limit_pct"] == Decimal("2")


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "yes", "y", "1", "on"])
def test_truthy_spellings(raw: str) -> None:
    result = apply_csv(_profile(), _csv(f"ai.enable_research,{raw}"))
    assert result.profile["ai"]["enable_research"] is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "no", "n", "0", "off"])
def test_falsy_spellings(raw: str) -> None:
    result = apply_csv(_profile(), _csv(f"strategy.donchian.long_only,{raw}"))
    assert result.profile["strategy"]["donchian"]["long_only"] is False


def test_ambiguous_boolean_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(CsvImportError, match="not true/false"):
        apply_csv(_profile(), _csv("strategy.donchian.long_only,maybe"))


# ── Lists and groups ──────────────────────────────────────────────────────────

def test_symbols_are_split_and_upcased() -> None:
    result = apply_csv(_profile(), _csv('symbols,"spy, qqq;iwm"'))
    assert result.profile["symbols"] == ["SPY", "QQQ", "IWM"]


def test_correlation_group_is_set_by_dotted_path() -> None:
    result = apply_csv(
        _profile(), _csv('risk.correlation_groups.index_beta,"SPY,QQQ,IWM"')
    )
    assert result.profile["risk"]["correlation_groups"] == {
        "index_beta": ["SPY", "QQQ", "IWM"]
    }


def test_group_names_keep_their_case() -> None:
    """The tail of that path is a user-chosen label, not a schema key."""
    result = apply_csv(_profile(), _csv('risk.correlation_groups.MegaCap,"AAPL"'))
    assert "MegaCap" in result.profile["risk"]["correlation_groups"]


def test_bare_correlation_groups_path_explains_the_dotted_form() -> None:
    with pytest.raises(CsvImportError, match="one per row"):
        apply_csv(_profile(), _csv("risk.correlation_groups,SPY"))


# ── Formatting tolerance ──────────────────────────────────────────────────────

def test_key_formatting_is_forgiving() -> None:
    result = apply_csv(_profile(), _csv("Risk.Max Open Positions,8"))
    assert result.profile["risk"]["max_open_positions"] == 8


def test_comments_and_blank_lines_are_ignored() -> None:
    text = (
        "# a comment, with a comma in it\n"
        "key,value\n"
        "\n"
        "# -- section --\n"
        "risk.max_open_positions,8\n"
    )
    result = apply_csv(_profile(), text)
    assert [c.path for c in result.changes] == ["risk.max_open_positions"]


def test_blank_value_is_skipped_not_cleared() -> None:
    """So a full template can be filled in partially."""
    result = apply_csv(_profile(), _csv("risk.max_open_positions,"))
    assert result.changes == []
    assert result.skipped == [("risk.max_open_positions", "no value given")]


def test_excel_byte_order_mark_is_tolerated() -> None:
    result = apply_csv(_profile(), "﻿" + _csv("risk.max_open_positions,8"))
    assert result.profile["risk"]["max_open_positions"] == 8


def test_header_row_is_optional() -> None:
    result = apply_csv(_profile(), "risk.max_open_positions,8\n")
    assert result.profile["risk"]["max_open_positions"] == 8


# ── Watchlist mode ────────────────────────────────────────────────────────────

def test_single_column_watchlist_sets_the_symbol_list() -> None:
    result = apply_csv(_profile(), "symbol\nNVDA\nAMD\nMSFT\n")
    assert result.mode == "symbols"
    assert result.profile["symbols"] == ["NVDA", "AMD", "MSFT"]


def test_screener_export_with_extra_columns_still_reads_column_one() -> None:
    text = "Symbol,Name,Sector\nNVDA,NVIDIA Corp,Tech\nXOM,Exxon,Energy\n"
    result = apply_csv(_profile(), text)
    assert result.profile["symbols"] == ["NVDA", "XOM"]


def test_symbols_as_a_settings_row_is_not_mistaken_for_a_watchlist_header() -> None:
    """'symbols,"SPY,QQQ"' is a legitimate key/value row; only a lone 'symbols'
    cell means watchlist."""
    rows, mode = parse_csv('symbols,"SPY,QQQ"\n')
    assert mode == "settings"
    assert rows[0].key == "symbols"


def test_empty_watchlist_is_rejected() -> None:
    with pytest.raises(CsvImportError, match="no symbols"):
        apply_csv(_profile(), "symbol\n\n")


# ── Warnings ──────────────────────────────────────────────────────────────────

def test_crypto_asset_class_with_bare_tickers_warns() -> None:
    result = apply_csv(
        _profile(),
        _csv("asset_class,crypto", "strategy.name,trend_sr", 'symbols,"BTC/USD,SPY"'),
    )
    # SPY is named as the offender; the correctly-formatted BTC/USD is not.
    assert any("but SPY is not" in w for w in result.warnings)


def test_changing_the_active_strategy_says_so() -> None:
    result = apply_csv(_profile(), _csv("strategy.name,vwap_revert"))
    assert any("vwap_revert" in w for w in result.warnings)


# ── The schema derivation itself ──────────────────────────────────────────────

def test_every_importable_path_round_trips() -> None:
    """Guards the derivation: a new config field must be exportable AND
    re-importable, not just appear in the file."""
    profile = _profile()
    text = export_csv("test", profile)
    exported = {
        line.split(",", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith("#") and "," in line
    }
    exported.discard("key")
    expected = {
        p for p in IMPORTABLE
        if p not in ("risk.correlation_groups",)
    }
    missing = expected - exported
    assert not missing, f"not exported: {sorted(missing)}"

    # And the file that came out must go back in cleanly.
    assert apply_csv(profile, text).changes == []


def test_blocked_keys_are_absent_from_the_importable_set() -> None:
    assert not (IMPORTABLE & set(BLOCKED))


def test_schema_covers_every_strategy() -> None:
    for name in ("orb", "ema", "donchian", "trend_sr", "vwap_revert"):
        assert any(p.startswith(f"strategy.{name}.") for p in SCHEMA), name
