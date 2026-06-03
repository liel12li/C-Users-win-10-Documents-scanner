#!/usr/bin/env python3
"""
Stock Scanner
Scans a universe of US stocks and returns the top-N based on a weighted
composite score built from 5 factors:
  - Technical   (RSI, MACD, Moving Averages)
  - Momentum    (multi-timeframe price returns)
  - Volume      (relative volume, volume trend)
  - Fundamentals (P/E, EPS growth, revenue growth, profit margin)
  - Short Interest (% float shorted, days-to-cover)

Run:  python scanner.py
Output: scanner_output.html  (open in any browser)
"""

import sys
import time
import logging
import warnings
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from config import CONFIG

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# STOCK UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "ORCL", "NFLX", "ADBE", "AMD", "QCOM", "CRM", "NOW", "UBER",
    "SNOW", "PLTR", "COIN", "JPM", "BAC", "GS", "V", "MA",
    "XOM", "CVX", "LLY", "UNH", "JNJ", "ABBV", "WMT", "COST",
    "HD", "NKE", "SBUX", "DIS", "PYPL", "INTC", "MU", "AMAT",
]

NASDAQ_100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "GOOG",
    "AVGO", "ORCL", "COST", "NFLX", "ADBE", "AMD", "CSCO", "QCOM",
    "TMUS", "INTU", "PEP", "TXN", "CMCSA", "AMGN", "HON", "AMAT",
    "ISRG", "BKNG", "VRTX", "ADP", "MU", "REGN", "PANW", "SBUX",
    "MDLZ", "GILD", "ADI", "LRCX", "SNPS", "KLAC", "MELI", "CDNS",
    "CRWD", "INTC", "CTAS", "ASML", "FTNT", "MAR", "ABNB", "MRVL",
    "PAYX", "ORLY", "DXCM", "MNST", "IDXX", "PCAR", "KDP", "AEP",
    "CEG", "EXC", "XEL", "ROST", "FAST", "ODFL", "VRSK", "GEHC",
    "CTSH", "BIIB", "ON", "CPRT", "FANG", "BKR", "GFS", "ZS",
    "DLTR", "MDB", "TTWO", "WBD", "WBA", "LCID", "RIVN",
]


def _get_sp500() -> list:
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        df = pd.read_html(url, header=0)[0]
        return df["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as exc:
        log.warning(f"S&P 500 fetch failed: {exc}")
        return []


def _get_finviz(filters: dict) -> list:
    try:
        from finvizfinance.screener.overview import Overview
        screen = Overview()
        screen.set_filter(filters_dict=filters)
        df = screen.screener_view()
        if df is not None and len(df) > 0:
            return df["Ticker"].tolist()
    except Exception as exc:
        log.warning(f"Finviz screener failed: {exc}")
    return []


def get_universe() -> list:
    method = CONFIG["universe_method"]
    tickers: list = []

    if method == "custom":
        tickers = CONFIG["custom_tickers"]
    elif method == "finviz":
        log.info("Fetching universe from Finviz screener…")
        tickers = _get_finviz(CONFIG["finviz_filters"])
    elif method == "nasdaq100":
        tickers = NASDAQ_100
    elif method == "sp500":
        log.info("Fetching S&P 500 tickers from Wikipedia…")
        tickers = _get_sp500()

    if not tickers:
        log.warning("Primary universe source failed – using fallback ticker list.")
        tickers = FALLBACK_TICKERS

    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order
    cap = CONFIG["max_stocks"]
    log.info(f"Universe: {len(tickers)} tickers (capped at {cap})")
    return tickers[:cap]


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch(ticker: str) -> Optional[dict]:
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo", auto_adjust=True)
        if hist is None or len(hist) < 22:
            return None
        info = stock.info or {}
        return {"ticker": ticker, "history": hist, "info": info}
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _macd(prices: pd.Series, fast=12, slow=26, sig=9) -> dict:
    ema_f = prices.ewm(span=fast, adjust=False).mean()
    ema_s = prices.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    signal = line.ewm(span=sig, adjust=False).mean()
    hist = line - signal
    cross = False
    if len(line) >= 2:
        cross = bool(line.iloc[-1] > signal.iloc[-1] and line.iloc[-2] <= signal.iloc[-2])
    return {
        "macd":        float(line.iloc[-1]),
        "signal":      float(signal.iloc[-1]),
        "histogram":   float(hist.iloc[-1]),
        "hist_prev":   float(hist.iloc[-2]) if len(hist) >= 2 else 0.0,
        "bull_cross":  cross,
        "above_signal": bool(line.iloc[-1] > signal.iloc[-1]),
    }


def _mas(prices: pd.Series) -> dict:
    p = float(prices.iloc[-1])

    def sma(n):
        if len(prices) >= n:
            v = float(prices.rolling(n).mean().iloc[-1])
            return v if pd.notna(v) else None
        return None

    s20, s50 = sma(20), sma(50)
    s200 = sma(200) if len(prices) >= 180 else sma(len(prices) // 2)
    return {
        "price":       p,
        "sma20":       s20,
        "sma50":       s50,
        "sma200":      s200,
        "above_20":    p > s20  if s20  else None,
        "above_50":    p > s50  if s50  else None,
        "above_200":   p > s200 if s200 else None,
    }


def _bollinger(prices: pd.Series, n=20, k=2) -> dict:
    mu  = prices.rolling(n).mean()
    sig = prices.rolling(n).std()
    upper = mu + k * sig
    lower = mu - k * sig
    p = float(prices.iloc[-1])
    u = float(upper.iloc[-1])
    l = float(lower.iloc[-1])
    band = u - l
    pos  = (p - l) / band if band > 0 else 0.5
    return {"upper": u, "middle": float(mu.iloc[-1]), "lower": l, "pct_b": float(np.clip(pos, 0, 1))}


# ─────────────────────────────────────────────────────────────────────────────
# SCORING FUNCTIONS  (each returns score 0-100 + raw metrics)
# ─────────────────────────────────────────────────────────────────────────────

def score_technical(data: dict) -> dict:
    prices = data["history"]["Close"]
    rsi   = _rsi(prices)
    macd  = _macd(prices)
    mas   = _mas(prices)
    bb    = _bollinger(prices)

    lo, hi = CONFIG["rsi_sweet_spot"]
    if lo <= rsi <= hi:
        rsi_score = 100
    elif CONFIG["rsi_oversold"] <= rsi < lo:
        rsi_score = 75
    elif hi < rsi <= CONFIG["rsi_overbought"]:
        rsi_score = 60
    elif rsi < CONFIG["rsi_oversold"]:
        rsi_score = 45
    else:
        rsi_score = 25

    if macd["bull_cross"]:
        macd_score = 100
    elif macd["above_signal"] and macd["histogram"] > macd["hist_prev"]:
        macd_score = 85
    elif macd["above_signal"]:
        macd_score = 68
    elif macd["histogram"] > macd["hist_prev"]:
        macd_score = 50
    else:
        macd_score = 22

    true_ma = sum(1 for v in [mas["above_20"], mas["above_50"], mas["above_200"]] if v is True)
    ma_score = 15 + true_ma * 28

    tech_score = rsi_score * 0.35 + macd_score * 0.35 + ma_score * 0.30
    return {
        "score":      float(np.clip(tech_score, 0, 100)),
        "rsi":        rsi,
        "rsi_score":  rsi_score,
        "macd":       macd,
        "macd_score": macd_score,
        "mas":        mas,
        "ma_score":   ma_score,
        "bb":         bb,
    }


def score_momentum(data: dict) -> dict:
    prices = data["history"]["Close"]

    def ret(n):
        if len(prices) > n:
            return float((prices.iloc[-1] / prices.iloc[-n] - 1) * 100)
        return 0.0

    r1d = ret(1)
    r1w = ret(5)
    r1m = ret(21)
    r3m = ret(63)

    def s(r, thr_ok, thr_great):
        if r >= thr_great:
            return 100.0
        if r >= thr_ok:
            return 50 + 50 * (r - thr_ok) / (thr_great - thr_ok)
        if r >= 0:
            return 50 * r / thr_ok
        return max(0.0, 50 + r * 2.5)

    mom_score = (
        s(r1d, 0.5,  2.0)  * 0.10 +
        s(r1w, 2.0,  5.0)  * 0.20 +
        s(r1m, 5.0, 15.0)  * 0.30 +
        s(r3m, 10., 30.0)  * 0.40
    )
    return {
        "score": float(np.clip(mom_score, 0, 100)),
        "ret_1d": r1d, "ret_1w": r1w, "ret_1m": r1m, "ret_3m": r3m,
    }


def score_volume(data: dict) -> dict:
    hist    = data["history"]
    vols    = hist["Volume"].astype(float)
    prices  = hist["Close"]

    avg_vol  = float(vols.iloc[:-1].tail(20).mean()) or 1.0
    cur_vol  = float(vols.iloc[-1])
    rel_vol  = cur_vol / avg_vol

    recent   = vols.tail(5).mean()
    prior    = vols.iloc[-10:-5].mean() if len(vols) >= 10 else avg_vol
    vol_trend = float(recent / prior) if prior > 0 else 1.0

    pc = prices.pct_change().tail(10)
    vc = vols.pct_change().tail(10)
    up_v   = vc[pc > 0].mean() if len(vc[pc > 0]) > 0 else 0.0
    down_v = vc[pc < 0].mean() if len(vc[pc < 0]) > 0 else 0.0
    pvr    = float(up_v - down_v)

    grt, gd = CONFIG["rel_vol_great"], CONFIG["rel_vol_good"]
    if rel_vol >= grt:
        rv_score = 100
    elif rel_vol >= grt * 0.67:
        rv_score = 85
    elif rel_vol >= gd:
        rv_score = 70
    elif rel_vol >= 1.0:
        rv_score = 50
    elif rel_vol >= 0.7:
        rv_score = 35
    else:
        rv_score = 18

    vt_score = 100 if vol_trend >= 1.2 else 70 if vol_trend >= 1.0 else 50 if vol_trend >= 0.8 else 28
    pvr_score = float(np.clip(60 + pvr * 20, 0, 100))

    vol_score = rv_score * 0.50 + vt_score * 0.30 + pvr_score * 0.20
    return {
        "score":      float(np.clip(vol_score, 0, 100)),
        "cur_vol":    cur_vol,
        "avg_vol":    avg_vol,
        "rel_vol":    rel_vol,
        "vol_trend":  vol_trend,
    }


def score_fundamentals(data: dict) -> dict:
    info   = data["info"]
    scores = []
    mets   = {}

    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe and 0 < pe < 1000:
        mets["pe"] = round(float(pe), 1)
        pe_s = (90 if pe < 15 else 75 if pe < 25 else 60 if pe < 35 else 45 if pe < 50 else 25)
        scores.append((pe_s, 0.25))

    epsg = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
    if epsg is not None:
        mets["eps_growth"] = round(float(epsg) * 100, 1)
        eg = float(epsg)
        epsg_s = (100 if eg > .30 else 80 if eg > .15 else 65 if eg > .05 else 50 if eg > 0 else 30 if eg > -.10 else 10)
        scores.append((epsg_s, 0.30))

    rg = info.get("revenueGrowth")
    if rg is not None:
        mets["rev_growth"] = round(float(rg) * 100, 1)
        rg = float(rg)
        rg_s = (100 if rg > .20 else 75 if rg > .10 else 60 if rg > .05 else 45 if rg > 0 else 20)
        scores.append((rg_s, 0.25))

    pm = info.get("profitMargins")
    if pm is not None:
        mets["profit_margin"] = round(float(pm) * 100, 1)
        pm = float(pm)
        pm_s = (100 if pm > .25 else 80 if pm > .15 else 60 if pm > .05 else 40 if pm > 0 else 10)
        scores.append((pm_s, 0.20))

    if not scores:
        return {"score": 50.0, "metrics": mets}

    total_w = sum(w for _, w in scores)
    fund_score = sum(s * w for s, w in scores) / total_w
    return {"score": float(np.clip(fund_score, 0, 100)), "metrics": mets}


def score_short_interest(data: dict) -> dict:
    info = data["info"]
    mets: dict = {}

    sf = info.get("shortPercentOfFloat")
    sr = info.get("shortRatio")

    if sf is not None:
        sf_pct = float(sf) * 100
        mets["short_float"] = round(sf_pct, 1)
        sf_score = (95 if sf_pct < 2 else 80 if sf_pct < 5 else 65 if sf_pct < 10
                    else 45 if sf_pct < 15 else 30 if sf_pct < 20 else 15)
    else:
        sf_score = 60

    if sr is not None:
        sr = float(sr)
        mets["short_ratio"] = round(sr, 1)
        sr_score = (90 if sr < 2 else 75 if sr < 3 else 55 if sr < 5 else 35 if sr < 8 else 18)
        final = sf_score * 0.60 + sr_score * 0.40 if sf is not None else sr_score
    else:
        final = sf_score

    return {"score": float(np.clip(final, 0, 100)), "metrics": mets}


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE SCORER
# ─────────────────────────────────────────────────────────────────────────────

def analyze(ticker: str) -> Optional[dict]:
    raw = fetch(ticker)
    if raw is None:
        return None
    try:
        tech  = score_technical(raw)
        mom   = score_momentum(raw)
        vol   = score_volume(raw)
        fund  = score_fundamentals(raw)
        si    = score_short_interest(raw)

        w = CONFIG["weights"]
        composite = (
            tech["score"]  * w["technical"]      +
            mom["score"]   * w["momentum"]        +
            vol["score"]   * w["volume"]           +
            fund["score"]  * w["fundamentals"]     +
            si["score"]    * w["short_interest"]
        )

        info  = raw["info"]
        price = float(raw["history"]["Close"].iloc[-1])
        return {
            "ticker":          ticker,
            "name":            info.get("longName", info.get("shortName", ticker)),
            "sector":          info.get("sector",   "—"),
            "industry":        info.get("industry", "—"),
            "market_cap":      info.get("marketCap"),
            "price":           price,
            "composite_score": float(np.clip(composite, 0, 100)),
            "technical":       tech,
            "momentum":        mom,
            "volume":          vol,
            "fundamentals":    fund,
            "short_interest":  si,
        }
    except Exception as exc:
        log.debug(f"  {ticker}: analysis error – {exc}")
        return None


def run_scanner() -> list:
    tickers = get_universe()
    results = []
    total   = len(tickers)

    for i, t in enumerate(tickers):
        result = analyze(t)
        if result:
            results.append(result)
        if (i + 1) % 20 == 0 or (i + 1) == total:
            log.info(f"  Progress: {i+1}/{total} scanned | {len(results)} valid")
        time.sleep(CONFIG["request_delay"])

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results[: CONFIG["top_n"]]


# ─────────────────────────────────────────────────────────────────────────────
# HTML DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_cap(mc) -> str:
    if mc is None:
        return "N/A"
    if mc >= 1e12:
        return f"${mc/1e12:.1f}T"
    if mc >= 1e9:
        return f"${mc/1e9:.1f}B"
    if mc >= 1e6:
        return f"${mc/1e6:.1f}M"
    return f"${mc:,.0f}"


def _fmt_pct(val, decimals=1) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{decimals}f}%"


def _score_color(score: float) -> str:
    if score >= 75:
        return "#00ff88"
    if score >= 55:
        return "#ffd700"
    if score >= 38:
        return "#ff9944"
    return "#ff4466"


def _build_card(rank: int, s: dict) -> str:
    tech = s["technical"]
    mom  = s["momentum"]
    vol  = s["volume"]
    fund = s["fundamentals"]
    si   = s["short_interest"]

    # ── Score breakdown bars ──────────────────────────────────────────────
    cats = [
        ("Technical",     tech["score"]),
        ("Momentum",      mom["score"]),
        ("Volume",        vol["score"]),
        ("Fundamentals",  fund["score"]),
        ("Short Interest",si["score"]),
    ]
    bars = ""
    for cat, sc in cats:
        col = _score_color(sc)
        bars += f"""
        <div class="sr">
          <span class="sl">{cat}</span>
          <div class="sb-wrap"><div class="sb" style="width:{sc:.0f}%;background:{col}"></div></div>
          <span class="sv" style="color:{col}">{sc:.0f}</span>
        </div>"""

    # ── Metrics grid ─────────────────────────────────────────────────────
    rsi_col  = _score_color(tech["rsi_score"])
    rv_col   = _score_color(vol["score"])
    r1d_col  = "#00ff88" if mom["ret_1d"] >= 0 else "#ff4466"
    r1m_col  = "#00ff88" if mom["ret_1m"] >= 0 else "#ff4466"

    pe_val  = fund["metrics"].get("pe", "N/A")
    sf_val  = si["metrics"].get("short_float")
    sf_str  = f"{sf_val}%" if sf_val is not None else "N/A"
    sr_val  = si["metrics"].get("short_ratio")
    sr_str  = f"{sr_val}d" if sr_val is not None else "N/A"
    epsg    = fund["metrics"].get("eps_growth")
    epsg_str= f"{_fmt_pct(epsg)}" if epsg is not None else "N/A"

    metrics = f"""
    <div class="mg">
      <div class="m"><span class="ml">RSI 14</span><span class="mv" style="color:{rsi_col}">{tech["rsi"]:.1f}</span></div>
      <div class="m"><span class="ml">Rel. Vol</span><span class="mv" style="color:{rv_col}">{vol["rel_vol"]:.2f}x</span></div>
      <div class="m"><span class="ml">1-Day</span><span class="mv" style="color:{r1d_col}">{_fmt_pct(mom["ret_1d"])}</span></div>
      <div class="m"><span class="ml">1-Month</span><span class="mv" style="color:{r1m_col}">{_fmt_pct(mom["ret_1m"])}</span></div>
      <div class="m"><span class="ml">Short Float</span><span class="mv">{sf_str}</span></div>
      <div class="m"><span class="ml">Days Cover</span><span class="mv">{sr_str}</span></div>
      <div class="m"><span class="ml">P/E</span><span class="mv">{pe_val}</span></div>
      <div class="m"><span class="ml">EPS Growth</span><span class="mv">{epsg_str}</span></div>
      <div class="m"><span class="ml">3-Month</span><span class="mv" style="color:{'#00ff88' if mom['ret_3m']>=0 else '#ff4466'}">{_fmt_pct(mom["ret_3m"])}</span></div>
    </div>"""

    # ── Signal badges ─────────────────────────────────────────────────────
    badges = ""
    mas = tech["mas"]
    if mas.get("above_20"):  badges += '<span class="badge g">Above 20MA</span>'
    if mas.get("above_50"):  badges += '<span class="badge g">Above 50MA</span>'
    if mas.get("above_200"): badges += '<span class="badge g">Above 200MA</span>'
    m = tech["macd"]
    if m["bull_cross"]:    badges += '<span class="badge gold">MACD Cross ✦</span>'
    elif m["above_signal"]: badges += '<span class="badge b">MACD Bull</span>'
    if vol["rel_vol"] >= CONFIG["rel_vol_good"]:
        badges += '<span class="badge b">High Volume</span>'
    si_sf = si["metrics"].get("short_float", 100)
    if si_sf > 15:
        badges += '<span class="badge r">High Short</span>'

    comp_col  = _score_color(s["composite_score"])
    rank_col  = "gold-border" if rank == 1 else ""
    r1w_col   = "#00ff88" if mom["ret_1w"] >= 0 else "#ff4466"

    return f"""
  <div class="card {rank_col}">
    <div class="ch">
      <div class="rb">#{rank}</div>
      <div class="ti">
        <div class="tk">{s["ticker"]}</div>
        <div class="cn">{s["name"][:40]}</div>
        <div class="sc2">{s["sector"]} · {_fmt_cap(s["market_cap"])}</div>
      </div>
      <div class="circle" style="border-color:{comp_col};color:{comp_col}">
        <div class="cn2">{s["composite_score"]:.0f}</div>
        <div class="cl">SCORE</div>
      </div>
    </div>
    <div class="pr">
      <span class="cp">${s["price"]:.2f}</span>
      <span style="color:{r1d_col};font-size:.85rem;font-weight:600">{_fmt_pct(mom["ret_1d"])} today</span>
      <span style="color:{r1w_col};font-size:.85rem;margin-left:8px">{_fmt_pct(mom["ret_1w"])} 1W</span>
    </div>
    <div class="badges">{badges if badges else '<span style="color:#3d4f7a;font-size:.75rem">No strong signals</span>'}</div>
    {metrics}
    <div class="breakdown">
      <div class="bt">Score Breakdown</div>
      {bars}
    </div>
  </div>"""


def generate_dashboard(stocks: list) -> None:
    ts     = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cards  = "".join(_build_card(i + 1, s) for i, s in enumerate(stocks))
    w      = CONFIG["weights"]
    winfo  = (f"Technical {w['technical']*100:.0f}% · "
              f"Momentum {w['momentum']*100:.0f}% · "
              f"Volume {w['volume']*100:.0f}% · "
              f"Fundamentals {w['fundamentals']*100:.0f}% · "
              f"Short Interest {w['short_interest']*100:.0f}%")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Stock Scanner – {ts}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#060912;color:#dde6ff;font-family:'Segoe UI',-apple-system,sans-serif;min-height:100vh;padding:24px 16px}}
/* ── Header ── */
.hdr{{text-align:center;padding:28px 24px 22px;background:linear-gradient(135deg,#0d1433,#1a2040);border-radius:18px;border:1px solid #1e2d5a;margin-bottom:28px}}
.hdr h1{{font-size:2rem;font-weight:800;background:linear-gradient(90deg,#00ff88,#00aaff,#aa55ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:6px}}
.hdr .sub{{color:#5568a0;font-size:.82rem}}
.hdr .ts{{color:#3d4f7a;font-size:.78rem;margin-top:6px}}
/* ── Grid ── */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:18px;max-width:1600px;margin:0 auto}}
/* ── Card ── */
.card{{background:linear-gradient(145deg,#0e1628,#111d38);border-radius:16px;padding:18px;border:1px solid #1e2d5a;transition:transform .2s,box-shadow .2s}}
.card:hover{{transform:translateY(-4px);box-shadow:0 14px 44px rgba(0,80,255,.14)}}
.card.gold-border{{border:2px solid #ffd700;box-shadow:0 0 24px rgba(255,215,0,.12)}}
/* ── Card header ── */
.ch{{display:flex;align-items:flex-start;gap:12px;margin-bottom:12px}}
.rb{{background:rgba(255,255,255,.06);color:#8898cc;width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:.82rem;font-weight:700;flex-shrink:0}}
.gold-border .rb{{background:rgba(255,215,0,.18);color:#ffd700}}
.ti{{flex:1;min-width:0}}
.tk{{font-size:1.55rem;font-weight:800;color:#fff;letter-spacing:.4px}}
.cn{{font-size:.8rem;color:#8898cc;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sc2{{font-size:.72rem;color:#5568a0;margin-top:2px}}
.circle{{width:66px;height:66px;border-radius:50%;border:3px solid;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0}}
.cn2{{font-size:1.4rem;font-weight:800;line-height:1}}
.cl{{font-size:.5rem;letter-spacing:1.2px;opacity:.65;margin-top:2px}}
/* ── Price row ── */
.pr{{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}}
.cp{{font-size:1.35rem;font-weight:700;color:#fff}}
/* ── Badges ── */
.badges{{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;min-height:24px}}
.badge{{padding:3px 8px;border-radius:6px;font-size:.7rem;font-weight:600}}
.badge.g{{background:rgba(0,255,136,.13);color:#00ff88;border:1px solid rgba(0,255,136,.28)}}
.badge.b{{background:rgba(0,170,255,.13);color:#00aaff;border:1px solid rgba(0,170,255,.28)}}
.badge.gold{{background:rgba(255,215,0,.15);color:#ffd700;border:1px solid rgba(255,215,0,.30)}}
.badge.r{{background:rgba(255,68,102,.13);color:#ff4466;border:1px solid rgba(255,68,102,.28)}}
/* ── Metrics grid ── */
.mg{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;background:rgba(255,255,255,.028);border-radius:10px;padding:10px;margin-bottom:12px}}
.m{{display:flex;flex-direction:column;align-items:center}}
.ml{{font-size:.63rem;color:#5568a0;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}}
.mv{{font-size:.88rem;font-weight:700;color:#c0ccee}}
/* ── Breakdown ── */
.breakdown{{background:rgba(0,0,0,.2);border-radius:10px;padding:11px}}
.bt{{font-size:.68rem;color:#5568a0;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}}
.sr{{display:flex;align-items:center;gap:7px;margin-bottom:5px}}
.sl{{font-size:.7rem;color:#8898cc;width:86px;flex-shrink:0}}
.sb-wrap{{flex:1;height:6px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden}}
.sb{{height:100%;border-radius:3px}}
.sv{{font-size:.72rem;font-weight:700;width:26px;text-align:right}}
/* ── Footer ── */
.ft{{text-align:center;margin-top:28px;color:#3d4f7a;font-size:.77rem;line-height:1.6}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}};body{{padding:10px}}}}
</style>
</head>
<body>
<div class="hdr">
  <h1>📊 Stock Scanner</h1>
  <div class="sub">Top {len(stocks)} picks · Multi-factor scoring · {winfo}</div>
  <div class="ts">Generated: {ts}</div>
</div>
<div class="grid">
{cards}
</div>
<div class="ft">
  <p>⚠️ For informational purposes only – not financial advice.</p>
  <p>Data: Yahoo Finance · Finviz &nbsp;|&nbsp; Universe: {CONFIG["universe_method"]} ({CONFIG["max_stocks"]} stocks scanned)</p>
</div>
</body>
</html>"""

    path = CONFIG["output_file"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info(f"Dashboard saved → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("  STOCK SCANNER  –  starting")
    log.info("=" * 55)

    top = run_scanner()

    if not top:
        log.error("No stocks survived screening. Check connectivity.")
        sys.exit(1)

    log.info("\n" + "─" * 40)
    log.info("  TOP RESULTS")
    log.info("─" * 40)
    for i, s in enumerate(top, 1):
        log.info(
            f"  {i}. {s['ticker']:<6}  score={s['composite_score']:5.1f}"
            f"  price=${s['price']:.2f}"
            f"  {s['name'][:30]}"
        )
    log.info("─" * 40)

    generate_dashboard(top)
    log.info(f"\nOpen  {CONFIG['output_file']}  in your browser.\n")


if __name__ == "__main__":
    main()
