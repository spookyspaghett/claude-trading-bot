# claude-trading

An Alpaca **paper-trading** bot and dashboard. It started as a single 15-minute
Opening-Range Breakout (ORB) scaffold for US equities and has grown into a
multi-strategy, multi-account platform with a web dashboard, backtesting, live
price charts, and crypto support. **Paper mode only by default** — live trading
requires an explicit typed confirmation.

## What's inside

- **Strategies**
  - **ORB** — 15-minute opening-range breakout (intraday equities).
  - **EMA crossover** — fast/slow EMA (equities or 24/7 crypto).
  - **Donchian** — daily-bar channel breakout; scans at the close (16:05 ET) and
    executes at the next open (09:31 ET), with a trailing stop.
  - **Trend/SR** — moving-average trend + pivot support/resistance breakout,
    crypto-oriented, with an optional regime-MA filter and optional
    [ADX & volume entry filters](docs/trend_sr_filters.md).
- **Stocks and crypto** — crypto runs 24/7 (no market-hours gate, GTC orders,
  fractional sizing, `BTC/USD` symbol format).
- **Profiles** — each profile is a self-contained bundle (name, its own Alpaca
  keys, asset class, symbols, strategy, risk). Stored locally and gitignored.
- **Multiple accounts at once** — run one bot per profile concurrently, each with
  its own dashboard tab, log stream, and kill switch.
- **Web dashboard** (FastAPI + React) — per-profile account balance, positions,
  P&L, live signal feed, candlestick price charts (with strategy overlays), a
  config editor, and start/stop/kill controls.
- **Backtesting** — ORB, Donchian, EMA, and Trend/SR, with modelled slippage and
  commission; pull bars from Alpaca or upload a CSV/Excel file.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11 or later |
| Node.js | 18 or later (for the dashboard UI) |
| Alpaca account | free at <https://alpaca.markets> |

---

## Quick start (local dev — Windows / macOS / Linux)

### 1 — Get Alpaca paper API keys

1. Create a free account at <https://alpaca.markets>.
2. Switch to **Paper Trading** (top-left toggle).
3. **API Keys → Generate New Key** and copy the **API Key ID** + **Secret Key**
   (the secret is shown only once).

### 2 — Install Python dependencies

**With uv (recommended):**

```powershell
pip install uv
uv venv .venv
.venv\Scripts\Activate.ps1     # macOS/Linux: source .venv/bin/activate
uv pip install -e ".[dev]"
```

**With plain pip:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1     # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

### 3 — (Optional) seed credentials for the first profile

On first run the app **migrates** any legacy `config.yaml` + `.env` into a
"Default" profile. If you want that seed, copy the template and fill it in:

```powershell
Copy-Item .env.example .env
```

```
ALPACA_API_KEY=your_paper_api_key_here
ALPACA_SECRET_KEY=your_paper_secret_key_here
```

`.env` and the `profiles/` directory are gitignored — never commit credentials.
After the first run you create and edit profiles entirely from the dashboard's
**Profiles** tab, so the `.env` seed is optional.

### 4 — Run the dashboard

Start the API (serves the bots, account data, and the built UI):

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

In a second terminal, run the UI in dev mode (hot reload):

```powershell
cd ui
npm install
npm run dev          # http://localhost:5173
```

For a production build the API serves the bundled UI directly:

```powershell
cd ui && npm run build      # outputs ui/dist, served by the API at :8000
```

Open the dashboard, create a profile (keys + asset class + symbols + strategy +
risk), and press **Start** on its tab. Each running profile is a separate
`python main.py --profile <slug>` process.

---

## Running a single bot from the CLI

You can also run one profile headless, without the dashboard:

```powershell
python main.py --profile <slug>     # omit --profile to use the active profile
```

Structured logs are written to `logs/<slug>/YYYY-MM-DD.jsonl`.

---

## Tests

```powershell
pytest -q
```

> Two `test_config.py` missing-key tests fail **only locally** when a real `.env`
> is present (because `load_dotenv()` repopulates the keys); they pass in a clean
> environment / CI.

---

## Kill switch

Each profile has its own kill file. From the dashboard, use the per-profile
**Kill** button or the header **Kill all** button. Manually, create the file:

```
logs/<slug>/KILL
```

The bot will cancel open orders, market-close its positions (equities; crypto
holds), and exit cleanly. Deleting the file (or pressing Start) re-arms it.

---

## Deployment (Ubuntu server)

`setup.sh` provisions and updates a deployment under `/home/<user>/claude-trading`:

```bash
chmod +x setup.sh
sudo ./setup.sh            # first run: install deps, build UI, create + start service
sudo ./setup.sh --force    # force a full reinstall + UI rebuild
```

It installs Python + Node deps, builds the UI, and creates a systemd service
`claude-trading` that runs `uvicorn api.main:app` on port 8000. Re-running pulls
the latest git changes and rebuilds only what changed. Bots are launched from the
dashboard; **if the API restarts, it relaunches the bots that were running**
(tracked in `logs/running_bots.json`).

---

## Project layout

```
claude-trading/
├── main.py               # per-profile async loop; branches on (asset_class, strategy)
├── config_loader.py      # Pydantic v2 config models
├── profiles.py           # profile store (CRUD) + legacy migration
├── logger.py             # structlog → daily JSONL per profile
├── risk.py               # sizing, stop, daily limit, kill switch, exposure caps
├── broker.py             # async TradingClient wrapper + live-trading guard
├── data.py               # Stock/Crypto data streams + bar aggregation
├── strategy.py           # Strategy ABC + ORB, EMA, Trend/SR
├── donchian_strategy.py  # daily Donchian breakout strategy + persisted state
├── donchian_runner.py    # Donchian EOD-scan → next-open loop (restart-safe handoff)
├── executor.py           # signal → risk check → order + broker-side trailing stop
├── backtest.py           # ORB / Donchian / EMA / Trend-SR backtests with costs
├── alerts.py             # optional Telegram alerts
├── api/                  # FastAPI app
│   ├── main.py           # app + routers; relaunches bots on startup
│   ├── bot_manager.py    # one subprocess per profile (start/stop/status/relaunch)
│   ├── deps.py           # per-profile cached TradingClient
│   └── routers/          # account, positions, bot, config, kill, ws, bars, backtest, profiles
├── ui/                   # React + Vite + TypeScript dashboard (lightweight-charts)
├── profiles/             # gitignored per-profile YAML + active.txt
├── memory/               # persisted strategy/handoff state + backtest reports
├── logs/                 # per-profile JSONL logs + KILL files (runtime)
├── tests/                # pytest suite
└── setup.sh              # Ubuntu provision/update + systemd service
```

---

## Safety guarantees

| Guarantee | How |
|-----------|-----|
| Paper mode by default | `TradingClient(paper=True)` unless `live: true` in the profile |
| Live confirmation | Broker refuses live trading until you type the confirmation |
| Per-account isolation | Each profile has its own keys, logs, state files, and kill switch |
| Kill switch | Per-profile `KILL` file polled continuously; UI Kill / Kill-all |
| Daily loss halt | Flattens and stops when the daily P&L (incl. unrealized) hits the limit |
| Exposure caps | Risk manager caps pending orders and aggregate exposure to equity |
| Market hours (equities) | Bars outside 09:30–16:00 ET are discarded; crypto trades 24/7 |
| Restart-safe Donchian | Persisted handoff + broker reconciliation so a restart can't orphan, duplicate, or strand a position; stops re-anchor to the real fill |
| Stale-data guard | Donchian skips the scan on a stale daily bar instead of trading yesterday |
| Bot supervision | API relaunches bots that were running before it restarted |
| Decimal arithmetic | Prices and quantities use `decimal.Decimal`, never `float` |
```
