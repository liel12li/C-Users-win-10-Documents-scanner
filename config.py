"""
Stock Scanner Configuration
Edit these values to customize the scanner behavior.
"""

CONFIG = {
    # ── Output ──────────────────────────────────────────────
    "top_n": 4,
    "output_file": "scanner_output.html",

    # ── Universe ─────────────────────────────────────────────
    # Options: "finviz" | "sp500" | "nasdaq100" | "custom"
    "universe_method": "finviz",
    "max_stocks": 120,  # Cap to keep runtime reasonable (~3-4 min)

    # Custom tickers (used only when universe_method = "custom")
    "custom_tickers": ["AAPL", "NVDA", "MSFT", "TSLA", "META"],

    # Finviz pre-filter (used when universe_method = "finviz")
    "finviz_filters": {
        "Average Volume": "Over 500K",
        "Market Cap.": "Large ($10bln to $200bln)",
        "Country": "USA",
    },

    # ── Scoring Weights (must sum to 1.0) ────────────────────
    "weights": {
        "technical":      0.30,   # RSI, MACD, Moving Averages
        "momentum":       0.20,   # Multi-timeframe price returns
        "volume":         0.20,   # Relative volume, vol trend
        "fundamentals":   0.15,   # P/E, EPS/Revenue growth, margins
        "short_interest": 0.15,   # % float short, days-to-cover
    },

    # ── Technical Thresholds ─────────────────────────────────
    "rsi_sweet_spot": (40, 65),   # RSI range that scores highest
    "rsi_oversold":   30,
    "rsi_overbought": 75,

    # ── Volume Thresholds ────────────────────────────────────
    "rel_vol_great":  3.0,
    "rel_vol_good":   1.5,

    # ── Delay between yfinance requests (seconds) ────────────
    "request_delay": 0.15,
}
