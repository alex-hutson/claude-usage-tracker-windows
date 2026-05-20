#!/usr/bin/env python3
"""
Claude Usage Tracker -- Windows System Tray
===========================================
Shows real-time Claude AI session and weekly usage in the Windows
notification area (system tray), using the same API as the macOS tracker.

Session usage  = 5-hour rolling window
Weekly usage   = 7-day rolling total
Opus usage     = 7-day Opus-specific total

The icon colour shifts green -> amber -> orange -> red as you use more.
Hover over the icon for a full summary including reset time.

Requirements:  pip install pystray pillow requests
Run silently:  pythonw claude_tray.py
Run with log:  python  claude_tray.py
"""

import os, sys, json, time, threading, webbrowser
import requests
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as Item, Menu

# ---------------------------------------------------------------------------
CONFIG_PATH  = os.path.join(os.path.expanduser("~"), ".claude_usage_tracker.json")
REFRESH_SECS = 60
TOOLTIP_MAX  = 127   # Windows tray tooltip limit

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"session_key": "", "org_id": ""}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def _hdrs(key):
    return {**API_HEADERS, "Cookie": f"sessionKey={key}"}


def api_get_orgs(key):
    r = requests.get("https://claude.ai/api/organizations", headers=_hdrs(key), timeout=10)
    r.raise_for_status()
    return r.json()


def api_get_usage(key, org_id):
    r = requests.get(
        f"https://claude.ai/api/organizations/{org_id}/usage",
        headers=_hdrs(key), timeout=10
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Icon drawing
# ---------------------------------------------------------------------------

def _col(pct):
    if pct < 50: return (59, 165, 93)
    if pct < 75: return (245, 158, 11)
    if pct < 90: return (249, 115, 22)
    return (220, 38, 38)


def make_icon(session_pct=0, weekly_pct=0, error=False):
    W = H = 64
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # Dark card background
    d.rounded_rectangle([0, 0, W-1, H-1], radius=10, fill=(28, 28, 36, 245))

    if error:
        d.ellipse([22, 10, 42, 30], fill=(220, 38, 38))
        d.text((32, 20), "!", fill="white", anchor="mm")
        return img

    bx0, bx1  = 6, W - 6
    bw        = bx1 - bx0
    track     = (55, 55, 68)

    # Session bar (top)
    sy0, sy1 = 8, 26
    d.rounded_rectangle([bx0, sy0, bx1, sy1], radius=4, fill=track)
    fw = max(4, int(bw * min(session_pct, 100) / 100))
    d.rounded_rectangle([bx0, sy0, bx0+fw, sy1], radius=4, fill=_col(session_pct))
    d.text((bx0+3, sy0+1), "S", fill=(200, 200, 215), anchor="lt")

    # Weekly bar (bottom)
    wy0, wy1 = 36, 54
    d.rounded_rectangle([bx0, wy0, bx1, wy1], radius=4, fill=track)
    fw2 = max(4, int(bw * min(weekly_pct, 100) / 100))
    d.rounded_rectangle([bx0, wy0, bx0+fw2, wy1], radius=4, fill=_col(weekly_pct))
    d.text((bx0+3, wy0+1), "W", fill=(200, 200, 215), anchor="lt")

    return img


# ---------------------------------------------------------------------------
# Windows startup helper
# ---------------------------------------------------------------------------

def _startup_bat():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        "claude_usage_tracker.bat"
    )

def _in_startup():
    return os.path.exists(_startup_bat())

def _add_startup():
    script = os.path.abspath(__file__)
    with open(_startup_bat(), "w") as f:
        f.write(f'@echo off\nstart "" /B pythonw "{script}"\n')

def _rem_startup():
    p = _startup_bat()
    if os.path.exists(p):
        os.remove(p)


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class Tracker:

    def __init__(self):
        self.cfg   = load_config()
        self.data  = None
        self.err   = None
        self._lk   = threading.Lock()
        self.icon  = None

    # --- Data properties ---

    @property
    def session_pct(self):
        return float((self.data or {}).get("five_hour", {}).get("utilization", 0))

    @property
    def weekly_pct(self):
        return float((self.data or {}).get("seven_day", {}).get("utilization", 0))

    @property
    def opus_pct(self):
        d = (self.data or {}).get("seven_day_opus") or {}
        return float(d.get("utilization", 0))

    @property
    def sonnet_pct(self):
        d = (self.data or {}).get("seven_day_sonnet") or {}
        return float(d.get("utilization", 0))

    @property
    def reset_time(self):
        raw = (self.data or {}).get("five_hour", {}).get("resets_at", "")
        if not raw:
            return "--:--"
        try:
            from datetime import datetime
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except Exception:
            return raw[:16]

    # --- Refresh ---

    def _refresh(self):
        if not self.cfg.get("session_key") or not self.cfg.get("org_id"):
            self.err = "Not configured -- right-click -> Set Session Key"
            return
        try:
            data = api_get_usage(self.cfg["session_key"], self.cfg["org_id"])
            with self._lk:
                self.data = data
                self.err  = None
        except requests.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            self.err = ("Session expired -- update key" if code == 401 else f"HTTP {code}")
        except Exception as e:
            self.err = str(e)[:80]

    def _loop(self):
        while True:
            self._refresh()
            self._update()
            time.sleep(REFRESH_SECS)

    # --- Icon update ---

    def _update(self):
        if not self.icon:
            return
        self.icon.icon  = make_icon(self.session_pct, self.weekly_pct, error=bool(self.err))
        tooltip = (
            f"Claude Usage  [!]  {self.err}" if self.err else
            f"Claude  |  Session: {self.session_pct:.0f}%  |  Weekly: {self.weekly_pct:.0f}%  |  Resets: {self.reset_time}"
        )
        self.icon.title = tooltip[:TOOLTIP_MAX]

    # --- Menu label callbacks ---

    def _lbl_session(self, item):
        if self.err:
            return f"[!]  {self.err}"
        return f"Session (5h):   {self.session_pct:.1f}%   resets {self.reset_time}"

    def _lbl_weekly(self, item):
        return f"Weekly  (7d):   {self.weekly_pct:.1f}%" if self.data else "Weekly  (7d):   --"

    def _lbl_opus(self, item):
        if not self.data:
            return "Sonnet (7d):    --"
        return f"Sonnet  (7d):   {self.sonnet_pct:.1f}%"

    def _lbl_startup(self, item):
        return "[x] Start with Windows" if _in_startup() else "[ ] Start with Windows"

    # --- Menu actions ---

    def _on_refresh(self, icon, item):
        threading.Thread(target=lambda: (self._refresh(), self._update()), daemon=True).start()

    def _on_set_key(self, icon, item):
        import tkinter as tk
        from tkinter import simpledialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        key = simpledialog.askstring(
            "Claude Usage Tracker",
            "Paste your sessionKey cookie from claude.ai\n\n"
            "How to get it:\n"
            "  1. Open https://claude.ai in Chrome or Edge\n"
            "  2. Press F12  ->  Application tab  ->  Cookies\n"
            "  3. Click https://claude.ai in the left panel\n"
            "  4. Find the row called  sessionKey\n"
            "  5. Double-click its Value cell and copy the full text\n"
            "     (starts with  sk-ant-sid01-...)\n",
            initialvalue=self.cfg.get("session_key", ""),
            parent=root,
        )

        if not key or not key.strip():
            root.destroy()
            return

        key = key.strip()
        try:
            orgs = api_get_orgs(key)
        except Exception as e:
            messagebox.showerror("Claude Usage Tracker",
                                 f"Could not connect:\n\n{e}")
            root.destroy()
            return

        if not orgs:
            messagebox.showerror("Claude Usage Tracker",
                                 "No organisations found. Check your session key.")
            root.destroy()
            return

        self.cfg["session_key"] = key
        self.cfg["org_id"]      = orgs[0]["uuid"]
        save_config(self.cfg)

        self._refresh()
        self._update()

        org_name = orgs[0].get("name", orgs[0]["uuid"][:12] + "...")
        messagebox.showinfo("Claude Usage Tracker",
                            f"Connected to: {org_name}\n\n"
                            f"Session: {self.session_pct:.1f}%\n"
                            f"Weekly:  {self.weekly_pct:.1f}%\n"
                            f"Resets:  {self.reset_time}")
        root.destroy()

    def _on_startup(self, icon, item):
        _rem_startup() if _in_startup() else _add_startup()

    def _on_open(self, icon, item):
        webbrowser.open("https://claude.ai")

    def _on_quit(self, icon, item):
        icon.stop()

    # --- Build menu ---

    def _menu(self):
        return Menu(
            Item("Claude Usage Tracker", None, enabled=False),
            Menu.SEPARATOR,
            Item(self._lbl_session, None, enabled=False),
            Item(self._lbl_weekly,  None, enabled=False),
            Item(self._lbl_opus,    None, enabled=False),
            Menu.SEPARATOR,
            Item("Refresh Now",        self._on_refresh),
            Item("Set Session Key...", self._on_set_key),
            Item("Open claude.ai",     self._on_open),
            Menu.SEPARATOR,
            Item(self._lbl_startup,    self._on_startup),
            Menu.SEPARATOR,
            Item("Quit",               self._on_quit),
        )

    # --- Entry point ---

    def run(self):
        threading.Thread(target=self._loop, daemon=True).start()

        self.icon = pystray.Icon(
            name  = "claude_usage",
            icon  = make_icon(),
            title = "Claude Usage Tracker -- loading...",
            menu  = self._menu(),
        )

        if not self.cfg.get("session_key"):
            # Prompt on first run
            self.icon.run(setup=lambda ic: threading.Thread(
                target=self._on_set_key, args=(ic, None), daemon=True
            ).start())
        else:
            self.icon.run()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Tracker().run()
