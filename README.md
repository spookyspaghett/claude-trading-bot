# claude-trading

Alpaca paper-trading scaffold implementing a 15-minute Opening-Range Breakout (ORB) strategy on US equities.  **Paper mode only by default.**

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11 or later |
| pip / uv | any recent |
| Alpaca account | free at alpaca.markets |

---

## 1 — Get your Alpaca paper API keys

1. Create a free account at <https://alpaca.markets>.
2. In the dashboard, switch to **Paper Trading** (toggle in the top-left).
3. Go to **API Keys** → **Generate New Key**.
4. Copy the **API Key ID** and **Secret Key** — the secret is shown only once.

---

## 2 — Clone / copy the project

```
F:\Claude-trading\
```

---

## 3 — Create your `.env` file

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in your paper credentials:

```
ALPACA_API_KEY=your_paper_api_key_here
ALPACA_SECRET_KEY=your_paper_secret_key_here
```

Never commit `.env` to git — it is already in `.gitignore`.

---

## 4 — Install dependencies

**With uv (recommended):**

```powershell
pip install uv
uv venv .venv
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

**With plain pip:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

---

## 5 — (Optional) install pre-commit hooks

```powershell
pip install pre-commit
pre-commit install
```

This runs `ruff` and `mypy` before every commit.

---

## 6 — Run the tests

```powershell
pytest -v
```

Expected output: all tests pass.  Coverage on `risk.py` and `strategy.py` should be above 80 %.

---

## 7 — Configure your strategy

Edit `config.yaml`:

```yaml
symbols:
  - SPY
  - AAPL
  - MSFT
  - NVDA

risk:
  max_position_usd: 5000      # max notional per position
  stop_loss_pct: 1.0          # % of entry price
  daily_loss_limit_usd: 500   # halt + flatten if daily P&L hits this
  max_open_positions: 4

strategy:
  name: orb
  orb:
    opening_range_minutes: 15   # 09:30–09:45 ET
    entry_order_type: limit     # or "market"
    eod_exit_time: "15:50"      # flatten all at this time ET
```

---

## 8 — Start the bot

```powershell
python main.py
```

The bot will:

1. Connect to `https://paper-api.alpaca.markets`.
2. Subscribe to live 1-minute bars for your configured symbols.
3. Between 09:30–09:45 ET, record the opening-range high and low.
4. After 09:45, fire a **limit buy** on a close above the range high, or a **limit sell** on a close below the range low.
5. Attach a protective stop order at ±1 % of entry.
6. Flatten all positions at 15:50 ET.

Structured logs are written to `logs/YYYY-MM-DD.jsonl`.

---

## 9 — Verify a paper trade

1. Open the [Alpaca paper dashboard](https://app.alpaca.markets).
2. Go to **Paper Trading → Orders** to see submitted orders.
3. Go to **Positions** to see open positions.
4. Check `logs/` for JSONL entries with `"event": "order_submitted"` and `"event": "fill"`.

---

## Kill switch

Create an empty file called `KILL` in the project root while the bot is running:

```powershell
New-Item KILL -ItemType File
```

The bot will:
1. Cancel all open orders.
2. Market-close all positions.
3. Exit cleanly.

---

## Project layout

```
F:\Claude-trading\
├── config.yaml          # strategy + risk parameters (safe to commit)
├── .env                 # API credentials (never commit)
├── .env.example         # credential template
├── pyproject.toml       # dependencies and tool config
├── config_loader.py     # Pydantic config models
├── logger.py            # structlog → daily JSONL
├── risk.py              # position sizing, stop loss, daily limit, kill switch
├── broker.py            # TradingClient wrapper
├── data.py              # StockDataStream + 5m bar aggregation
├── strategy.py          # ORBStrategy (abstract base + implementation)
├── executor.py          # signal → risk check → order + stop
├── main.py              # async event loop
├── tests/
│   ├── test_config.py
│   ├── test_risk.py
│   └── test_strategy.py
└── logs/                # created automatically at runtime
```

---

## Safety guarantees

| Guarantee | How |
|-----------|-----|
| Paper mode by default | `TradingClient(paper=True)` unless `live: true` in config |
| Live confirmation | Prints prompt requiring you to type `YES` |
| No orders while disconnected | `feed.connected` checked before processing each bar |
| Kill switch | Polls for `KILL` file every event-loop tick |
| Daily loss halt | Flattens and stops immediately when limit is hit |
| Market hours only | Bars outside 09:30–16:00 ET are discarded |
| Decimal arithmetic | All prices and quantities use `decimal.Decimal`, never `float` |
