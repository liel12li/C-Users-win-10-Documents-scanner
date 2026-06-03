#!/usr/bin/env python3
"""
Stock Scanner – fast multi-factor analysis.

Speed strategy:
  1. yf.download()           → batch price history in ONE request
  2. ThreadPoolExecutor      → parallel info fetches
  3. Finviz screener         → pre-filtered universe
Target runtime: ~60 seconds for 60 stocks.
"""

import sys
import json
import time
import logging
import threading
import warnings
import http.server
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf

from config import CONFIG

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# LIVE PRICE SERVER  (localhost:18723)
# ─────────────────────────────────────────────────────────────────────────────
PRICE_PORT   = 18723
_price_cache: dict = {}          # {ticker: {price, change_pct, prev_close}}
_cache_lock  = threading.Lock()
_cache_ts    = 0.0               # epoch of last refresh
_CACHE_TTL   = 60                # seconds between yfinance refreshes


def _yf_live(symbols: list) -> dict:
    """Fetch latest price + change% for a list of symbols via yfinance."""
    if not symbols:
        return {}
    try:
        raw = yf.download(
            symbols, period="5d", interval="1d",
            auto_adjust=True, progress=False, timeout=20,
            group_by="ticker",
        )
        out: dict = {}
        for sym in symbols:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    closes = raw[sym]["Close"].dropna()
                else:
                    closes = raw["Close"].dropna()
                if len(closes) < 2:
                    continue
                curr  = float(closes.iloc[-1])
                prev  = float(closes.iloc[-2])
                chpct = (curr / prev - 1) * 100
                out[sym] = {
                    "price":      round(curr, 2),
                    "change_pct": round(chpct, 2),
                    "prev_close": round(prev, 2),
                }
            except Exception:
                pass
        return out
    except Exception:
        return {}


def prefill_price_cache(stocks: list) -> None:
    """Pre-populate cache with data we already have from the scan."""
    with _cache_lock:
        global _cache_ts
        for s in stocks:
            t = s["ticker"]
            _price_cache[t] = {
                "price":      round(s["price"], 2),
                "change_pct": round(s["momentum"]["ret_1d"], 2),
                "prev_close": round(s["price"] / (1 + s["momentum"]["ret_1d"] / 100), 2),
            }
        _cache_ts = time.time()


def _background_refresh(symbols: list) -> None:
    global _cache_ts
    fresh = _yf_live(symbols)
    if fresh:
        with _cache_lock:
            _price_cache.update(fresh)
            _cache_ts = time.time()


class _PriceHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _cache_ts
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/prices":
            params  = urllib.parse.parse_qs(parsed.query)
            symbols = [s.strip() for s in params.get("symbols", [""])[0].split(",") if s.strip()]

            # Trigger background refresh if cache is stale
            if time.time() - _cache_ts > _CACHE_TTL:
                threading.Thread(target=_background_refresh, args=(symbols,), daemon=True).start()

            with _cache_lock:
                data = {k: _price_cache[k] for k in symbols if k in _price_cache}

            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_): pass   # silent


_httpd: http.server.HTTPServer | None = None


def start_price_server() -> bool:
    global _httpd
    try:
        _httpd = http.server.HTTPServer(("localhost", PRICE_PORT), _PriceHandler)
        t = threading.Thread(target=_httpd.serve_forever, daemon=True)
        t.start()
        return True
    except OSError:
        return False   # port busy – another instance may already be running
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Global cancel flag – set by GUI Cancel button
_cancel = threading.Event()

def cancel():
    _cancel.set()

def _cancelled():
    return _cancel.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

NASDAQ_100 = [
    "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","ORCL",
    "COST","NFLX","ADBE","AMD","CSCO","QCOM","TMUS","INTU","PEP","TXN",
    "CMCSA","AMGN","HON","AMAT","ISRG","BKNG","VRTX","ADP","MU","REGN",
    "PANW","SBUX","MDLZ","GILD","ADI","LRCX","SNPS","KLAC","MELI","CDNS",
    "CRWD","INTC","CTAS","FTNT","MAR","ABNB","MRVL","PAYX","ORLY","DXCM",
    "IDXX","PCAR","ROST","FAST","ODFL","VRSK","CTSH","BIIB","ON","CPRT",
]

FALLBACK = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","ORCL","NFLX",
    "ADBE","AMD","QCOM","CRM","NOW","UBER","JPM","BAC","GS","V","MA",
    "XOM","CVX","LLY","UNH","JNJ","ABBV","WMT","COST","HD","NKE",
]


def _sp500() -> list:
    try:
        df = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return df["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        log.warning(f"S&P 500 fetch failed: {e}")
        return []


def _finviz(filters: dict) -> list:
    try:
        from finvizfinance.screener.overview import Overview
        s = Overview()
        s.set_filter(filters_dict=filters)
        df = s.screener_view()
        if df is not None and len(df) > 0:
            return df["Ticker"].tolist()
    except Exception as e:
        log.warning(f"Finviz failed: {e}")
    return []


def get_universe() -> list:
    m = CONFIG["universe_method"]
    tickers: list = []

    if m == "custom":
        tickers = list(CONFIG["custom_tickers"])
    elif m == "finviz":
        log.info("Fetching universe from Finviz…")
        tickers = _finviz(CONFIG["finviz_filters"])
    elif m == "nasdaq100":
        tickers = list(NASDAQ_100)
    elif m == "sp500":
        log.info("Fetching S&P 500 from Wikipedia…")
        tickers = _sp500()

    if not tickers:
        log.warning("Primary source failed – using built-in fallback list.")
        tickers = list(FALLBACK)

    tickers = list(dict.fromkeys(tickers))  # deduplicate
    cap = CONFIG["max_stocks"]
    log.info(f"Universe: {len(tickers)} tickers (capped at {cap})")
    return tickers[:cap]


# ─────────────────────────────────────────────────────────────────────────────
# FAST BATCH DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def batch_prices(tickers: list) -> dict[str, pd.DataFrame]:
    """Download all price history in one call (much faster than one-by-one)."""
    log.info(f"Batch downloading price history for {len(tickers)} tickers…")
    try:
        raw = yf.download(
            tickers=tickers,
            period="3mo",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
            timeout=60,
        )
        result: dict[str, pd.DataFrame] = {}

        if isinstance(raw.columns, pd.MultiIndex):
            # Multi-ticker: columns = (field, ticker) or (ticker, field)
            lvl0 = raw.columns.get_level_values(0).unique().tolist()
            for t in tickers:
                try:
                    if t in lvl0:
                        df = raw[t].dropna(how="all")
                    else:
                        df = raw.xs(t, axis=1, level=1).dropna(how="all")
                    if len(df) >= 20:
                        result[t] = df
                except Exception:
                    pass
        else:
            # Single ticker returned as plain DataFrame
            if len(tickers) == 1 and len(raw) >= 20:
                result[tickers[0]] = raw.dropna(how="all")

        log.info(f"Price data: {len(result)}/{len(tickers)} tickers OK")
        return result

    except Exception as e:
        log.error(f"Batch download failed: {e}")
        return {}


def parallel_infos(tickers: list) -> dict[str, dict]:
    """Fetch ticker.info for all tickers in parallel."""
    log.info(f"Fetching fundamentals ({CONFIG['parallel_workers']} workers)…")
    results: dict[str, dict] = {}

    def _get(t):
        try:
            return t, yf.Ticker(t).info or {}
        except Exception:
            return t, {}

    with ThreadPoolExecutor(max_workers=CONFIG["parallel_workers"]) as ex:
        futures = {ex.submit(_get, t): t for t in tickers}
        for f in as_completed(futures):
            if _cancelled():
                ex.shutdown(wait=False, cancel_futures=True)
                break
            t, info = f.result()
            results[t] = info

    return results


# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(prices: pd.Series, n=14) -> float:
    d = prices.diff()
    g = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    r = g / l.replace(0, np.nan)
    v = (100 - 100 / (1 + r)).iloc[-1]
    return float(v) if pd.notna(v) else 50.0


def _macd(prices: pd.Series) -> dict:
    f = prices.ewm(span=12, adjust=False).mean()
    s = prices.ewm(span=26, adjust=False).mean()
    line = f - s
    sig = line.ewm(span=9, adjust=False).mean()
    hist = line - sig
    cross = len(line) >= 2 and bool(line.iloc[-1] > sig.iloc[-1] and line.iloc[-2] <= sig.iloc[-2])
    return {
        "above_signal": bool(line.iloc[-1] > sig.iloc[-1]),
        "bull_cross":   cross,
        "histogram":    float(hist.iloc[-1]),
        "hist_prev":    float(hist.iloc[-2]) if len(hist) >= 2 else 0.0,
    }


def _ma_flags(prices: pd.Series) -> dict:
    p = float(prices.iloc[-1])
    def sma(n):
        if len(prices) >= n:
            v = float(prices.rolling(n).mean().iloc[-1])
            return v if pd.notna(v) else None
        return None
    s20, s50, s200 = sma(20), sma(50), sma(min(200, len(prices) - 1))
    return {
        "above_20":  p > s20  if s20  else None,
        "above_50":  p > s50  if s50  else None,
        "above_200": p > s200 if s200 else None,
        "sma20": s20, "sma50": s50, "sma200": s200,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def _score_technical(hist: pd.DataFrame) -> dict:
    prices = hist["Close"]
    rsi  = _rsi(prices)
    macd = _macd(prices)
    mas  = _ma_flags(prices)

    lo, hi = CONFIG["rsi_sweet_spot"]
    if lo <= rsi <= hi:                     rsi_s = 100
    elif CONFIG["rsi_oversold"] <= rsi < lo: rsi_s = 75
    elif hi < rsi <= CONFIG["rsi_overbought"]: rsi_s = 60
    elif rsi < CONFIG["rsi_oversold"]:       rsi_s = 45
    else:                                    rsi_s = 22

    if macd["bull_cross"]:                                            macd_s = 100
    elif macd["above_signal"] and macd["histogram"] > macd["hist_prev"]: macd_s = 85
    elif macd["above_signal"]:                                        macd_s = 68
    elif macd["histogram"] > macd["hist_prev"]:                       macd_s = 48
    else:                                                             macd_s = 20

    n_above = sum(1 for v in [mas["above_20"], mas["above_50"], mas["above_200"]] if v is True)
    ma_s = 15 + n_above * 28

    score = rsi_s * 0.35 + macd_s * 0.35 + ma_s * 0.30
    return {"score": float(np.clip(score, 0, 100)), "rsi": rsi, "rsi_score": rsi_s,
            "macd": macd, "macd_score": macd_s, "mas": mas, "ma_score": ma_s}


def _score_momentum(hist: pd.DataFrame) -> dict:
    prices = hist["Close"]
    def ret(n):
        return float((prices.iloc[-1] / prices.iloc[-n] - 1) * 100) if len(prices) > n else 0.0
    r1d, r1w, r1m, r3m = ret(1), ret(5), ret(21), ret(63)
    def s(r, ok, great):
        if r >= great: return 100.0
        if r >= ok:    return 50 + 50 * (r - ok) / (great - ok)
        if r >= 0:     return 50 * r / ok
        return max(0.0, 50 + r * 2.5)
    score = s(r1d,.5,2)*0.10 + s(r1w,2,5)*0.20 + s(r1m,5,15)*0.30 + s(r3m,10,30)*0.40
    return {"score": float(np.clip(score,0,100)), "ret_1d":r1d,"ret_1w":r1w,"ret_1m":r1m,"ret_3m":r3m}


def _score_volume(hist: pd.DataFrame) -> dict:
    vols   = hist["Volume"].astype(float)
    prices = hist["Close"]
    avg    = float(vols.iloc[:-1].tail(20).mean()) or 1.0
    cur    = float(vols.iloc[-1])
    rv     = cur / avg
    trend  = float(vols.tail(5).mean() / (vols.iloc[-10:-5].mean() or avg))
    pc = prices.pct_change().tail(10)
    vc = vols.pct_change().tail(10)
    up_v   = vc[pc > 0].mean() if any(pc > 0) else 0.0
    down_v = vc[pc < 0].mean() if any(pc < 0) else 0.0
    pvr_s  = float(np.clip(60 + (up_v - down_v) * 20, 0, 100))
    grt, gd = CONFIG["rel_vol_great"], CONFIG["rel_vol_good"]
    rv_s   = (100 if rv>=grt else 85 if rv>=grt*.67 else 70 if rv>=gd else 50 if rv>=1 else 35 if rv>=.7 else 18)
    vt_s   = (100 if trend>=1.2 else 70 if trend>=1 else 50 if trend>=.8 else 28)
    score  = rv_s*.50 + vt_s*.30 + pvr_s*.20
    return {"score":float(np.clip(score,0,100)),"cur_vol":cur,"avg_vol":avg,"rel_vol":rv,"vol_trend":trend}


def _score_fundamentals(info: dict) -> dict:
    pts, mets = [], {}
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe and 0 < pe < 1000:
        mets["pe"] = round(float(pe),1)
        pts.append((90 if pe<15 else 75 if pe<25 else 60 if pe<35 else 45 if pe<50 else 25, 0.25))
    epsg = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
    if epsg is not None:
        mets["eps_growth"] = round(float(epsg)*100,1)
        eg = float(epsg)
        pts.append((100 if eg>.30 else 80 if eg>.15 else 65 if eg>.05 else 50 if eg>0 else 30 if eg>-.10 else 10, 0.30))
    rg = info.get("revenueGrowth")
    if rg is not None:
        mets["rev_growth"] = round(float(rg)*100,1)
        rg = float(rg)
        pts.append((100 if rg>.20 else 75 if rg>.10 else 60 if rg>.05 else 45 if rg>0 else 20, 0.25))
    pm = info.get("profitMargins")
    if pm is not None:
        mets["profit_margin"] = round(float(pm)*100,1)
        pm = float(pm)
        pts.append((100 if pm>.25 else 80 if pm>.15 else 60 if pm>.05 else 40 if pm>0 else 10, 0.20))
    if not pts:
        return {"score":50.0,"metrics":mets}
    tw = sum(w for _,w in pts)
    return {"score":float(np.clip(sum(s*w for s,w in pts)/tw,0,100)),"metrics":mets}


def _score_short_interest(info: dict) -> dict:
    mets: dict = {}
    sf = info.get("shortPercentOfFloat")
    sr = info.get("shortRatio")
    if sf is not None:
        pct = float(sf)*100
        mets["short_float"] = round(pct,1)
        sf_s = (95 if pct<2 else 80 if pct<5 else 65 if pct<10 else 45 if pct<15 else 30 if pct<20 else 15)
    else:
        sf_s = 60
    if sr is not None:
        sr = float(sr)
        mets["short_ratio"] = round(sr,1)
        sr_s = (90 if sr<2 else 75 if sr<3 else 55 if sr<5 else 35 if sr<8 else 18)
        final = sf_s*.60 + sr_s*.40 if sf is not None else sr_s
    else:
        final = sf_s
    return {"score":float(np.clip(final,0,100)),"metrics":mets}


# ─────────────────────────────────────────────────────────────────────────────
# PRE-FILTER
# ─────────────────────────────────────────────────────────────────────────────

def _passes_prefilter(ticker: str, hist: pd.DataFrame, info: dict) -> bool:
    pf   = CONFIG["pre_filters"]
    prices = hist["Close"]
    price  = float(prices.iloc[-1])

    if not (pf["min_price"] <= price <= pf["max_price"]):
        return False

    mc = info.get("marketCap")
    if mc and pf.get("min_market_cap", 0) > 0 and mc < pf["min_market_cap"]:
        return False

    only = pf.get("only_sectors", [])
    if only:
        sector = info.get("sector", "")
        if sector not in only:
            return False

    rsi = _rsi(prices)
    if not (pf["rsi_min"] <= rsi <= pf["rsi_max"]):
        return False

    vols = hist["Volume"].astype(float)
    avg  = float(vols.iloc[:-1].tail(20).mean()) or 1.0
    rv   = float(vols.iloc[-1]) / avg
    if rv < pf["min_rel_volume"]:
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def run_scanner() -> list:
    _cancel.clear()

    tickers = get_universe()
    if _cancelled():
        return []

    # ── Step 1: batch download all price history (fast!) ─────────────────────
    prices_map = batch_prices(tickers)
    if _cancelled():
        return []

    valid_tickers = list(prices_map.keys())
    log.info(f"Valid price data: {len(valid_tickers)} tickers")

    # ── Step 2: parallel info fetch ──────────────────────────────────────────
    infos = parallel_infos(valid_tickers)
    if _cancelled():
        return []

    # ── Step 3: score each stock ─────────────────────────────────────────────
    log.info("Scoring stocks…")
    results = []
    w = CONFIG["weights"]

    for t in valid_tickers:
        if _cancelled():
            break
        hist = prices_map[t]
        info = infos.get(t, {})

        if not _passes_prefilter(t, hist, info):
            continue

        try:
            tech  = _score_technical(hist)
            mom   = _score_momentum(hist)
            vol   = _score_volume(hist)
            fund  = _score_fundamentals(info)
            si    = _score_short_interest(info)

            composite = (
                tech["score"] * w["technical"] +
                mom["score"]  * w["momentum"]  +
                vol["score"]  * w["volume"]    +
                fund["score"] * w["fundamentals"] +
                si["score"]   * w["short_interest"]
            )

            results.append({
                "ticker":          t,
                "name":            info.get("longName", info.get("shortName", t)),
                "sector":          info.get("sector",   "—"),
                "industry":        info.get("industry", "—"),
                "market_cap":      info.get("marketCap"),
                "price":           float(hist["Close"].iloc[-1]),
                "composite_score": float(np.clip(composite, 0, 100)),
                "technical":       tech,
                "momentum":        mom,
                "volume":          vol,
                "fundamentals":    fund,
                "short_interest":  si,
            })
        except Exception as exc:
            log.debug(f"  {t}: scoring error – {exc}")

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    top = results[: CONFIG["top_n"]]

    log.info("\n" + "─" * 42)
    log.info("  TOP RESULTS")
    log.info("─" * 42)
    for i, s in enumerate(top, 1):
        log.info(f"  {i}. {s['ticker']:<6}  score={s['composite_score']:5.1f}  ${s['price']:.2f}  {s['name'][:28]}")
    log.info("─" * 42)
    return top


# ─────────────────────────────────────────────────────────────────────────────
# HTML DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_cap(mc) -> str:
    if mc is None: return "N/A"
    if mc >= 1e12: return f"${mc/1e12:.1f}T"
    if mc >= 1e9:  return f"${mc/1e9:.1f}B"
    if mc >= 1e6:  return f"${mc/1e6:.1f}M"
    return f"${mc:,.0f}"

def _fmt_pct(val, d=1) -> str:
    if val is None: return "N/A"
    return f"{'+'if val>=0 else ''}{val:.{d}f}%"

def _col(score: float) -> str:
    if score >= 75: return "#00ff88"
    if score >= 55: return "#ffd700"
    if score >= 38: return "#ff9944"
    return "#ff4466"

def _card(rank: int, s: dict) -> str:
    tech = s["technical"]; mom = s["momentum"]; vol = s["volume"]
    fund = s["fundamentals"]; si = s["short_interest"]

    bars = ""
    for cat, sc in [("Technical",tech["score"]),("Momentum",mom["score"]),
                    ("Volume",vol["score"]),("Fundamentals",fund["score"]),
                    ("Short Interest",si["score"])]:
        c = _col(sc)
        bars += f'<div class="sr"><span class="sl">{cat}</span><div class="sb-wrap"><div class="sb" style="width:{sc:.0f}%;background:{c}"></div></div><span class="sv" style="color:{c}">{sc:.0f}</span></div>'

    mets = (f'<div class="mg">'
        f'<div class="m"><span class="ml">RSI 14</span><span class="mv" style="color:{_col(tech["rsi_score"])}">{tech["rsi"]:.1f}</span></div>'
        f'<div class="m"><span class="ml">Rel. Vol</span><span class="mv" style="color:{_col(vol["score"])}">{vol["rel_vol"]:.2f}x</span></div>'
        f'<div class="m"><span class="ml">1-Day</span><span class="mv" style="color:{"#00ff88"if mom["ret_1d"]>=0 else"#ff4466"}">{_fmt_pct(mom["ret_1d"])}</span></div>'
        f'<div class="m"><span class="ml">1-Week</span><span class="mv" style="color:{"#00ff88"if mom["ret_1w"]>=0 else"#ff4466"}">{_fmt_pct(mom["ret_1w"])}</span></div>'
        f'<div class="m"><span class="ml">1-Month</span><span class="mv" style="color:{"#00ff88"if mom["ret_1m"]>=0 else"#ff4466"}">{_fmt_pct(mom["ret_1m"])}</span></div>'
        f'<div class="m"><span class="ml">3-Month</span><span class="mv" style="color:{"#00ff88"if mom["ret_3m"]>=0 else"#ff4466"}">{_fmt_pct(mom["ret_3m"])}</span></div>'
        f'<div class="m"><span class="ml">Short Float</span><span class="mv">{si["metrics"].get("short_float","N/A")}{"%" if isinstance(si["metrics"].get("short_float"),(int,float)) else ""}</span></div>'
        f'<div class="m"><span class="ml">Days Cover</span><span class="mv">{si["metrics"].get("short_ratio","N/A")}{"d" if isinstance(si["metrics"].get("short_ratio"),(int,float)) else ""}</span></div>'
        f'<div class="m"><span class="ml">P/E</span><span class="mv">{fund["metrics"].get("pe","N/A")}</span></div>'
        f'</div>')

    badges = ""
    mas = tech["mas"]
    if mas.get("above_20"):  badges += '<span class="badge g">Above 20MA</span>'
    if mas.get("above_50"):  badges += '<span class="badge g">Above 50MA</span>'
    if mas.get("above_200"): badges += '<span class="badge g">Above 200MA</span>'
    m = tech["macd"]
    if m["bull_cross"]:     badges += '<span class="badge gold">MACD Cross ✦</span>'
    elif m["above_signal"]: badges += '<span class="badge b">MACD Bull</span>'
    if vol["rel_vol"] >= CONFIG["rel_vol_good"]: badges += '<span class="badge b">High Volume</span>'
    sf = si["metrics"].get("short_float", 0) or 0
    if sf > 15: badges += '<span class="badge r">High Short %</span>'

    cc    = _col(s["composite_score"])
    gold  = "gold-border" if rank == 1 else ""
    tk    = s["ticker"]
    r1d_c = "#00ff88" if mom["ret_1d"] >= 0 else "#ff4466"
    r1w_c = "#00ff88" if mom["ret_1w"] >= 0 else "#ff4466"

    return f"""
  <div class="card {gold}" data-score="{s['composite_score']:.2f}" data-ticker="{tk}">
    <div class="ch">
      <div class="rb">#{rank}</div>
      <div class="ti">
        <div class="tk">{tk}</div>
        <div class="cn">{s["name"][:42]}</div>
        <div class="sc2">{s["sector"]} · {_fmt_cap(s["market_cap"])}</div>
      </div>
      <div class="circle" style="border-color:{cc};color:{cc}">
        <div class="cn2">{s["composite_score"]:.0f}</div>
        <div class="cl">SCORE</div>
      </div>
    </div>
    <div class="pr">
      <span class="cp" id="p-{tk}">${s["price"]:.2f}</span>
      <span id="c-{tk}" style="color:{r1d_c};font-size:.85rem;font-weight:600">{_fmt_pct(mom["ret_1d"])} today</span>
      <span id="w-{tk}" style="color:{r1w_c};font-size:.85rem;margin-left:8px">{_fmt_pct(mom["ret_1w"])} 1W</span>
    </div>
    <div class="badges">{badges or '<span style="color:#3d4f7a;font-size:.75rem">No strong signals</span>'}</div>
    {mets}
    <div class="breakdown"><div class="bt">Score Breakdown</div>{bars}</div>
  </div>"""


def generate_dashboard(stocks: list) -> None:
    # Stocks arrive pre-sorted by composite_score (highest first) from run_scanner().
    # Re-sort here too as belt-and-suspenders.
    stocks = sorted(stocks, key=lambda x: x["composite_score"], reverse=True)

    ts      = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cards   = "".join(_card(i + 1, s) for i, s in enumerate(stocks))
    w       = CONFIG["weights"]
    winfo   = (f"Technical {w['technical']*100:.0f}% · Momentum {w['momentum']*100:.0f}% · "
               f"Volume {w['volume']*100:.0f}% · Fundamentals {w['fundamentals']*100:.0f}% · "
               f"Short Interest {w['short_interest']*100:.0f}%")
    symbols_js = json.dumps([s["ticker"] for s in stocks])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Stock Scanner – {ts}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#060912;color:#dde6ff;font-family:'Segoe UI',-apple-system,sans-serif;min-height:100vh;padding:24px 16px}}
.hdr{{text-align:center;padding:28px 24px 22px;background:linear-gradient(135deg,#0d1433,#1a2040);border-radius:18px;border:1px solid #1e2d5a;margin-bottom:28px}}
.hdr h1{{font-size:2rem;font-weight:800;background:linear-gradient(90deg,#00ff88,#00aaff,#aa55ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:6px}}
.hdr .sub{{color:#5568a0;font-size:.82rem}}.hdr .ts{{color:#3d4f7a;font-size:.78rem;margin-top:4px}}
.live-bar{{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:8px}}
#live-ind{{font-size:.78rem;color:#5568a0;font-family:monospace}}
.live-dot{{width:8px;height:8px;border-radius:50%;background:#5568a0;display:inline-block}}
.live-dot.on{{background:#00ff88;box-shadow:0 0 6px #00ff88;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:18px;max-width:1600px;margin:0 auto}}
.card{{background:linear-gradient(145deg,#0e1628,#111d38);border-radius:16px;padding:18px;border:1px solid #1e2d5a;transition:transform .2s,box-shadow .2s}}
.card:hover{{transform:translateY(-4px);box-shadow:0 14px 44px rgba(0,80,255,.14)}}
.card.gold-border{{border:2px solid #ffd700;box-shadow:0 0 24px rgba(255,215,0,.12)}}
.ch{{display:flex;align-items:flex-start;gap:12px;margin-bottom:12px}}
.rb{{background:rgba(255,255,255,.06);color:#8898cc;width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:.82rem;font-weight:700;flex-shrink:0}}
.gold-border .rb{{background:rgba(255,215,0,.18);color:#ffd700}}
.ti{{flex:1;min-width:0}}.tk{{font-size:1.55rem;font-weight:800;color:#fff;letter-spacing:.4px}}
.cn{{font-size:.8rem;color:#8898cc;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sc2{{font-size:.72rem;color:#5568a0;margin-top:2px}}
.circle{{width:66px;height:66px;border-radius:50%;border:3px solid;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0}}
.cn2{{font-size:1.4rem;font-weight:800;line-height:1}}.cl{{font-size:.5rem;letter-spacing:1.2px;opacity:.65;margin-top:2px}}
.pr{{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}}.cp{{font-size:1.35rem;font-weight:700;color:#fff;transition:color .3s}}
.price-flash{{animation:flash .6s ease}}
@keyframes flash{{0%{{opacity:.3}}100%{{opacity:1}}}}
.badges{{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;min-height:24px}}
.badge{{padding:3px 8px;border-radius:6px;font-size:.7rem;font-weight:600}}
.badge.g{{background:rgba(0,255,136,.13);color:#00ff88;border:1px solid rgba(0,255,136,.28)}}
.badge.b{{background:rgba(0,170,255,.13);color:#00aaff;border:1px solid rgba(0,170,255,.28)}}
.badge.gold{{background:rgba(255,215,0,.15);color:#ffd700;border:1px solid rgba(255,215,0,.30)}}
.badge.r{{background:rgba(255,68,102,.13);color:#ff4466;border:1px solid rgba(255,68,102,.28)}}
.mg{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;background:rgba(255,255,255,.028);border-radius:10px;padding:10px;margin-bottom:12px}}
.m{{display:flex;flex-direction:column;align-items:center}}
.ml{{font-size:.63rem;color:#5568a0;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}}
.mv{{font-size:.88rem;font-weight:700;color:#c0ccee}}
.breakdown{{background:rgba(0,0,0,.2);border-radius:10px;padding:11px}}
.bt{{font-size:.68rem;color:#5568a0;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}}
.sr{{display:flex;align-items:center;gap:7px;margin-bottom:5px}}
.sl{{font-size:.7rem;color:#8898cc;width:86px;flex-shrink:0}}
.sb-wrap{{flex:1;height:6px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden}}
.sb{{height:100%;border-radius:3px}}.sv{{font-size:.72rem;font-weight:700;width:26px;text-align:right}}
.ft{{text-align:center;margin-top:28px;color:#3d4f7a;font-size:.77rem;line-height:1.6}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}};body{{padding:10px}}}}
</style>
</head>
<body>
<div class="hdr">
  <h1>📊 Stock Scanner</h1>
  <div class="sub">Top {len(stocks)} picks · {winfo}</div>
  <div class="ts">Scan: {ts}</div>
  <div class="live-bar">
    <span class="live-dot" id="live-dot"></span>
    <span id="live-ind">Connecting…</span>
  </div>
</div>
<div class="grid" id="grid">{cards}</div>
<div class="ft">
  <p>⚠️ For informational purposes only – not financial advice.</p>
  <p>Data: Yahoo Finance · Finviz &nbsp;|&nbsp; Universe: {CONFIG["universe_method"]} ({CONFIG["max_stocks"]} stocks scanned)</p>
</div>

<script>
(function(){{
  const PORT    = {PRICE_PORT};
  const SYMBOLS = {symbols_js};
  const dot     = document.getElementById('live-dot');
  const ind     = document.getElementById('live-ind');
  let   prevPrices = {{}};

  function fmt(n, d=2){{ return n >= 0 ? '+'+n.toFixed(d)+'%' : n.toFixed(d)+'%'; }}

  function flash(el){{
    el.classList.remove('price-flash');
    void el.offsetWidth;
    el.classList.add('price-flash');
  }}

  function update(){{
    fetch('http://localhost:'+PORT+'/prices?symbols='+SYMBOLS.join(','))
      .then(r => r.json())
      .then(data => {{
        dot.className = 'live-dot on';
        ind.textContent = '🟢 Live · ' + new Date().toLocaleTimeString();

        Object.entries(data).forEach(([sym, info]) => {{
          // Price
          const pe = document.getElementById('p-'+sym);
          if (pe && info.price !== undefined) {{
            const newTxt = '$' + info.price.toFixed(2);
            if (pe.textContent !== newTxt) {{ pe.textContent = newTxt; flash(pe); }}
          }}
          // Today's change %
          const ce = document.getElementById('c-'+sym);
          if (ce && info.change_pct !== undefined) {{
            const pct = info.change_pct;
            ce.textContent = fmt(pct) + ' today';
            ce.style.color = pct >= 0 ? '#00ff88' : '#ff4466';
          }}
        }});
      }})
      .catch(() => {{
        dot.className = 'live-dot';
        ind.textContent = '⚫ Offline – prices from last scan';
      }});
  }}

  // Sort cards by data-score descending (belt-and-suspenders)
  const grid  = document.getElementById('grid');
  const cards = [...grid.querySelectorAll('.card')];
  cards.sort((a,b) => parseFloat(b.dataset.score) - parseFloat(a.dataset.score));
  cards.forEach(c => grid.appendChild(c));

  setInterval(update, 3000);
  setTimeout(update, 800);
}})();
</script>
</body></html>"""

    with open(CONFIG["output_file"], "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info(f"Dashboard saved → {CONFIG['output_file']}")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    top = run_scanner()
    if top:
        generate_dashboard(top)
        log.info(f"\nOpen  {CONFIG['output_file']}  in your browser.\n")
    else:
        log.error("No stocks found.")
        sys.exit(1)
