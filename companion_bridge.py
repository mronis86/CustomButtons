#!/usr/bin/env python3
"""
Companion Bridge — Local OSC relay
Polls a Railway server for pending trigger commands,
fires OSC /press/bank/{page}/{bank} to Bitfocus Companion.

Requirements:  pip install python-osc requests
"""

import json
import os
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import scrolledtext, ttk
import urllib.request
import urllib.error

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "bridge_config.json")

# ─── OSC implementation (no dependency on python-osc if not installed) ─────────
# We include a minimal OSC builder so the app works with zero pip installs.
# If python-osc IS installed it will be used instead for full compatibility.

def _osc_string(s):
    s = s.encode("utf-8") + b"\x00"
    pad = (4 - len(s) % 4) % 4
    return s + b"\x00" * pad

def _osc_build(address, *args):
    """Build a minimal OSC message with int or float args."""
    msg = _osc_string(address)
    type_tag = "," + "".join("i" if isinstance(a, int) else "f" for a in args)
    msg += _osc_string(type_tag)
    for a in args:
        if isinstance(a, int):
            msg += struct.pack(">i", a)
        else:
            msg += struct.pack(">f", a)
    return msg

def send_osc(host, port, address, *args):
    data = _osc_build(address, *args)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (host, int(port)))
    finally:
        sock.close()

# ─── Config ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "railway_url":    "",
    "api_secret":     "",
    "companion_host": "127.0.0.1",
    "companion_port": "12321",
    "poll_interval":  "1.0",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            # fill missing keys with defaults
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── Poller thread ────────────────────────────────────────────────────────────

class Poller(threading.Thread):
    """
    Long-polls GET /poll from the Railway server.
    The server returns the next queued trigger as JSON { page, bank }
    or { waiting: true } if nothing is pending.
    """
    def __init__(self, cfg, log_queue, stop_event):
        super().__init__(daemon=True)
        self.cfg         = cfg
        self.log_queue   = log_queue
        self.stop_event  = stop_event

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] [{level}] {msg}")

    def run(self):
        self.log("Poller started")
        while not self.stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:
                self.log(f"Poll error: {e}", "ERR")
                time.sleep(3)
            interval = float(self.cfg.get("poll_interval", 1.0))
            time.sleep(interval)
        self.log("Poller stopped")

    def _poll_once(self):
        url    = self.cfg["railway_url"].rstrip("/") + "/poll"
        secret = self.cfg.get("api_secret", "")

        req = urllib.request.Request(url)
        if secret:
            req.add_header("x-api-secret", secret)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return  # nothing waiting
            raise
        except urllib.error.URLError as e:
            self.log(f"Cannot reach Railway: {e.reason}", "ERR")
            time.sleep(5)
            return

        if data.get("waiting"):
            return

        page = data.get("page")
        bank = data.get("bank")
        if page and bank:
            self._fire_osc(page, bank)

    def _fire_osc(self, page, bank):
        host    = self.cfg["companion_host"]
        port    = self.cfg["companion_port"]
        address = f"/press/bank/{page}/{bank}"
        try:
            send_osc(host, port, address)
            self.log(f"OSC  {address}  →  {host}:{port}", "OSC")
        except Exception as e:
            self.log(f"OSC failed: {e}", "ERR")

# ─── GUI ──────────────────────────────────────────────────────────────────────

BG        = "#0d0f12"
SURFACE   = "#141720"
SURFACE2  = "#1c2030"
BORDER    = "#2a2e3a"
TEXT      = "#c8cdd8"
MUTED     = "#4e5668"
ACCENT    = "#00d4ff"
SUCCESS   = "#00e676"
DANGER    = "#ff3b3b"
WARN      = "#ffb700"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Companion Bridge")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(560, 480)
        self.geometry("640x560")

        self.cfg         = load_config()
        self.log_queue   = queue.Queue()
        self.stop_event  = threading.Event()
        self.poller      = None
        self.running     = False

        self._build_ui()
        self._poll_log()

        # Auto-start if config looks complete
        if self.cfg.get("railway_url") and self.cfg.get("companion_host"):
            self.after(600, self.start_polling)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        mono  = tkfont.Font(family="Courier", size=10)
        label_font = tkfont.Font(family="Courier", size=9)

        # ── Header bar ──
        hdr = tk.Frame(self, bg=SURFACE, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="COMPANION BRIDGE", bg=SURFACE, fg=ACCENT,
                 font=("Courier", 12, "bold"), padx=16).pack(side="left", pady=10)

        self.status_canvas = tk.Canvas(hdr, width=12, height=12, bg=SURFACE,
                                       highlightthickness=0)
        self.status_canvas.pack(side="right", padx=(0, 8), pady=16)
        self.status_dot = self.status_canvas.create_oval(1,1,11,11, fill=MUTED, outline="")

        self.status_label = tk.Label(hdr, text="STOPPED", bg=SURFACE,
                                     fg=MUTED, font=("Courier", 9))
        self.status_label.pack(side="right", padx=(0, 4))

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x")

        # ── Config section ──
        cfg_outer = tk.Frame(self, bg=BG)
        cfg_outer.pack(fill="x", padx=16, pady=(14, 0))

        tk.Label(cfg_outer, text="// CONFIGURATION", bg=BG, fg=MUTED,
                 font=("Courier", 9)).pack(anchor="w", pady=(0, 8))

        cfg_frame = tk.Frame(cfg_outer, bg=SURFACE, bd=0,
                             highlightthickness=1, highlightbackground=BORDER)
        cfg_frame.pack(fill="x")

        self.fields = {}
        rows = [
            ("Railway URL",      "railway_url",    "https://your-app.up.railway.app", False),
            ("API Secret",       "api_secret",      "optional",                        True),
            ("Companion Host",   "companion_host",  "192.168.1.x",                     False),
            ("Companion OSC Port","companion_port", "12321",                            False),
            ("Poll Interval (s)","poll_interval",   "1.0",                             False),
        ]

        for i, (lbl, key, placeholder, is_secret) in enumerate(rows):
            row = tk.Frame(cfg_frame, bg=SURFACE)
            row.pack(fill="x", padx=1, pady=1)

            tk.Label(row, text=lbl, bg=SURFACE, fg=MUTED,
                     font=("Courier", 9), width=20, anchor="w").pack(side="left", padx=(10,0), pady=6)

            entry = tk.Entry(row, bg=SURFACE2, fg=TEXT, insertbackground=ACCENT,
                             relief="flat", font=("Courier", 10),
                             highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=ACCENT,
                             show="•" if is_secret else "")
            entry.insert(0, self.cfg.get(key, ""))
            entry.pack(side="left", fill="x", expand=True, padx=(8, 10), pady=6, ipady=4)
            entry.bind("<FocusOut>", lambda e, k=key, ent=entry: self._field_changed(k, ent))
            entry.bind("<Return>",   lambda e, k=key, ent=entry: self._field_changed(k, ent))

            self.fields[key] = entry

            if i < len(rows) - 1:
                tk.Frame(cfg_frame, bg=BORDER, height=1).pack(fill="x", padx=1)

        # ── Buttons row ──
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=12)

        self.start_btn = self._mk_btn(btn_row, "▶  START", self.start_polling, ACCENT, "#000")
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = self._mk_btn(btn_row, "■  STOP", self.stop_polling, SURFACE2, TEXT)
        self.stop_btn.pack(side="left", padx=(0, 8))
        self.stop_btn.config(state="disabled")

        self._mk_btn(btn_row, "TEST OSC", self.test_osc, SURFACE2, WARN).pack(side="left")

        self._mk_btn(btn_row, "CLEAR LOG", self.clear_log, SURFACE2, MUTED).pack(side="right")

        # ── Log section ──
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        log_hdr = tk.Frame(self, bg=SURFACE)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="// ACTIVITY LOG", bg=SURFACE, fg=MUTED,
                 font=("Courier", 9), padx=16).pack(side="left", pady=6)

        self.log_box = scrolledtext.ScrolledText(
            self, bg=BG, fg=TEXT, font=("Courier", 9),
            relief="flat", bd=0, padx=10, pady=8,
            insertbackground=ACCENT, state="disabled",
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True)

        # colour tags
        self.log_box.tag_config("OSC",  foreground=ACCENT)
        self.log_box.tag_config("ERR",  foreground=DANGER)
        self.log_box.tag_config("OK",   foreground=SUCCESS)
        self.log_box.tag_config("WARN", foreground=WARN)
        self.log_box.tag_config("INFO", foreground=MUTED)

    def _mk_btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=SURFACE2, activeforeground=ACCENT,
                         font=("Courier", 10, "bold"), relief="flat",
                         padx=14, pady=6, cursor="hand2",
                         highlightthickness=1, highlightbackground=BORDER)

    # ── Field changes ─────────────────────────────────────────────────────────

    def _field_changed(self, key, entry):
        self.cfg[key] = entry.get().strip()
        save_config(self.cfg)

    # ── Controls ─────────────────────────────────────────────────────────────

    def start_polling(self):
        # Save all fields first
        for key, entry in self.fields.items():
            self.cfg[key] = entry.get().strip()
        save_config(self.cfg)

        if not self.cfg.get("railway_url"):
            self._log("[ERR] Railway URL is required", "ERR")
            return

        self.stop_event.clear()
        self.poller = Poller(self.cfg, self.log_queue, self.stop_event)
        self.poller.start()
        self.running = True

        self._set_status("RUNNING", SUCCESS)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._log("Bridge started", "OK")

    def stop_polling(self):
        self.stop_event.set()
        self.running = False
        self._set_status("STOPPED", MUTED)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._log("Bridge stopped", "WARN")

    def test_osc(self):
        """Send a test OSC message to verify Companion connectivity."""
        for key, entry in self.fields.items():
            self.cfg[key] = entry.get().strip()
        host = self.cfg.get("companion_host", "127.0.0.1")
        port = self.cfg.get("companion_port", "12321")
        # Test with page 1, bank 1
        try:
            send_osc(host, int(port), "/press/bank/1/1")
            self._log(f"TEST OSC  /press/bank/1/1  →  {host}:{port}", "OK")
        except Exception as e:
            self._log(f"TEST OSC failed: {e}", "ERR")

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _set_status(self, text, color):
        self.status_canvas.itemconfig(self.status_dot, fill=color)
        self.status_label.config(text=text, fg=color)

    # ── Log polling ───────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll_log)

    def _log(self, msg, level=None):
        # Auto-detect level from message if not provided
        if level is None:
            for tag in ("OSC", "ERR", "OK", "WARN"):
                if f"[{tag}]" in msg or msg.startswith(tag):
                    level = tag
                    break
            else:
                level = "INFO"

        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n", level)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def on_close(self):
        self.stop_event.set()
        self.destroy()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
