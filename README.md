# Trading Terminal v3.0

Live trading dashboard for LaT-PFN automated futures trading. Displays real-time prices, ADX regime detection, economic calendar, broker performance, predictions, turbo signals, and Polymarket forecasts.

## Quick Start

```bash
cd ~/Desktop/trading-terminal
source ~/latpfn-trading/venv/bin/activate
python app.py
```

Open http://localhost:5099

## Structure

```
trading-terminal/
  app.py                  # Flask routes & API endpoints
  config.py               # Configuration (ports, paths, YF_MAP)
  templates/
    dashboard.html        # Main dashboard template
  static/
    css/terminal.css      # Terminal/TUI visual theme
    js/terminal.js        # Client-side polling & chart logic
  tests/
    test_api.py           # API endpoint tests
    test_blockbar.py      # blockBar edge case tests
    test_config.py        # Configuration validation
    test_security.py      # Security header tests
```

## Instruments

| Symbol | yfinance Ticker | Description |
|--------|----------------|-------------|
| MNQ    | NQ=F           | Micro Nasdaq |
| MYM    | YM=F           | Micro Dow    |
| MES    | ES=F           | Micro S&P    |
| MBT    | BTC=F          | Micro Bitcoin|

## Tests

```bash
cd ~/Desktop/trading-terminal
source ~/latpfn-trading/venv/bin/activate
pytest tests/ -v
```

## Lint

```bash
ruff check .
```
