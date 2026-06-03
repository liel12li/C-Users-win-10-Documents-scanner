#!/usr/bin/env python3
"""Stock Scanner – GUI launcher.  Run: python app.py"""

import os
import sys
import subprocess
import importlib

# ── Auto-install missing packages BEFORE any other import ────────────────────
_REQUIRED = ["numpy", "pandas", "yfinance", "requests", "bs4", "lxml", "finvizfinance"]

def _ensure_deps():
    missing = []
    for pkg in _REQUIRED:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Installing missing packages: {', '.join(missing)} …")
        req = os.path.join(os.path.dirname(__file__), "requirements.txt")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", req, "-q"],
            stdout=subprocess.DEVNULL,
        )
        print("Done.")

_ensure_deps()
# ─────────────────────────────────────────────────────────────────────────────

import threading
import logging
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#060912"
BG2    = "#0d1433"
BG3    = "#111d38"
ACCENT = "#00cc66"
BLUE   = "#0055ff"
TEXT   = "#dde6ff"
MUTED  = "#5568a0"
LOG_FG = "#6878aa"
RED    = "#cc2244"


# ─────────────────────────────────────────────────────────────────────────────
# Logging → Tkinter Text widget
# ─────────────────────────────────────────────────────────────────────────────
class WidgetLog(logging.Handler):
    def __init__(self, widget: tk.Text):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.widget.after(0, self._write, msg)

    def _write(self, msg):
        self.widget.config(state="normal")
        self.widget.insert(tk.END, msg)
        self.widget.see(tk.END)
        self.widget.config(state="disabled")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _lbl(parent, text, size=10, bold=False, color=TEXT, bg=BG, anchor="w", **kw):
    font = ("Segoe UI", size, "bold" if bold else "normal")
    return tk.Label(parent, text=text, font=font, fg=color, bg=bg, anchor=anchor, **kw)

def _combo(parent, var, values, width=28):
    cb = ttk.Combobox(parent, textvariable=var, values=values,
                      state="readonly", font=("Segoe UI", 10), width=width)
    return cb

def _spin(parent, var, lo, hi, step=1, width=8):
    return tk.Spinbox(parent, from_=lo, to=hi, increment=step,
                      textvariable=var, font=("Segoe UI", 10), width=width,
                      bg=BG3, fg=TEXT, insertbackground=TEXT,
                      buttonbackground=BG2, relief="flat")

def _row(frame, label, widget_fn, pady=5, tip=None):
    f = tk.Frame(frame, bg=BG)
    f.pack(fill="x", padx=18, pady=pady)
    _lbl(f, label, width=26).pack(side="left")
    w = widget_fn(f)
    w.pack(side="left")
    if tip:
        _help_btn(f, label, tip).pack(side="left", padx=6)
    return w

def _section(parent, title, tip=None):
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", padx=10, pady=(10, 2))
    inner = tk.Frame(f, bg=BG)
    inner.pack(anchor="w", padx=8)
    _lbl(inner, f"── {title} ──", size=9, color="#3d5488", bg=BG).pack(side="left")
    if tip:
        _help_btn(inner, title, tip).pack(side="left", padx=4)
    return f


# ── Tooltip texts (Hebrew) ────────────────────────────────────────────────────
_TIPS = {
    "Universe": (
        "מאיפה הסורק לוקח את רשימת המניות לבדיקה\n\n"
        "• Finviz – שולח שאילתה לאתר Finviz ומחזיר מניות שעוברות פילטר ראשוני\n"
        "• Nasdaq 100 – 100 המניות הגדולות בנאסד\"ק\n"
        "• S&P 500 – 500 המניות הגדולות בארה\"ב\n"
        "• Custom – רשימה ידנית שאתה מגדיר ב-config.py"
    ),
    "Max stocks to scan": (
        "כמה מניות הסורק יבדוק בכל ריצה\n\n"
        "יותר מניות = תוצאות מדויקות יותר, אבל לוקח יותר זמן.\n"
        "50–80 מניות מומלץ לאיזון בין מהירות לכיסוי."
    ),
    "Top results to show": (
        "כמה מניות יוצגו בדשבורד הסופי\n\n"
        "הסורק מחשב ציון לכל המניות, ומציג רק את הטובות ביותר.\n"
        "ברירת מחדל: 4"
    ),
    "Market Cap": (
        "שווי שוק (Market Cap) = מחיר המניה × מספר המניות בשוק\n\n"
        "• Mega – מעל $200B  (Apple, Microsoft וכו')\n"
        "• Large – $10B–$200B  (חברות גדולות ויציבות)\n"
        "• Mid   – $2B–$10B   (חברות בינוניות, פוטנציאל צמיחה)\n"
        "• Small – $300M–$2B  (חברות קטנות, סיכון גבוה יותר)"
    ),
    "Average Volume": (
        "ממוצע נפח המסחר היומי – כמה מניות נסחרות בממוצע ביום\n\n"
        "נפח גבוה = נזילות גבוהה = קל לקנות ולמכור בלי להזיז את המחיר.\n"
        "מומלץ: לפחות 500K ביום למניות רגילות."
    ),
    "RSI Range": (
        "RSI – Relative Strength Index (מדד עוצמה יחסית), טווח 0–100\n\n"
        "• מתחת ל-30 → מניה 'מוכרת יתר', עשויה לעלות (oversold)\n"
        "• מעל 70    → מניה 'קנויה יתר', עשויה לרדת (overbought)\n"
        "• 40–65     → טווח בריא, מומנטום חיובי\n\n"
        "הסורק נותן ניקוד גבוה יותר למניות ב-RSI 40–65."
    ),
    "RSI minimum": (
        "הסף התחתון של ה-RSI\n\n"
        "מניות עם RSI מתחת לערך זה יסוננו החוצה.\n"
        "ברירת מחדל: 20 (מסנן רק קריסות קיצוניות)"
    ),
    "RSI maximum": (
        "הסף העליון של ה-RSI\n\n"
        "מניות עם RSI מעל ערך זה יסוננו החוצה.\n"
        "ברירת מחדל: 85 (מסנן רק קניית יתר קיצונית)"
    ),
    "Price Range  ($)": (
        "טווח המחיר של המניה בדולרים\n\n"
        "מאפשר להתמקד במניות בטווח מחיר ספציפי.\n"
        "לדוגמה: $20–$200 לסינון מניות 'penny stocks' מחד\n"
        "ומניות יקרות מאוד מאידך."
    ),
    "Volume": (
        "נפח מסחר יחסי (Relative Volume)\n\n"
        "= נפח המסחר היום ÷ ממוצע 20 הימים האחרונים\n\n"
        "• 1.0x = נפח רגיל\n"
        "• 1.5x = 50% יותר מסחר מהרגיל\n"
        "• 3.0x+ = נפח חריג מאוד – לעיתים מסמן תנועה גדולה\n\n"
        "נפח גבוה בימי עלייה = ביקוש חזק = אות חיובי."
    ),
    "Min relative volume": (
        "הנפח היחסי המינימלי שמניה צריכה להציג כדי לעבור את הסינון\n\n"
        "0.3 = לפחות 30% מהנפח הרגיל\n"
        "1.0 = לפחות נפח ממוצע\n"
        "1.5 = לפחות 50% מעל הממוצע (מסנן יותר)"
    ),
    "Market Cap": (
        "שווי שוק מינימלי בביליוני דולר\n\n"
        "0 = ללא הגבלה\n"
        "1 = לפחות $1B (מסנן חברות קטנות מאוד)\n"
        "10 = לפחות $10B (רק חברות גדולות)"
    ),
    "Technical (RSI / MACD / MA)": (
        "ניתוח טכני (Technical Analysis)\n\n"
        "בוחן את תנועת המחיר בגרפים. כולל:\n"
        "• RSI – מדד עוצמה יחסית\n"
        "• MACD – מדד מגמה ותנע (Crossover = אות קנייה)\n"
        "• MA – ממוצעים נעים (20/50/200 יום)\n\n"
        "משקל גבוה = הסורק מעדיף מניות עם גרף טכני חזק."
    ),
    "Momentum  (price returns)": (
        "מומנטום (Momentum) – עוצמת מגמת העלייה לאורך זמן\n\n"
        "מחושב מהתשואה ב-4 מסגרות זמן:\n"
        "• 1 יום  (משקל 10%)\n"
        "• שבוע   (משקל 20%)\n"
        "• חודש   (משקל 30%)\n"
        "• 3 חודשים (משקל 40%)\n\n"
        "משקל גבוה = הסורק מעדיף מניות שעולות לאורך זמן."
    ),
    "Volume  (relative volume)": (
        "נפח מסחר (Volume)\n\n"
        "מחושב מ-3 אלמנטים:\n"
        "• נפח יחסי (50%) – האם היום נסחר יותר מהרגיל?\n"
        "• מגמת נפח (30%) – האם הנפח עולה בימים האחרונים?\n"
        "• יחס מחיר-נפח (20%) – האם הנפח גבוה בימי עלייה?\n\n"
        "משקל גבוה = הסורק מעדיף מניות עם נפח מסחר חריג."
    ),
    "Fundamentals  (P/E, EPS…)": (
        "פונדמנטלים (Fundamentals) – ביצועים פיננסיים\n\n"
        "• P/E – מכפיל רווח (מחיר ÷ רווח למניה). נמוך = זול יותר.\n"
        "• EPS Growth – צמיחת הרווח למניה\n"
        "• Revenue Growth – צמיחת ההכנסות\n"
        "• Profit Margin – מרווח הרווח הנקי\n\n"
        "משקל גבוה = הסורק מעדיף חברות רווחיות וצומחות."
    ),
    "Short Interest": (
        "שורט (Short Interest) – אחוז המניות הממורות (שורט)\n\n"
        "שורט = השקעה שמרוויחה כשהמניה יורדת.\n\n"
        "• שורט נמוך (<5%) = אין לחץ מכירה, סימן חיובי\n"
        "• שורט גבוה (>20%) = הרבה 'מהמרים' נגד המניה\n\n"
        "משקל גבוה = הסורק מעדיף מניות עם שורט נמוך.\n"
        "Short Ratio = ימים שייקח לסגור את כל השורטים."
    ),
    "Sector Filter  (leave empty = all sectors)": (
        "סינון לפי ענף (Sector)\n\n"
        "אם לא בוחרים כלום – הסורק בודק את כל הענפים.\n"
        "אם בוחרים ענפים ספציפיים – רק מניות מאותם ענפים\n"
        "ייכנסו לסריקה.\n\n"
        "לדוגמה: לבחור רק Technology + Healthcare."
    ),
    "Min market cap ($B)": (
        "שווי שוק מינימלי בביליוני דולר ($B)\n\n"
        "0 = ללא הגבלה\n"
        "1 = לפחות $1B\n"
        "10 = לפחות $10B (רק חברות גדולות)"
    ),
    "Scoring Weights  (higher = more influence)": (
        "משקלות הציון (Weights)\n\n"
        "כל מניה מקבלת ציון מורכב (0–100) שמחושב\n"
        "כממוצע משוקלל של 5 פקטורים.\n\n"
        "הגדל את המשקל של הפקטורים שחשובים לך יותר.\n"
        "המשקלות מנורמלות אוטומטית ל-100% בשמירה."
    ),
}

def _help_btn(parent, key: str, tip: str) -> tk.Button:
    """Small '?' button that opens a Hebrew explanation popup."""
    def _show():
        win = tk.Toplevel()
        win.title("הסבר")
        win.configure(bg=BG2)
        win.resizable(False, False)
        win.grab_set()
        # Force right-to-left friendly display
        tk.Label(win, text=key, font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=ACCENT, justify="right").pack(padx=24, pady=(18, 6))
        tk.Frame(win, bg=BG3, height=1).pack(fill="x", padx=20)
        tk.Label(win, text=tip, font=("Segoe UI", 10),
                 bg=BG2, fg=TEXT, justify="right",
                 wraplength=360, anchor="e").pack(padx=24, pady=12)
        tk.Button(win, text="סגור ✕",
                  font=("Segoe UI", 10), bg=BG3, fg=TEXT,
                  activebackground=BG, relief="flat",
                  padx=16, pady=6, cursor="hand2",
                  command=win.destroy).pack(pady=(0, 16))
        win.update_idletasks()
        # Centre relative to screen
        w, h = win.winfo_width(), win.winfo_height()
        sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
        win.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    return tk.Button(parent, text="?",
                     font=("Segoe UI", 8, "bold"),
                     bg=BG3, fg=MUTED,
                     activebackground=ACCENT, activeforeground="white",
                     relief="flat", width=2, padx=0, pady=1,
                     cursor="hand2", command=_show)


# ─────────────────────────────────────────────────────────────────────────────
# Settings Window
# ─────────────────────────────────────────────────────────────────────────────
class SettingsWindow(tk.Toplevel):
    SECTORS = [
        "Technology", "Healthcare", "Financial Services",
        "Consumer Cyclical", "Consumer Defensive", "Industrials",
        "Energy", "Basic Materials", "Real Estate",
        "Communication Services", "Utilities",
    ]

    def __init__(self, parent, cfg: dict, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, True)
        self.grab_set()
        self.on_save = on_save
        self.cfg = cfg

        _lbl(self, "Scanner Settings", size=14, bold=True).pack(pady=(16, 2))
        _lbl(self, "Changes apply on the next run", size=9, color=MUTED).pack()

        # ── Notebook tabs ─────────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook",       background=BG, borderwidth=0)
        style.configure("TNotebook.Tab",   background=BG3, foreground=MUTED,
                        padding=[12, 5], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)], foreground=[("selected", TEXT)])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=10)

        tab1 = tk.Frame(nb, bg=BG)
        tab2 = tk.Frame(nb, bg=BG)
        tab3 = tk.Frame(nb, bg=BG)
        nb.add(tab1, text=" Universe ")
        nb.add(tab2, text=" Filters ")
        nb.add(tab3, text=" Weights ")

        self._build_universe_tab(tab1)
        self._build_filters_tab(tab2)
        self._build_weights_tab(tab3)

        # ── Save ──────────────────────────────────────────────────────────────
        tk.Button(self, text="  ✓  Save Settings  ",
                  font=("Segoe UI", 11, "bold"),
                  bg=ACCENT, fg="white", activebackground="#009944",
                  relief="flat", padx=20, pady=8, cursor="hand2",
                  command=self._save).pack(pady=14)

    # ── Tab 1: Universe ───────────────────────────────────────────────────────
    def _build_universe_tab(self, tab):
        _section(tab, "Data Source")

        self.universe_var = tk.StringVar(value=self.cfg["universe_method"])
        _row(tab, "Universe", lambda f: _combo(f, self.universe_var,
             ["finviz", "nasdaq100", "sp500", "custom"]),
             tip=_TIPS["Universe"])

        self.maxstocks_var = tk.IntVar(value=self.cfg["max_stocks"])
        _row(tab, "Max stocks to scan", lambda f: _spin(f, self.maxstocks_var, 10, 500, 10),
             tip=_TIPS["Max stocks to scan"])

        self.topn_var = tk.IntVar(value=self.cfg["top_n"])
        _row(tab, "Top results to show", lambda f: _spin(f, self.topn_var, 1, 10),
             tip=_TIPS["Top results to show"])

        _section(tab, "Finviz Pre-Filter")

        self.cap_var = tk.StringVar(value=self.cfg["finviz_filters"].get("Market Cap.", "Large ($10bln to $200bln)"))
        _row(tab, "Market Cap", lambda f: _combo(f, self.cap_var, [
            "Mega ($200bln and more)",
            "Large ($10bln to $200bln)",
            "Mid ($2bln to $10bln)",
            "Small ($300mln to $2bln)",
        ]), tip=_TIPS["Market Cap"])

        self.avgvol_var = tk.StringVar(value=self.cfg["finviz_filters"].get("Average Volume", "Over 500K"))
        _row(tab, "Average Volume", lambda f: _combo(f, self.avgvol_var, [
            "Over 100K", "Over 200K", "Over 500K", "Over 1M", "Over 2M",
        ]), tip=_TIPS["Average Volume"])

        _section(tab, "Sector Filter  (leave empty = all sectors)",
                 tip=_TIPS["Sector Filter  (leave empty = all sectors)"])
        f = tk.Frame(tab, bg=BG)
        f.pack(fill="x", padx=18, pady=4)
        _lbl(f, "Only these sectors:", size=9, color=MUTED, bg=BG).pack(anchor="w")
        self.sector_vars: dict[str, tk.BooleanVar] = {}
        only = self.cfg["pre_filters"].get("only_sectors", [])
        grid = tk.Frame(f, bg=BG)
        grid.pack(anchor="w", pady=4)
        for i, sec in enumerate(self.SECTORS):
            v = tk.BooleanVar(value=sec in only)
            self.sector_vars[sec] = v
            cb = tk.Checkbutton(grid, text=sec, variable=v,
                                bg=BG, fg=TEXT, selectcolor=BG3,
                                activebackground=BG, activeforeground=TEXT,
                                font=("Segoe UI", 9))
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=4, pady=1)

    # ── Tab 2: Filters ────────────────────────────────────────────────────────

    # Preset price ranges  (label → (min, max))
    PRICE_PRESETS = {
        "All prices":      (0,    999999),
        "Under $20":       (0,    20),
        "$5 – $50":        (5,    50),
        "$20 – $100":      (20,   100),
        "$50 – $200":      (50,   200),
        "$100 – $500":     (100,  500),
        "$200 – $1,000":   (200,  1000),
        "$500+":           (500,  999999),
        "Custom…":         None,
    }

    def _build_filters_tab(self, tab):
        pf = self.cfg["pre_filters"]

        # ── RSI ───────────────────────────────────────────────────────────────
        _section(tab, "RSI Range", tip=_TIPS["RSI Range"])
        self.rsi_min_var = tk.DoubleVar(value=pf.get("rsi_min", 20))
        self.rsi_max_var = tk.DoubleVar(value=pf.get("rsi_max", 85))
        _row(tab, "RSI minimum", lambda f: _spin(f, self.rsi_min_var, 0, 100),
             tip=_TIPS["RSI minimum"])
        _row(tab, "RSI maximum", lambda f: _spin(f, self.rsi_max_var, 0, 100),
             tip=_TIPS["RSI maximum"])

        # ── Price Range ───────────────────────────────────────────────────────
        _section(tab, "Price Range  ($)", tip=_TIPS["Price Range  ($)"])

        self.minprice_var = tk.DoubleVar(value=pf.get("min_price", 5))
        self.maxprice_var = tk.DoubleVar(value=pf.get("max_price", 5000))

        # Detect current preset
        cur_min = pf.get("min_price", 5)
        cur_max = pf.get("max_price", 5000)
        default_preset = "Custom…"
        for label, rng in self.PRICE_PRESETS.items():
            if rng and rng[0] == cur_min and rng[1] == cur_max:
                default_preset = label
                break

        # Quick-select buttons row
        btn_outer = tk.Frame(tab, bg=BG)
        btn_outer.pack(fill="x", padx=18, pady=(4, 2))
        _lbl(btn_outer, "Quick select:", size=9, color=MUTED, bg=BG).pack(anchor="w", pady=(0, 4))

        btn_frame = tk.Frame(btn_outer, bg=BG)
        btn_frame.pack(anchor="w")

        self._price_preset_var = tk.StringVar(value=default_preset)
        self._price_btns: list[tk.Button] = []

        def _apply_preset(label):
            self._price_preset_var.set(label)
            rng = self.PRICE_PRESETS[label]
            if rng:
                self.minprice_var.set(rng[0])
                self.maxprice_var.set(rng[1])
                self._custom_frame.pack_forget()
            else:
                self._custom_frame.pack(fill="x", padx=18, pady=2)
            # Redraw button colours
            for b in self._price_btns:
                active = b.cget("text") == label
                b.config(
                    bg=ACCENT if active else BG3,
                    fg="white" if active else MUTED,
                )

        # Two rows of buttons (4 per row)
        labels = list(self.PRICE_PRESETS.keys())
        for i, label in enumerate(labels):
            row_frame = btn_frame if i < 4 else None
            if i == 4:
                row_frame = tk.Frame(btn_outer, bg=BG)
                row_frame.pack(anchor="w", pady=(2, 0))
                btn_frame2 = row_frame
            if i >= 4:
                row_frame = btn_frame2
            else:
                row_frame = btn_frame

            active = label == default_preset
            b = tk.Button(
                row_frame,
                text=label,
                font=("Segoe UI", 9),
                bg=ACCENT if active else BG3,
                fg="white" if active else MUTED,
                activebackground=ACCENT,
                relief="flat", padx=8, pady=5, cursor="hand2",
                command=lambda l=label: _apply_preset(l),
            )
            b.pack(side="left", padx=3, pady=1)
            self._price_btns.append(b)

        # Custom fields (shown only when "Custom…" selected)
        self._custom_frame = tk.Frame(tab, bg=BG)
        if default_preset == "Custom…":
            self._custom_frame.pack(fill="x", padx=18, pady=2)

        cf = self._custom_frame
        row_f = tk.Frame(cf, bg=BG)
        row_f.pack(fill="x", pady=2)
        _lbl(row_f, "Min ($)", size=9, width=10, color=TEXT, bg=BG).pack(side="left")
        _spin(row_f, self.minprice_var, 0, 99999, 5).pack(side="left", padx=(0, 20))
        _lbl(row_f, "Max ($)", size=9, width=10, color=TEXT, bg=BG).pack(side="left")
        _spin(row_f, self.maxprice_var, 0, 99999, 50).pack(side="left")

        # ── Volume ────────────────────────────────────────────────────────────
        _section(tab, "Volume", tip=_TIPS["Volume"])
        self.minrv_var = tk.DoubleVar(value=pf.get("min_rel_volume", 0.3))
        _row(tab, "Min relative volume", lambda f: _spin(f, self.minrv_var, 0, 10, 0.1),
             tip=_TIPS["Min relative volume"])

        # ── Market Cap ────────────────────────────────────────────────────────
        _section(tab, "Market Cap")
        self.minmc_var = tk.StringVar(
            value=str(int(pf.get("min_market_cap", 0) / 1e9)) if pf.get("min_market_cap") else "0"
        )
        _row(tab, "Min market cap ($B)", lambda f: _spin(f, self.minmc_var, 0, 10000, 1),
             tip=_TIPS["Min market cap ($B)"])

    # ── Tab 3: Weights ────────────────────────────────────────────────────────
    def _build_weights_tab(self, tab):
        _section(tab, "Scoring Weights  (higher = more influence)",
                 tip=_TIPS["Scoring Weights  (higher = more influence)"])
        self.weight_vars: dict[str, tk.IntVar] = {}
        labels = {
            "technical":      "Technical (RSI / MACD / MA)",
            "momentum":       "Momentum  (price returns)",
            "volume":         "Volume  (relative volume)",
            "fundamentals":   "Fundamentals  (P/E, EPS…)",
            "short_interest": "Short Interest",
        }
        for key, label in labels.items():
            val = int(self.cfg["weights"][key] * 100)
            var = tk.IntVar(value=val)
            self.weight_vars[key] = var
            f = tk.Frame(tab, bg=BG)
            f.pack(fill="x", padx=18, pady=4)
            _lbl(f, label, size=9, width=30).pack(side="left")
            sl = tk.Scale(f, variable=var, from_=0, to=60, orient="horizontal",
                          length=180, bg=BG, fg=TEXT, troughcolor=BG3,
                          highlightthickness=0, sliderlength=14,
                          font=("Segoe UI", 8))
            sl.pack(side="left")
            tk.Label(f, textvariable=var, width=3, font=("Segoe UI", 9),
                     bg=BG, fg=ACCENT).pack(side="left")
            tk.Label(f, text="%", font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(side="left")
            if label in _TIPS:
                _help_btn(f, label, _TIPS[label]).pack(side="left", padx=6)

        _lbl(tab, "Weights are auto-normalised to 100% on save.",
             size=8, color=MUTED).pack(pady=6)

    # ── Save ──────────────────────────────────────────────────────────────────
    def _save(self):
        self.cfg["universe_method"] = self.universe_var.get()
        self.cfg["max_stocks"]      = self.maxstocks_var.get()
        self.cfg["top_n"]           = self.topn_var.get()
        self.cfg["finviz_filters"]["Market Cap."]   = self.cap_var.get()
        self.cfg["finviz_filters"]["Average Volume"] = self.avgvol_var.get()

        self.cfg["pre_filters"]["rsi_min"]        = float(self.rsi_min_var.get())
        self.cfg["pre_filters"]["rsi_max"]        = float(self.rsi_max_var.get())
        self.cfg["pre_filters"]["min_price"]      = float(self.minprice_var.get())
        self.cfg["pre_filters"]["max_price"]      = float(self.maxprice_var.get())
        self.cfg["pre_filters"]["min_rel_volume"] = float(self.minrv_var.get())
        try:
            self.cfg["pre_filters"]["min_market_cap"] = float(self.minmc_var.get()) * 1e9
        except ValueError:
            pass

        only = [s for s, v in self.sector_vars.items() if v.get()]
        self.cfg["pre_filters"]["only_sectors"] = only

        raw = {k: v.get() for k, v in self.weight_vars.items()}
        total = sum(raw.values()) or 1
        for k, v in raw.items():
            self.cfg["weights"][k] = round(v / total, 4)

        self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main App Window
# ─────────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Stock Scanner")
        self.root.configure(bg=BG)
        self.root.geometry("740x580")
        self.root.minsize(600, 480)

        sys.path.insert(0, os.path.dirname(__file__))
        from config import CONFIG
        self.cfg = CONFIG

        self._scanning = False
        self._log_handler: logging.Handler | None = None
        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # Top bar
        top = tk.Frame(self.root, bg=BG2, pady=14)
        top.pack(fill="x", padx=14, pady=(14, 0))
        _lbl(top, "📊  Stock Scanner", size=20, bold=True, color=ACCENT, bg=BG2).pack(side="left", padx=16)
        tk.Button(top, text="⚙  Settings",
                  font=("Segoe UI", 10), bg=BG3, fg=MUTED, activebackground=BG2,
                  relief="flat", padx=10, pady=6, cursor="hand2",
                  command=self._open_settings).pack(side="right", padx=12)

        _lbl(self.root,
             "Technical · Momentum · Volume · Fundamentals · Short Interest",
             size=9, color=MUTED).pack(pady=(10, 0))

        # Button row
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=16)

        self.run_btn = tk.Button(btn_row, text="▶   RUN SCANNER",
                                 font=("Segoe UI", 15, "bold"),
                                 bg=ACCENT, fg="white", activebackground="#009944",
                                 relief="flat", padx=36, pady=13, cursor="hand2",
                                 command=self._start)
        self.run_btn.pack(side="left", padx=6)

        self.cancel_btn = tk.Button(btn_row, text="✕  Cancel",
                                    font=("Segoe UI", 11),
                                    bg=RED, fg="white", activebackground="#aa1133",
                                    relief="flat", padx=16, pady=13, cursor="hand2",
                                    state="disabled", command=self._cancel)
        self.cancel_btn.pack(side="left", padx=6)

        # Status + progress
        self.status_var = tk.StringVar(value="Ready – press RUN to start")
        _lbl(self.root, "", size=1, color=MUTED).pack()  # spacer
        tk.Label(self.root, textvariable=self.status_var,
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack()

        sty = ttk.Style()
        sty.theme_use("default")
        sty.configure("G.Horizontal.TProgressbar",
                      troughcolor=BG3, background=ACCENT, thickness=5)
        self.pb = ttk.Progressbar(self.root, mode="indeterminate", length=480,
                                  style="G.Horizontal.TProgressbar")
        self.pb.pack(pady=5)

        # Log
        lf = tk.Frame(self.root, bg=BG)
        lf.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        self.log_box = scrolledtext.ScrolledText(
            lf, font=("Consolas", 9), bg="#080d1c", fg=LOG_FG,
            insertbackground=LOG_FG, state="disabled", relief="flat", borderwidth=0)
        self.log_box.pack(fill="both", expand=True)

        # Open dashboard button (hidden until scan finishes)
        self.open_btn = tk.Button(self.root, text="🌐   Open Dashboard",
                                  font=("Segoe UI", 12, "bold"),
                                  bg=BLUE, fg="white", activebackground="#003dbb",
                                  relief="flat", padx=26, pady=10, cursor="hand2",
                                  command=self._open_dashboard)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _open_settings(self):
        SettingsWindow(self.root, self.cfg, lambda: self.status_var.set("Settings saved ✓"))

    def _start(self):
        if self._scanning:
            return
        self._scanning = True
        self.run_btn.config(state="disabled", text="⏳   Scanning…")
        self.cancel_btn.config(state="normal")
        self.open_btn.pack_forget()
        self.pb.start(8)
        self.status_var.set("Scanning… (batch download + parallel fetch, usually ~1 min)")

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

        threading.Thread(target=self._worker, daemon=True).start()

    def _cancel(self):
        import scanner as sc
        sc.cancel()
        self.status_var.set("Cancelling…")
        self.cancel_btn.config(state="disabled")

    def _worker(self):
        handler = None
        try:
            import scanner as sc
            from scanner import run_scanner, generate_dashboard

            # Sync runtime config
            sc.CONFIG.update(self.cfg)

            handler = WidgetLog(self.log_box)
            handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
            root_log = logging.getLogger()
            root_log.addHandler(handler)
            root_log.setLevel(logging.INFO)

            top = run_scanner()

            if handler:
                root_log.removeHandler(handler)

            if top:
                generate_dashboard(top)
                self.root.after(0, self._done_ok)
            elif sc._cancel.is_set():
                self.root.after(0, self._done_cancelled)
            else:
                self.root.after(0, lambda: self._done_err("No stocks passed filters – try relaxing the pre-filters"))

        except Exception as exc:
            if handler:
                logging.getLogger().removeHandler(handler)
            self.root.after(0, lambda: self._done_err(str(exc)))

    def _done_ok(self):
        self._finish()
        self.status_var.set("✅  Scan complete! Opening dashboard…")
        self.open_btn.pack(pady=(2, 10))
        self._open_dashboard()

    def _done_cancelled(self):
        self._finish()
        self.status_var.set("Scan cancelled.")

    def _done_err(self, msg):
        self._finish()
        self.status_var.set(f"❌  {msg}")

    def _finish(self):
        self._scanning = False
        self.pb.stop()
        self.run_btn.config(state="normal", text="▶   RUN SCANNER")
        self.cancel_btn.config(state="disabled")

    def _open_dashboard(self):
        path = os.path.abspath(self.cfg["output_file"])
        webbrowser.open(f"file:///{path}")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
