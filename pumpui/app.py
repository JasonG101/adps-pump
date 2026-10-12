"""
app.py - minimal touch UI (CustomTkinter). All pump logic lives in pump_backend.py.

Run on a laptop:  PUMP_MOCK=1 python3 app.py
Run on the Pi:    python3 app.py --fullscreen
"""
import queue
import socket
import subprocess
import sys
import threading

import customtkinter as ctk

import pump_backend as pump

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

W, H = 1024, 600
F_BIG = ("DejaVu Sans", 44, "bold")
F_MED = ("DejaVu Sans", 28, "bold")
F_SMALL = ("DejaVu Sans", 20)
GREEN, RED, GREY = "#1f9d55", "#c0392b", "#3a3a3a"


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
        self.busy = False               # a dose is running
        self.ok = False                 # Klipper reports ready
        self.incomplete = False         # last dose did not finish
        self.entry = ""                 # Manual mode text
        self._check_pending = False
        self._ip_pending = False
        self._ticks = 0
        self.lbl_ip = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="PROTOTYPE - WATER ONLY - NOT FOR CLINICAL USE",
                     font=("DejaVu Sans", 16, "bold"), text_color="#e6a23c"
                     ).grid(row=0, column=0, sticky="ew", pady=(4, 0))

        self.screens = {}
        for name, build in (("home", self.build_home), ("manual", self.build_manual),
                            ("auto", self.build_auto)):
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
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

        if pump.CONFIG_WARNINGS:
            self.set_status("; ".join(pump.CONFIG_WARNINGS), error=True)
        self.show("home")
        self.refresh()
        self.after(100, self.poll)

    # ---------- screens ----------
    def build_home(self, f):
        f.grid_columnconfigure((0, 1), weight=1)
        f.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(f, text="ADPS Dose Preparation", font=F_BIG).grid(
            row=0, column=0, columnspan=2, pady=(10, 0))
        ctk.CTkButton(f, text="AUTO", font=F_BIG, command=lambda: self.show("auto")).grid(
            row=1, column=0, sticky="nsew", padx=20, pady=20)
        ctk.CTkButton(f, text="MANUAL", font=F_BIG, command=lambda: self.show("manual")).grid(
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
            ctk.CTkButton(pad, text="DEL" if k == "<" else k, font=F_BIG, fg_color="#2b4a6f",
                          command=lambda k=k: self.key(k)).grid(
                row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=4)
        side = ctk.CTkFrame(f, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(3, weight=1)
        ctk.CTkButton(side, text="< Back", font=F_SMALL, height=60, fg_color=GREY,
                      command=lambda: self.show("home")).grid(row=0, column=0, sticky="ew", pady=4)
        self.lbl_value = ctk.CTkLabel(side, text="0 mL", font=F_BIG)
        self.lbl_value.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(side, text=f"Max {pump.MAX_ML:g} mL", font=F_SMALL).grid(row=2, column=0)
        self.btn_dispense = ctk.CTkButton(side, text="DISPENSE", font=F_MED, fg_color=GREEN,
                                          hover_color="#27ae60", command=self.on_manual_dispense)
        self.btn_dispense.grid(row=3, column=0, sticky="nsew", pady=4)
        ctk.CTkButton(side, text="Clear", font=F_SMALL, height=60, fg_color=GREY,
                      command=lambda: self.key("C")).grid(row=4, column=0, sticky="ew", pady=4)

    def build_auto(self, f):
        f.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(f, text="< Back", font=F_SMALL, height=60, width=160, fg_color=GREY,
                      command=lambda: self.show("home")).grid(row=0, column=0, sticky="w", pady=4)
        self.preset_buttons = []
        if not pump.PRESETS:
            ctk.CTkLabel(f, text="No valid presets (see config.json)", font=F_MED).grid(
                row=1, column=0, columnspan=2)
        for i, p in enumerate(pump.PRESETS):
            f.grid_rowconfigure(1 + i // 2, weight=1)
            b = ctk.CTkButton(f, text=f"{p['name']}\n{p['ml']:g} mL", font=F_MED, fg_color=GREEN,
                              hover_color="#27ae60",
                              command=lambda ml=p["ml"]: self.start_dose(ml))
            b.grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)
            self.preset_buttons.append(b)

    def show(self, name):
        self.screens[name].tkraise()

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

    def on_manual_dispense(self):
        self.start_dose(self.entry)

    # ---------- dosing ----------
    def start_dose(self, value):
        if self.busy:
            return
        try:
            ml = pump.check_ml(value)
        except pump.PumpError as e:
            self.set_status(str(e), error=True)
            return
        self.busy = True
        self.refresh()
        self.set_status(f"Dispensing {ml:g} mL ...")
        pump.dispense_async(ml, lambda err, ml=ml: self.q.put(("done", ml, err)))

    def on_stop(self):
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
        can_dose = self.ok and not self.busy
        state = "normal" if can_dose else "disabled"
        self.btn_dispense.configure(state=state)
        for b in self.preset_buttons:
            b.configure(state=state)
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
                    ml, err = rest
                    self.busy = False
                    self.incomplete = err is not None
                    if err is None:
                        self.set_status(f"Done: {ml:g} mL commanded")
                    else:
                        self.set_status(f"FAILED: {err}", error=True)
                    self.refresh()
                elif kind == "ready":
                    self._check_pending = False
                    ok, msg = rest
                    was_ok = self.ok
                    self.ok = ok
                    if not ok:
                        prefix = "Dose INCOMPLETE. " if self.incomplete else ""
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
        if self._ticks % 15 == 1 and not self._check_pending and not self.busy:
            self._check_pending = True
            threading.Thread(target=self._check_worker, daemon=True).start()
        if self.lbl_ip is not None and self._ticks % 50 == 1 and not self._ip_pending:
            self._ip_pending = True
            threading.Thread(target=self._ip_worker, daemon=True).start()
        self.after(100, self.poll)


if __name__ == "__main__":
    App(fullscreen="--fullscreen" in sys.argv).mainloop()
