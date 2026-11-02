"""
app.py - touch UI (CustomTkinter). All pump logic lives in pump_backend.py,
reverse.py and presets.py.

Run on a laptop:  PUMP_MOCK=1 python3 app.py
Run on the Pi:    python3 app.py --fullscreen
"""
import math
import queue
import socket
import subprocess
import sys
import threading
import time

import customtkinter as ctk

import presets
import pump_backend as pump
import reverse

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

W, H = 1024, 600
F_BIG = ("DejaVu Sans", 44, "bold")
F_MED = ("DejaVu Sans", 26, "bold")
F_SMALL = ("DejaVu Sans", 20)
F_KEY = ("DejaVu Sans", 22, "bold")
GREEN, RED, GREY, BLUE = "#1f9d55", "#c0392b", "#3a3a3a", "#2b4a6f"
PER_PAGE_AUTO = 6
PER_PAGE_EDIT = 4


def get_ips():
    """IPv4 addresses of this machine for the home screen. Never raises."""
    ips = []
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        ips = [a for a in out.split() if "." in a and ":" not in a]
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))   # sends nothing; just picks the outgoing interface
            ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass
    return [a for a in ips if not a.startswith("127.") and a != "0.0.0.0"]


class App(ctk.CTk):
    def __init__(self, fullscreen=False):
        super().__init__()
        self.title("ADPS")
        self.geometry(f"{W}x{H}")
        if fullscreen:
            self.attributes("-fullscreen", True)
        self.q = queue.Queue()          # worker threads -> UI thread
        self.busy = False               # a dose or reverse is running
        self.stopping = False
        self.ok = False                 # Klipper reports ready
        self.incomplete = False         # last job did not finish
        self.entry = ""                 # Manual mode text
        self.medication = None          # Manual mode medication
        self.job = None
        self.job_t0 = 0.0
        self.presets, warnings = presets.load()
        self.auto_page = 0
        self.edit_page = 0
        self.overlay = None
        self.shift = False
        self.can_dose = False
        self._check_pending = False
        self._ip_pending = False
        self._ticks = 0
        self.lbl_ip = None
        self.preset_buttons = []
        self.edit_buttons = []
        self.reverse_buttons = []
        self.prime_buttons = []
        self.pending = None             # (medication, mL) waiting for the syringe after priming

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="PROTOTYPE - WATER ONLY - NOT FOR CLINICAL USE",
                     font=("DejaVu Sans", 16, "bold"), text_color="#e6a23c"
                     ).grid(row=0, column=0, sticky="ew")
        self.btn_menu = ctk.CTkButton(header, text="MENU", width=130, height=44, font=F_SMALL,
                                      fg_color=BLUE, command=self.open_menu)
        self.btn_menu.grid(row=0, column=1, sticky="e")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.screens = {}
        for name, build in (("home", self.build_home), ("manual", self.build_manual),
                            ("auto", self.build_auto), ("edit", self.build_edit),
                            ("maint", self.build_maint)):
            frame = ctk.CTkFrame(self.body, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            build(frame)
            self.screens[name] = frame

        bar = ctk.CTkFrame(self)
        bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(bar, text="Starting...", font=F_SMALL, anchor="w",
                                   justify="left", wraplength=560)
        self.status.grid(row=0, column=0, sticky="ew", padx=12)
        self.btn_recover = ctk.CTkButton(bar, text="RECOVER", width=150, height=70, font=F_SMALL,
                                         fg_color=GREY, command=self.on_recover)
        self.btn_recover.grid(row=0, column=1, padx=6, pady=6)
        self.btn_stop = ctk.CTkButton(bar, text="STOP", width=200, height=70, font=F_MED,
                                      fg_color=RED, hover_color="#e74c3c", command=self.on_stop)
        self.btn_stop.grid(row=0, column=2, padx=6, pady=6)

        msgs = pump.CONFIG_WARNINGS + warnings
        if msgs:
            self.set_status("; ".join(msgs), error=True)
        self.render_auto()
        self.show("home")
        self.refresh()
        self.after(100, self.poll)

    # ---------- screens ----------
    def build_home(self, f):
        f.grid_columnconfigure((0, 1), weight=1)
        f.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(f, text="ADPS Dose Preparation", font=F_BIG).grid(
            row=0, column=0, columnspan=2, pady=(10, 0))
        ctk.CTkButton(f, text="AUTO\n(presets)", font=F_BIG, command=lambda: self.show("auto")).grid(
            row=1, column=0, sticky="nsew", padx=20, pady=20)
        ctk.CTkButton(f, text="MANUAL\n(medication + mL)", font=F_BIG,
                      command=lambda: self.show("manual")).grid(
            row=1, column=1, sticky="nsew", padx=20, pady=20)
        if pump.CFG.get("show_ip", True):     # set "show_ip": false in config.json to hide
            self.lbl_ip = ctk.CTkLabel(f, text="Network: checking ...", font=F_SMALL, text_color="#9aa0a6")
            self.lbl_ip.grid(row=2, column=0, columnspan=2, pady=(0, 6))

    def build_manual(self, f):
        f.grid_columnconfigure(0, weight=3)
        f.grid_columnconfigure(1, weight=2)
        f.grid_rowconfigure(0, weight=1)
        pad = ctk.CTkFrame(f, fg_color="transparent")
        pad.grid(row=0, column=0, sticky="nsew")
        keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "<"]
        for i, k in enumerate(keys):
            pad.grid_rowconfigure(i // 3, weight=1)
            pad.grid_columnconfigure(i % 3, weight=1)
            ctk.CTkButton(pad, text="DEL" if k == "<" else k, font=F_BIG, fg_color=BLUE,
                          command=lambda k=k: self.key(k)).grid(
                row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=4)
        side = ctk.CTkFrame(f, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure((0, 1), weight=1)
        side.grid_rowconfigure(3, weight=1)
        ctk.CTkButton(side, text="< Home", font=F_SMALL, height=48, fg_color=GREY,
                      command=lambda: self.show("home")).grid(
            row=0, column=0, sticky="ew", padx=(0, 3), pady=3)
        b = ctk.CTkButton(side, text="PRIME", font=F_SMALL, height=48, fg_color="#8e5b1a",
                          hover_color="#a8701f", state="disabled", command=self.ask_confirm_prime)
        b.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=3)
        self.prime_buttons.append(b)
        self.btn_med = ctk.CTkButton(side, text="Tap to choose\nmedication", font=F_SMALL, height=80,
                                     fg_color=BLUE, command=self.pick_medication)
        self.btn_med.grid(row=1, column=0, columnspan=2, sticky="ew", pady=3)
        self.lbl_value = ctk.CTkLabel(side, text="0 mL", font=F_BIG)
        self.lbl_value.grid(row=2, column=0, columnspan=2, sticky="ew", pady=2)
        self.btn_dispense = ctk.CTkButton(side, text="DISPENSE", font=F_MED, fg_color=GREEN,
                                          hover_color="#27ae60", command=self.on_manual_dispense)
        self.btn_dispense.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=3)
        ctk.CTkLabel(side, text=f"Max {pump.MAX_ML:g} mL", font=("DejaVu Sans", 16)).grid(
            row=4, column=0, columnspan=2)
        ctk.CTkButton(side, text="Clear", font=F_SMALL, height=52, fg_color=GREY,
                      command=lambda: self.key("C")).grid(row=5, column=0, sticky="ew", padx=(0, 3), pady=3)
        self.btn_save = ctk.CTkButton(side, text="Save preset", font=F_SMALL, height=52, fg_color=BLUE,
                                      command=self.on_save_preset)
        self.btn_save.grid(row=5, column=1, sticky="ew", padx=(3, 0), pady=3)

    def build_auto(self, f):
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(top, text="< Home", font=F_SMALL, height=52, width=150, fg_color=GREY,
                      command=lambda: self.show("home")).pack(side="left", pady=3)
        ctk.CTkLabel(top, text="Auto - tap a preset", font=F_MED).pack(side="left", padx=20)
        self.auto_grid = ctk.CTkFrame(f, fg_color="transparent")
        self.auto_grid.grid(row=1, column=0, sticky="nsew")
        self.auto_grid.grid_columnconfigure((0, 1), weight=1)
        for r in range(3):
            self.auto_grid.grid_rowconfigure(r, weight=1)

    def render_auto(self):
        """(Re)build the preset buttons. Call after presets change."""
        for w in self.auto_grid.winfo_children():
            w.destroy()
        self.preset_buttons = []
        pages = max(1, math.ceil(len(self.presets) / PER_PAGE_AUTO))
        self.auto_page = min(self.auto_page, pages - 1)
        start = self.auto_page * PER_PAGE_AUTO
        chunk = self.presets[start:start + PER_PAGE_AUTO]
        state = "normal" if self.can_dose else "disabled"
        if not chunk:
            ctk.CTkLabel(self.auto_grid, text="No presets yet.\nUse MANUAL > Save preset.",
                         font=F_MED).grid(row=0, column=0, columnspan=2, rowspan=3)
        for i, p in enumerate(chunk):
            b = ctk.CTkButton(self.auto_grid, text=f"{p['name']}\n{p['ml']:g} mL", font=F_MED,
                              fg_color=GREEN, hover_color="#27ae60", state=state,
                              command=lambda p=p: self.ask_confirm_dose(p["name"], p["ml"], auto=True))
            b.grid(row=i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            self.preset_buttons.append(b)
        if pages > 1:
            nav = ctk.CTkFrame(self.auto_grid, fg_color="transparent")
            nav.grid(row=3, column=0, columnspan=2, sticky="ew")
            ctk.CTkButton(nav, text="< Prev", font=F_SMALL, width=140, height=48, fg_color=GREY,
                          state="normal" if self.auto_page > 0 else "disabled",
                          command=lambda: self.page_auto(-1)).pack(side="left", padx=6)
            ctk.CTkLabel(nav, text=f"Page {self.auto_page + 1}/{pages}", font=F_SMALL).pack(side="left", expand=True)
            ctk.CTkButton(nav, text="Next >", font=F_SMALL, width=140, height=48, fg_color=GREY,
                          state="normal" if self.auto_page < pages - 1 else "disabled",
                          command=lambda: self.page_auto(1)).pack(side="right", padx=6)

    def page_auto(self, d):
        self.auto_page += d
        self.render_auto()

    def build_edit(self, f):
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(top, text="< Home", font=F_SMALL, height=52, width=150, fg_color=GREY,
                      command=lambda: self.show("home")).pack(side="left", pady=3)
        ctk.CTkLabel(top, text="Edit presets", font=F_MED).pack(side="left", padx=20)
        ctk.CTkLabel(top, text="Add new ones from MANUAL > Save preset", font=("DejaVu Sans", 16),
                     text_color="#9aa0a6").pack(side="left")
        self.edit_list = ctk.CTkFrame(f, fg_color="transparent")
        self.edit_list.grid(row=1, column=0, sticky="nsew")
        self.edit_list.grid_columnconfigure(0, weight=1)

    def render_edit(self):
        for w in self.edit_list.winfo_children():
            w.destroy()
        self.edit_buttons = []
        pages = max(1, math.ceil(len(self.presets) / PER_PAGE_EDIT))
        self.edit_page = min(self.edit_page, pages - 1)
        start = self.edit_page * PER_PAGE_EDIT
        if not self.presets:
            ctk.CTkLabel(self.edit_list, text="No presets.", font=F_MED).grid(row=0, column=0, pady=40)
        for r, p in enumerate(self.presets[start:start + PER_PAGE_EDIT]):
            idx = start + r
            row = ctk.CTkFrame(self.edit_list)
            row.grid(row=r, column=0, sticky="ew", pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"{p['name']}   -   {p['ml']:g} mL", font=F_MED, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=12, pady=8)
            b = ctk.CTkButton(row, text="Delete", font=F_SMALL, width=140, height=52, fg_color=RED,
                              state="disabled" if self.busy else "normal",
                              command=lambda i=idx, p=p: self.ask_delete(i, p))
            b.grid(row=0, column=1, padx=8, pady=6)
            self.edit_buttons.append(b)
        if pages > 1:
            nav = ctk.CTkFrame(self.edit_list, fg_color="transparent")
            nav.grid(row=PER_PAGE_EDIT, column=0, sticky="ew", pady=4)
            ctk.CTkButton(nav, text="< Prev", font=F_SMALL, width=140, height=48, fg_color=GREY,
                          state="normal" if self.edit_page > 0 else "disabled",
                          command=lambda: self.page_edit(-1)).pack(side="left", padx=6)
            ctk.CTkLabel(nav, text=f"Page {self.edit_page + 1}/{pages}", font=F_SMALL).pack(side="left", expand=True)
            ctk.CTkButton(nav, text="Next >", font=F_SMALL, width=140, height=48, fg_color=GREY,
                          state="normal" if self.edit_page < pages - 1 else "disabled",
                          command=lambda: self.page_edit(1)).pack(side="right", padx=6)

    def page_edit(self, d):
        self.edit_page += d
        self.render_edit()

    def build_maint(self, f):
        f.grid_columnconfigure(tuple(range(max(1, len(reverse.OPTIONS)))), weight=1)
        f.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=max(1, len(reverse.OPTIONS)), sticky="ew")
        ctk.CTkButton(top, text="< Home", font=F_SMALL, height=52, width=150, fg_color=GREY,
                      command=lambda: self.show("home")).pack(side="left", pady=3)
        ctk.CTkLabel(top, text="Maintenance - reverse pump", font=F_MED).pack(side="left", padx=20)
        ctk.CTkLabel(f, text="Runs the pump BACKWARDS to pull liquid back / clear the line.\n"
                             "This is NOT a dose. Use water only.", font=F_SMALL, justify="left"
                     ).grid(row=1, column=0, columnspan=max(1, len(reverse.OPTIONS)), pady=10, sticky="w")
        self.reverse_buttons = []
        for i, ml in enumerate(reverse.OPTIONS):
            b = ctk.CTkButton(f, text=f"REVERSE\n{ml:g} mL", font=F_MED, fg_color="#8e5b1a",
                              hover_color="#a8701f", state="disabled",
                              command=lambda ml=ml: self.ask_confirm_reverse(ml))
            b.grid(row=2, column=i, sticky="nsew", padx=8, pady=8)
            self.reverse_buttons.append(b)
        f.grid_rowconfigure(3, weight=1)
        b = ctk.CTkButton(f, text=f"PRIME LINE (forward {reverse.PRIME_ML:g} mL)", font=F_MED,
                          fg_color="#1f6f8b", hover_color="#2587a8", state="disabled",
                          command=self.ask_confirm_prime)
        b.grid(row=3, column=0, columnspan=max(1, len(reverse.OPTIONS)), sticky="nsew", padx=8, pady=8)
        self.prime_buttons.append(b)

    def show(self, name):
        self.close_overlay()
        if name == "edit":
            self.render_edit()
        elif name == "auto":
            self.render_auto()
        self.screens[name].tkraise()

    # ---------- overlays (drawn inside the body, so STOP stays visible) ----------
    def open_overlay(self):
        self.close_overlay()
        self.overlay = ctk.CTkFrame(self.body, corner_radius=12, fg_color="#1e1e1e",
                                    border_width=2, border_color="#666")
        self.overlay.place(relx=0.01, rely=0.01, relwidth=0.98, relheight=0.98)
        self.overlay.tkraise()
        return self.overlay

    def close_overlay(self):
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None

    def open_menu(self):
        if self.busy:
            return
        o = self.open_overlay()
        o.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(o, text="Menu", font=F_MED).grid(row=0, column=0, columnspan=2, pady=(10, 0))
        items = [("Auto (presets)", "auto"), ("Manual", "manual"), ("Edit presets", "edit"),
                 ("Maintenance\n(reverse pump)", "maint"), ("Home", "home")]
        for i, (text, scr) in enumerate(items):
            o.grid_rowconfigure(1 + i // 2, weight=1)
            ctk.CTkButton(o, text=text, font=F_MED, fg_color=BLUE,
                          command=lambda s=scr: self.show(s)).grid(
                row=1 + i // 2, column=i % 2, sticky="nsew", padx=10, pady=8)
        ctk.CTkButton(o, text="Close", font=F_MED, fg_color=GREY, command=self.close_overlay).grid(
            row=1 + len(items) // 2, column=len(items) % 2, sticky="nsew", padx=10, pady=8)
        o.grid_rowconfigure(1 + len(items) // 2, weight=1)

    def confirm(self, title, lines, ok_text, on_ok, ok_color=GREEN, note=None, on_cancel=None):
        o = self.open_overlay()
        o.grid_columnconfigure((0, 1), weight=1)
        o.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(o, text=title, font=F_MED, text_color="#e6a23c").grid(
            row=0, column=0, columnspan=2, pady=(14, 0))
        body = ctk.CTkFrame(o, fg_color="transparent")
        body.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure((0, 2), weight=1)
        ctk.CTkLabel(body, text="\n".join(lines), font=("DejaVu Sans", 34, "bold"), justify="center",
                     wraplength=880).grid(row=1, column=0)
        if note:
            ctk.CTkLabel(body, text=note, font=F_SMALL, text_color="#e6a23c", justify="center",
                         wraplength=880).grid(row=2, column=0, sticky="n")

        def go():
            self.close_overlay()
            on_ok()

        def cancel():
            self.close_overlay()
            if on_cancel:
                on_cancel()
        ctk.CTkButton(o, text="CANCEL", font=F_MED, height=110, fg_color=GREY,
                      command=cancel).grid(row=2, column=0, sticky="ew", padx=14, pady=14)
        ctk.CTkButton(o, text=ok_text, font=F_MED, height=110, fg_color=ok_color,
                      command=go).grid(row=2, column=1, sticky="ew", padx=14, pady=14)

    def pick_medication(self):
        if self.busy:
            return
        o = self.open_overlay()
        o.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(o, text="Choose medication", font=F_MED).grid(row=0, column=0, columnspan=2, pady=(8, 0))
        opts = presets.MEDICATIONS + ["Other ..."]
        for i, m in enumerate(opts):
            o.grid_rowconfigure(1 + i // 2, weight=1)
            other = m == "Other ..."
            ctk.CTkButton(o, text=m, font=F_SMALL if len(m) > 18 else F_MED, fg_color="#555" if other else BLUE,
                          command=(self.type_medication if other else (lambda m=m: self.set_medication(m)))
                          ).grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=8, pady=5)
        last = 1 + len(opts) // 2
        o.grid_rowconfigure(last, weight=1)
        ctk.CTkButton(o, text="Cancel", font=F_MED, fg_color=GREY, command=self.close_overlay).grid(
            row=last, column=len(opts) % 2, sticky="nsew", padx=8, pady=5)

    def set_medication(self, name):
        self.close_overlay()
        self.medication = name
        self.btn_med.configure(text=f"Medication:\n{name}")

    def type_medication(self):
        """On-screen keyboard for a medication that is not in the list."""
        text = {"v": ""}
        self.shift = False
        o = self.open_overlay()
        o.grid_columnconfigure(0, weight=1)
        lbl = ctk.CTkLabel(o, text="_", font=("DejaVu Sans", 30, "bold"), anchor="w")
        lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 2))
        keys_frame = ctk.CTkFrame(o, fg_color="transparent")
        keys_frame.grid(row=1, column=0, sticky="nsew", padx=8)
        o.grid_rowconfigure(1, weight=1)
        letter_btns = []

        def show():
            lbl.configure(text=(text["v"] or "") + "_")

        def press(ch):
            if len(text["v"]) < presets.NAME_MAX:
                text["v"] += ch.upper() if self.shift and ch.isalpha() else ch
                show()

        def back():
            text["v"] = text["v"][:-1]
            show()

        def toggle_shift():
            self.shift = not self.shift
            for b, ch in letter_btns:
                b.configure(text=ch.upper() if self.shift else ch)

        def ok():
            try:
                name = presets.clean_name(text["v"])
            except pump.PumpError as e:
                lbl.configure(text=str(e))
                return
            self.set_medication(name)

        rows = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", ".,-/%:"]
        for r, chars in enumerate(rows):
            keys_frame.grid_rowconfigure(r, weight=1)
            for c, ch in enumerate(chars):
                keys_frame.grid_columnconfigure(c, weight=1)
                b = ctk.CTkButton(keys_frame, text=ch, font=F_KEY, width=80, fg_color=BLUE,
                                  command=lambda ch=ch: press(ch))
                b.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
                if ch.isalpha():
                    letter_btns.append((b, ch))
        bottom = ctk.CTkFrame(o, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=6)
        bottom.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(bottom, text="Cancel", font=F_SMALL, height=56, width=130, fg_color=GREY,
                      command=self.close_overlay).grid(row=0, column=0, padx=3)
        ctk.CTkButton(bottom, text="Shift", font=F_SMALL, height=56, width=110, fg_color="#555",
                      command=toggle_shift).grid(row=0, column=1, padx=3, sticky="w")
        ctk.CTkButton(bottom, text="Space", font=F_SMALL, height=56, fg_color=BLUE,
                      command=lambda: press(" ")).grid(row=0, column=2, padx=3, sticky="ew")
        bottom.grid_columnconfigure(2, weight=3)
        ctk.CTkButton(bottom, text="DEL", font=F_SMALL, height=56, width=110, fg_color="#555",
                      command=back).grid(row=0, column=3, padx=3)
        ctk.CTkButton(bottom, text="OK", font=F_MED, height=56, width=130, fg_color=GREEN,
                      command=ok).grid(row=0, column=4, padx=3)

    # ---------- Manual keypad ----------
    def key(self, k):
        if self.busy:
            return
        if k == "C":
            self.entry = ""
        elif k == "<":
            self.entry = self.entry[:-1]
        elif k == ".":
            if "." not in self.entry:
                self.entry = (self.entry or "0") + "."
        elif self.entry == "0":
            self.entry = k
        elif len(self.entry) < 6 and not ("." in self.entry and len(self.entry.split(".")[1]) >= 2):
            self.entry += k
        self.lbl_value.configure(text=f"{self.entry or '0'} mL")

    def _manual_inputs(self):
        """Returns (medication, ml) or sets a status error and returns None."""
        if not self.medication:
            self.set_status("Choose a medication first", error=True)
            return None
        try:
            ml = pump.check_ml(self.entry)
        except pump.PumpError as e:
            self.set_status(str(e), error=True)
            return None
        return self.medication, ml

    def on_manual_dispense(self):
        got = self._manual_inputs()
        if got:
            self.ask_confirm_dose(*got)

    def on_save_preset(self):
        got = self._manual_inputs()
        if not got:
            return
        med, ml = got
        try:
            self.presets = presets.add(self.presets, med, ml)
        except pump.PumpError as e:
            self.set_status(str(e), error=True)
            return
        self.auto_page = (len(self.presets) - 1) // PER_PAGE_AUTO
        self.render_auto()
        self.set_status(f"Saved preset: {med} {ml:g} mL (see AUTO)")

    # ---------- dosing ----------
    def ask_confirm_dose(self, med, value, auto=False):
        if self.busy:
            return
        try:
            ml = pump.check_ml(value)
        except pump.PumpError as e:
            self.set_status(str(e), error=True)
            return
        if auto and reverse.AUTO_PRIME:
            self.confirm("Confirm dose - check before starting",
                         [med, f"{ml:g} mL"], "CONFIRM", lambda: self.start_auto(med, ml),
                         note="The line is primed first. Then attach the syringe and press CONTINUE.")
        else:
            self.confirm("Confirm dose - check before dispensing",
                         [med, f"{ml:g} mL"], "CONFIRM", lambda: self.run_job("dose", ml, med))

    def start_auto(self, med, ml):
        """Auto sequence step 1: prime. When it finishes, poll() asks for the syringe."""
        self.pending = (med, ml)
        self.run_job("prime", reverse.PRIME_ML)
        if not self.busy:           # run_job refused (not ready etc.)
            self.pending = None

    def ask_confirm_prime(self):
        if self.busy:
            return
        self.confirm("Confirm PRIME (not a dose)", [f"Prime line: {reverse.PRIME_ML:g} mL forward"],
                     "PRIME", lambda: self.run_job("prime", reverse.PRIME_ML), ok_color="#1f6f8b",
                     note="Pumps liquid forward. Keep the tube end over a cup.")

    def show_syringe_prompt(self, med, ml):
        self.confirm("Prime complete - attach the syringe", [med, f"{ml:g} mL"], "CONTINUE",
                     lambda: self.run_job("dose", ml, med),
                     note="Press CONTINUE only when the syringe is attached.",
                     on_cancel=lambda: self.set_status("Cancelled after priming - no dose given"))

    def show_complete(self, med, ml):
        """Flashing 'process finished' pop-up. Stays until OK."""
        o = self.open_overlay()
        o.configure(fg_color=GREEN)
        o.grid_columnconfigure(0, weight=1)
        o.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(o, text=f"DOSE COMPLETE\n{med}\n{ml:g} mL", font=("DejaVu Sans", 40, "bold"),
                     justify="center", wraplength=900).grid(row=0, column=0, sticky="nsew")
        ctk.CTkButton(o, text="OK", font=F_MED, height=100, fg_color="#0e4d29", hover_color="#145c33",
                      command=self.close_overlay).grid(row=1, column=0, sticky="ew", padx=40, pady=20)
        self._flash(o, 0)

    def _flash(self, frame, n):
        if self.overlay is not frame:
            return
        if n > 16:
            frame.configure(fg_color=GREEN)
            return
        frame.configure(fg_color=GREEN if n % 2 == 0 else "#0e4d29")
        self.after(400, lambda: self._flash(frame, n + 1))

    def ask_confirm_reverse(self, ml):
        if self.busy:
            return
        self.confirm("Confirm REVERSE (not a dose)", [f"Reverse {ml:g} mL"], "REVERSE",
                     lambda: self.run_job("reverse", ml), ok_color="#8e5b1a")

    def ask_delete(self, idx, p):
        self.confirm("Delete this preset?", [p["name"], f"{p['ml']:g} mL"], "DELETE",
                     lambda: self.do_delete(idx, p), ok_color=RED)

    def do_delete(self, idx, p):
        try:
            if self.presets[idx] != p:
                raise pump.PumpError("List changed - try again")
            self.presets = presets.remove(self.presets, idx)
        except (pump.PumpError, IndexError) as e:
            self.set_status(str(e), error=True)
            return
        self.render_edit()
        self.render_auto()
        self.set_status(f"Deleted preset: {p['name']} {p['ml']:g} mL")

    def run_job(self, kind, ml, med=None):
        if self.busy:
            return
        if not self.ok:
            self.set_status("Not ready - cannot start", error=True)
            return
        try:
            if kind == "reverse":
                ml = reverse.check_reverse(ml)
            elif kind == "prime":
                ml = reverse.check_prime(ml)
            else:
                ml = pump.check_ml(ml)
        except pump.PumpError as e:
            self.set_status(str(e), error=True)
            return
        self.busy = True
        self.stopping = False
        self.job = (kind, ml, med)
        self.job_t0 = time.monotonic()
        self.refresh()
        self.set_status(self._job_text())

        def cb(err):
            self.q.put(("done", kind, ml, med, err))
        if kind == "dose":
            pump.dispense_async(ml, cb)
        elif kind == "prime":
            reverse.prime_async(ml, cb)
        else:
            reverse.reverse_async(ml, cb)

    def _job_text(self):
        kind, ml, med = self.job
        secs = int(time.monotonic() - self.job_t0)
        what = {"dose": f"Dispensing {med} {ml:g} mL", "prime": f"Priming {ml:g} mL"}.get(
            kind, f"Reversing {ml:g} mL")
        return f"{what} ... {secs} s"

    def on_stop(self):
        self.stopping = True
        self.set_status("Stopping ...", error=True)
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        try:
            pump.emergency_stop()
        except pump.PumpError as e:
            self.q.put(("msg", f"Stop failed: {e}", True))

    def on_recover(self):
        self.set_status("Recovering ...")
        threading.Thread(target=self._recover_worker, daemon=True).start()

    def _recover_worker(self):
        try:
            pump.recover()
        except pump.PumpError as e:
            self.q.put(("msg", f"Recover failed: {e}", True))

    # ---------- UI thread plumbing ----------
    def set_status(self, text, error=False):
        self.status.configure(text=text, text_color="#ff6b6b" if error else "white")

    def refresh(self):
        self.can_dose = self.ok and not self.busy
        state = "normal" if self.can_dose else "disabled"
        for b in [self.btn_dispense, self.btn_save, *self.preset_buttons, *self.reverse_buttons,
                  *self.prime_buttons]:
            b.configure(state=state)
        for b in self.edit_buttons:
            b.configure(state="disabled" if self.busy else "normal")
        self.btn_menu.configure(state="disabled" if self.busy else "normal")
        self.btn_med.configure(state="disabled" if self.busy else "normal")
        self.btn_stop.configure(state="normal" if self.busy else "disabled")
        self.btn_recover.configure(state="normal" if (not self.ok and not self.busy) else "disabled")

    def _ip_worker(self):
        ips = get_ips()
        text = ("Network: " + "   ".join(f"{ip}  (ssh group4-5@{ip})" for ip in ips)) if ips else "Network: not connected"
        self.q.put(("ip", text))

    def _check_worker(self):
        ok, msg = pump.ready()
        self.q.put(("ready", ok, msg))

    def poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "done":
                    jkind, ml, med, err = rest
                    pend, self.pending = self.pending, None
                    self.busy = False
                    self.stopping = False
                    self.job = None
                    self.incomplete = err is not None
                    what = f"{med} {ml:g} mL" if jkind == "dose" else f"{jkind} {ml:g} mL"
                    if err is not None:
                        self.set_status(f"FAILED ({what}): {err}", error=True)
                    elif jkind == "dose":
                        self.set_status(f"Done: {what} commanded")
                        self.show_complete(med, ml)
                    elif jkind == "prime":
                        self.set_status(f"Primed {ml:g} mL (not a dose)")
                        if pend:
                            self.show_syringe_prompt(*pend)
                    else:
                        self.set_status(f"Reversed {ml:g} mL (not a dose)")
                    self.refresh()
                elif kind == "ready":
                    self._check_pending = False
                    ok, msg = rest
                    was_ok = self.ok
                    self.ok = ok
                    if not ok:
                        prefix = "Last job INCOMPLETE. " if self.incomplete else ""
                        self.set_status(f"{prefix}Not ready: {msg}", error=True)
                    elif not was_ok and not self.busy:
                        self.incomplete = False
                        self.set_status("Ready")
                    self.refresh()
                elif kind == "msg":
                    self.set_status(rest[0], error=rest[1])
                elif kind == "ip":
                    self._ip_pending = False
                    if self.lbl_ip is not None:
                        self.lbl_ip.configure(text=rest[0])
        except queue.Empty:
            pass
        self._ticks += 1
        if self.busy and self.job and not self.stopping and self._ticks % 10 == 0:
            self.set_status(self._job_text())
        if self._ticks % 15 == 1 and not self._check_pending and not self.busy:
            self._check_pending = True
            threading.Thread(target=self._check_worker, daemon=True).start()
        if self.lbl_ip is not None and self._ticks % 50 == 1 and not self._ip_pending:
            self._ip_pending = True
            threading.Thread(target=self._ip_worker, daemon=True).start()
        self.after(100, self.poll)


if __name__ == "__main__":
    App(fullscreen="--fullscreen" in sys.argv).mainloop()
