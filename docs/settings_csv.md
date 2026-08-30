# Settings import/export (CSV)

Every tuned parameter in a profile — sizing, heat caps, stop distances, strategy
periods — lives only inside `profiles/<slug>.yaml`, which is gitignored. That
makes a working configuration something you can lose by changing machines, and
something you cannot review, diff, or hand to anyone else.

This feature makes those settings a file.

```powershell
# Back one up
python settings_csv.py export crypto-trendsr-5 -o crypto.csv

# See what a file would change (writes nothing)
python settings_csv.py import crypto-trendsr-5 crypto.csv

# Actually write it
python settings_csv.py import crypto-trendsr-5 crypto.csv --apply
```

The same two operations are in the dashboard, at the top of the Config panel:
**Export CSV** and **Import CSV**. Choosing a file only ever produces a diff —
nothing is written until you press Apply.

---

## The format

Two columns, one setting per row. Keys are dotted paths into the profile.

```csv
key,value
name,Crypto Trend/SR
asset_class,crypto
symbols,"BTC/USD,ETH/USD,SOL/USD"
strategy.name,trend_sr
strategy.trend_sr.bar_minutes,60
strategy.trend_sr.min_adx,22
risk.risk_per_trade_pct,0.5
risk.correlation_groups.crypto_beta,"BTC/USD,ETH/USD,SOL/USD"
```

- Rows beginning with `#` are comments, and blank rows are ignored. An export
  uses both, so the file documents itself and still round-trips.
- The `key,value` header row is optional.
- Keys are matched forgivingly on formatting: `Risk.Max Position USD` resolves
  to `risk.max_position_usd`. They are **not** matched fuzzily — a key that
  doesn't exist is an error with a suggestion, never a silent skip.
- Correlation groups are one row each, `risk.correlation_groups.<label>`. The
  label keeps its capitalisation; it's your name for the group, not a schema key.

### A plain watchlist also works

A single-column file with a `symbol` or `ticker` header sets the symbol list and
nothing else — which is the shape most screeners export:

```csv
Symbol,Name,Sector
NVDA,NVIDIA Corp,Technology
XOM,Exxon Mobil,Energy
```

Extra columns are ignored. `symbols,"SPY,QQQ"` in a settings file is still read
as a normal key/value row; only a lone `symbols` cell means watchlist.

---

## Three rules that matter more than the syntax

### It merges. It never replaces.

A three-row file changes three settings. Every key the file leaves out keeps the
value it already had, and a row with a blank value is skipped, so you can keep a
full template and fill in only the parts you're changing.

The alternative — rebuilding the profile from the file — would reset every
omitted key to a model default. Import a Trend/SR file into a profile with a
tuned `strategy.donchian.exit_lookback: 20`, and you'd silently get a Donchian
that exits on its entry channel again. `config_router.py` already carries scars
from this exact class of bug; the importer does not repeat it.

The corollary: **an import never removes anything.** Deleting a correlation-group
row from the file does not delete the group. Use the Config editor for that.

### An unknown key is an error, not a no-op.

```
line 8: unknown setting 'risk.max_positon_usd' - did you mean 'risk.max_position_usd'?
```

A typo that quietly does nothing is worse than one that fails, because you walk
away believing a risk limit is in force when it isn't. Every problem in the file
is reported at once, so a forty-row file takes one round trip to fix, not forty.

### `live` and the Alpaca keys cannot be imported at all.

They're refused by name, and excluded from every export. A file that can switch a
paper bot to real money, or move it onto a different account, isn't a
convenience. Change those in the profile editor, deliberately.

---

## Validation

The merged profile is rebuilt as a real `Config` before anything is written, so
an import is held to exactly the same standard as the dashboard's Save button:

- Field constraints (`max_open_positions ≥ 1`, `min_risk_scale ≤ 1`, …).
- The drawdown ladder's cross-field rule — `halt_dd_pct` must be deeper than
  `derisk_start_dd_pct`.
- The strategy/asset-class pairing in `STRATEGY_ASSETS`. Setting
  `asset_class,crypto` on an ORB profile is refused, because ORB on a 24/7 feed
  doesn't crash — it just quietly stops trading outside US market hours.

If validation fails, nothing is written.

You also get non-blocking warnings for things that are legal but probably not
what you meant: crypto symbols that aren't in `BASE/QUOTE` form, stock symbols
that look like crypto pairs, a change to the active strategy, and an import into
a profile whose bot is currently running (it keeps the settings it started with
until you restart it).

### Values a spreadsheet mangles

Round-tripping through Excel is the normal case, so the importer accepts what
Excel writes: `21.0` for an integer field, `$15,000` for a dollar amount, `2%`
for a percent field (which lands as `2.0`, since every percent field here already
means "2.0 == 2%"). `21.5` for an integer field is an error, not a rounding.

Booleans accept `true/false`, `yes/no`, `y/n`, `1/0`, `on/off` in any case.
Anything else is rejected rather than guessed — a misparsed `long_only` changes
what the strategy does.

---

## The key namespace

Valid keys are derived from the `Config` pydantic model at import time, not kept
in a list. A field added to `RiskConfig` or to any strategy becomes importable
and exportable the moment it is declared, with nothing to keep in sync.
`test_settings_csv.py::test_every_importable_path_round_trips` holds that
property.

| Prefix | Covers |
|--------|--------|
| `name`, `asset_class`, `symbols` | Profile identity and watch list |
| `risk.*` | Sizing, heat caps, loss limits, drawdown throttle |
| `risk.correlation_groups.<label>` | One group per row |
| `strategy.name` | Active strategy |
| `strategy.{orb,ema,donchian,trend_sr,vwap_revert}.*` | Per-strategy parameters |
| `ai.*` | Research / Claude-filter toggles |
| ~~`live`~~, ~~`alpaca_api_key`~~, ~~`alpaca_secret_key`~~ | Refused |

An export contains every strategy's block, not just the active one, so it is a
complete backup. Inactive rows carry an `(inactive)` marker in a third column,
which the importer ignores.

---

## HTTP API

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/profiles/{slug}/settings.csv` | Downloads the file |
| `POST` | `/api/profiles/{slug}/settings/import` | Multipart `file`, plus `apply` (default `false`) |

`apply=false` returns the diff and writes nothing. A rejected file returns 422
with `detail.errors` as a list; a file over 1 MB returns 413 before it is parsed.

---

## Where it lives

| Concern | Location |
|---------|----------|
| Parsing, coercion, merge, validation, CLI | `settings_csv.py` |
| HTTP endpoints | `api/routers/settings_csv_router.py` |
| UI buttons and diff panel | `ui/src/components/SettingsCsv.tsx` |
| Tests | `tests/test_settings_csv.py`, `tests/test_settings_csv_router.py` |
