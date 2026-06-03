#!/usr/bin/env python3
"""
Stock Scanner – GUI Launcher
Just run:  python app.py
"""

import os
import sys
import threading
import logging
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext

# ── Colours ──────────────────────────────────────────────────────────────────
BG       = "#060912"
BG2      = "#0d1433"
BG3      = "#111d38"
ACCENT   = "#00cc66"
ACCENT2  = "#0055ff"
TEXT     = "#dde6ff"
MUTED    = "#5568a0"
LOG_FG   = "#7080aa"


# ── Log handler that writes into a Tkinter Text widget ───────────────────────
class WidgetLogHandler(logging.Handler):
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


# ── Settings window ──────────────────────────────────────────────────────────
class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config: dict, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.on_save = on_save
        self.config_ref = config

        pad = {"padx": 18, "pady": 6}

        tk.Label(self, text="Scanner Settings", font=("Segoe UI", 14, "bold"),
                 bg=BG, fg=TEXT).pack(pady=(18, 4))
        tk.Label(self, text="Changes take effect on the next scan",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(pady=(0, 12))

        def row(label, widget_factory):
            f = tk.Frame(self, bg=BG)
            f.pack(fill="x", **pad)
            tk.Label(f, text=label, font=("Segoe UI", 10),
                     bg=BG, fg=TEXT, width=22, anchor="w").pack(side="left")
            w = widget_factory(f)
            w.pack(side="left", fill="x", expand=True)
            return w

        # ── Market Cap ──
        self.cap_var = tk.StringVar(value=config["finviz_filters"].get("Market Cap.", "Large ($10bln to $200bln)"))
        cap_opts = [
            "Mega ($200bln and more)",
            "Large ($10bln to $200bln)",
            "Mid ($2bln to $10bln)",
            "Small ($300mln to $2bln)",
        ]
        row("Market Cap", lambda f: ttk.Combobox(f, textvariable=self.cap_var,
                                                  values=cap_opts, state="readonly",
                                                  font=("Segoe UI", 10), width=28))

        # ── Universe method ──
        self.universe_var = tk.StringVar(value=config["universe_method"])
        row("Universe", lambda f: ttk.Combobox(f, textvariable=self.universe_var,
                                                values=["finviz", "nasdaq100", "sp500"],
                                                state="readonly",
                                                font=("Segoe UI", 10), width=28))

        # ── Max stocks ──
        self.maxstocks_var = tk.IntVar(value=config["max_stocks"])
        def make_spin(f):
            return tk.Spinbox(f, from_=20, to=300, increment=10,
                              textvariable=self.maxstocks_var,
                              font=("Segoe UI", 10), width=8,
                              bg=BG3, fg=TEXT, insertbackground=TEXT,
                              buttonbackground=BG2, relief="flat")
        row("Max stocks to scan", make_spin)

        # ── Top N ──
        self.topn_var = tk.IntVar(value=config["top_n"])
        def make_topn(f):
            return tk.Spinbox(f, from_=2, to=10, increment=1,
                              textvariable=self.topn_var,
                              font=("Segoe UI", 10), width=8,
                              bg=BG3, fg=TEXT, insertbackground=TEXT,
                              buttonbackground=BG2, relief="flat")
        row("Top results to show", make_topn)

        # ── Weight sliders ──
        tk.Label(self, text="Scoring weights", font=("Segoe UI", 10, "bold"),
                 bg=BG, fg=MUTED).pack(pady=(14, 2))

        self.weight_vars = {}
        weight_labels = {
            "technical":      "Technical (RSI/MACD/MA)",
            "momentum":       "Momentum",
            "volume":         "Volume",
            "fundamentals":   "Fundamentals",
            "short_interest": "Short Interest",
        }
        for key, label in weight_labels.items():
            val = int(config["weights"][key] * 100)
            var = tk.IntVar(value=val)
            self.weight_vars[key] = var
            f = tk.Frame(self, bg=BG)
            f.pack(fill="x", padx=18, pady=3)
            tk.Label(f, text=label, font=("Segoe UI", 9),
                     bg=BG, fg=TEXT, width=26, anchor="w").pack(side="left")
            sl = tk.Scale(f, variable=var, from_=0, to=60,
                          orient="horizontal", length=160,
                          bg=BG, fg=TEXT, troughcolor=BG3,
                          highlightthickness=0, sliderlength=14,
                          font=("Segoe UI", 8))
            sl.pack(side="left")
            tk.Label(f, textvariable=var, width=3,
                     font=("Segoe UI", 9), bg=BG, fg=ACCENT).pack(side="left")
            tk.Label(f, text="%", font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(side="left")

        # ── Save button ──
        tk.Button(self, text="  Save Settings  ",
                  font=("Segoe UI", 11, "bold"),
                  bg=ACCENT, fg="white", activebackground="#009944",
                  relief="flat", padx=20, pady=8, cursor="hand2",
                  command=self._save).pack(pady=18)

    def _save(self):
        self.config_ref["universe_method"] = self.universe_var.get()
        self.config_ref["max_stocks"]       = self.maxstocks_var.get()
        self.config_ref["top_n"]            = self.topn_var.get()
        self.config_ref["finviz_filters"]["Market Cap."] = self.cap_var.get()

        raw = {k: v.get() for k, v in self.weight_vars.items()}
        total = sum(raw.values()) or 1
        for k, v in raw.items():
            self.config_ref["weights"][k] = round(v / total, 4)

        self.on_save()
        self.destroy()


# ── Main app window ───────────────────────────────────────────────────────────
class StockScannerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Stock Scanner")
        self.root.configure(bg=BG)
        self.root.geometry("720x560")
        self.root.minsize(580, 460)

        # Load config
        sys.path.insert(0, os.path.dirname(__file__))
        from config import CONFIG
        self.cfg = CONFIG

        self._scanning = False
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(self.root, bg=BG2, pady=16)
        top.pack(fill="x", padx=14, pady=(14, 0))

        tk.Label(top, text="📊  Stock Scanner",
                 font=("Segoe UI", 20, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left", padx=16)

        tk.Button(top, text="⚙  Settings",
                  font=("Segoe UI", 10),
                  bg=BG3, fg=MUTED,
                  activebackground=BG2,
                  relief="flat", padx=10, pady=6, cursor="hand2",
                  command=self._open_settings).pack(side="right", padx=12)

        # ── Subtitle ─────────────────────────────────────────────────────────
        tk.Label(self.root,
                 text="Multi-factor analysis  ·  Technical · Momentum · Volume · Fundamentals · Short Interest",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(pady=(10, 0))

        # ── Run button ────────────────────────────────────────────────────────
        self.run_btn = tk.Button(
            self.root,
            text="▶   RUN SCANNER",
            font=("Segoe UI", 16, "bold"),
            bg=ACCENT, fg="white",
            activebackground="#009944",
            relief="flat", padx=40, pady=14,
            cursor="hand2",
            command=self._start_scan,
        )
        self.run_btn.pack(pady=18)

        # ── Status ────────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready – press RUN to start")
        tk.Label(self.root, textvariable=self.status_var,
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack()

        # ── Progress bar ──────────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("default")
        style.configure("green.Horizontal.TProgressbar",
                        troughcolor=BG3, background=ACCENT, thickness=6)
        self.progress = ttk.Progressbar(
            self.root, mode="indeterminate", length=460,
            style="green.Horizontal.TProgressbar",
        )
        self.progress.pack(pady=6)

        # ── Log area ──────────────────────────────────────────────────────────
        log_frame = tk.Frame(self.root, bg=BG, padx=14)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(4, 6))

        self.log_box = scrolledtext.ScrolledText(
            log_frame,
            font=("Consolas", 9), bg="#080d1c", fg=LOG_FG,
            insertbackground=LOG_FG,
            state="disabled", relief="flat", borderwidth=0,
        )
        self.log_box.pack(fill="both", expand=True)

        # ── Open dashboard button (hidden until scan completes) ───────────────
        self.open_btn = tk.Button(
            self.root,
            text="🌐   Open Dashboard",
            font=("Segoe UI", 12, "bold"),
            bg=ACCENT2, fg="white",
            activebackground="#003dbb",
            relief="flat", padx=26, pady=10,
            cursor="hand2",
            command=self._open_dashboard,
        )

    # ── Actions ───────────────────────────────────────────────────────────────
    def _open_settings(self):
        SettingsWindow(self.root, self.cfg, self._on_settings_saved)

    def _on_settings_saved(self):
        self.status_var.set("Settings saved ✓  – press RUN to scan")

    def _start_scan(self):
        if self._scanning:
            return
        self._scanning = True
        self.run_btn.config(state="disabled", text="⏳   Scanning…")
        self.open_btn.pack_forget()
        self.progress.start(8)
        self.status_var.set("Scanning… this usually takes 3-5 minutes")

        # Clear log
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            from scanner import run_scanner, generate_dashboard

            # Wire up logging → widget
            handler = WidgetLogHandler(self.log_box)
            handler.setFormatter(
                logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
            )
            root_log = logging.getLogger()
            root_log.addHandler(handler)
            root_log.setLevel(logging.INFO)

            # Patch CONFIG in scanner module with current UI config
            import scanner as sc_mod
            sc_mod.CONFIG.update(self.cfg)

            top = run_scanner()
            root_log.removeHandler(handler)

            if top:
                generate_dashboard(top)
                self.root.after(0, self._done_ok)
            else:
                self.root.after(0, lambda: self._done_err("No stocks found – check your internet connection"))
        except Exception as exc:
            self.root.after(0, lambda: self._done_err(str(exc)))

    def _done_ok(self):
        self._scanning = False
        self.progress.stop()
        self.run_btn.config(state="normal", text="▶   RUN SCANNER")
        self.status_var.set("✅  Scan complete! Dashboard opened in browser.")
        self.open_btn.pack(pady=(2, 10))
        self._open_dashboard()

    def _done_err(self, msg):
        self._scanning = False
        self.progress.stop()
        self.run_btn.config(state="normal", text="▶   RUN SCANNER")
        self.status_var.set(f"❌  Error: {msg}")

    def _open_dashboard(self):
        path = os.path.abspath(self.cfg["output_file"])
        webbrowser.open(f"file:///{path}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = StockScannerApp(root)
    root.mainloop()
