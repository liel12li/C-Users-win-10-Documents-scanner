"""
Stock Scanner Configuration – edit freely, no coding needed.
"""

CONFIG = {
    # ── Output ───────────────────────────────────────────────
    "top_n":        4,
    "output_file":  "scanner_output.html",

    # ── Universe ─────────────────────────────────────────────
    # "finviz" | "nasdaq100" | "sp500" | "custom"
    "universe_method":  "finviz",
    "max_stocks":       60,       # keep low → fast scan
    "parallel_workers": 15,       # concurrent info fetches

    # Used only when universe_method = "custom"
    "custom_tickers": ["AAPL", "NVDA", "MSFT", "TSLA", "META", "AMZN"],

    # Finviz screener pre-filter
    "finviz_filters": {
        "Average Volume": "Over 500K",
        "Market Cap.":    "Large ($10bln to $200bln)",
        "Country":        "USA",
    },

    # ── Hard pre-filters (applied before scoring) ────────────
    # Stocks that don't pass these are dropped immediately
    "pre_filters": {
        "rsi_min":        20.0,    # drop if RSI < this
        "rsi_max":        85.0,    # drop if RSI > this
        "min_price":       5.0,    # drop if price < $5
        "max_price":    5000.0,    # drop if price > $5000
        "min_rel_volume":  0.3,    # drop if relative volume < this
        "only_sectors":    [],     # e.g. ["Technology","Healthcare"]; [] = all
        "min_market_cap":  0,      # in USD, e.g. 1_000_000_000 for $1B
    },

    # ── Scoring Weights (must sum to 1.0) ────────────────────
    "weights": {
        "technical":       0.30,
        "momentum":        0.20,
        "volume":          0.20,
        "fundamentals":    0.15,
        "short_interest":  0.15,
    },

    # ── RSI scoring thresholds ───────────────────────────────
    "rsi_sweet_spot": (40, 65),
    "rsi_oversold":   30,
    "rsi_overbought": 75,

    # ── Volume thresholds ────────────────────────────────────
    "rel_vol_great": 3.0,
    "rel_vol_good":  1.5,
}
