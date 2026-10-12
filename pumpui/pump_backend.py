"""
pump_backend.py - everything that talks to the pump. No UI code in here.

Handover note for the UI developer: build any UI you like, but only call:
    ready()                      -> (ok: bool, message: str)
    check_ml(value)              -> float, or raises PumpError if invalid
    dispense_async(ml, on_done)  -> runs in a thread; on_done(None | PumpError)
                                    is called from that worker thread
    emergency_stop()
    recover()                    -> restarts Klipper firmware after an e-stop
    MAX_ML, PRESETS, CONFIG_WARNINGS

Speed and the mL->steps conversion are locked inside the DISPENSE macro in
printer.cfg. This file never sends a speed.

Set PUMP_MOCK=1 to run with a fake pump (laptop development, no hardware).
"""
import json
import math
import os
import threading

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config.json")) as f:
    CFG = json.load(f)

MOCK = os.environ.get("PUMP_MOCK") == "1"
URL = CFG.get("moonraker_url", "http://127.0.0.1:7125")
MAX_ML = float(CFG["max_ml"])  # keep equal to max_ml in the DISPENSE macro
TIMEOUT_S_PER_ML = float(CFG.get("timeout_s_per_ml", 5))


class PumpError(Exception):
    pass


def check_ml(value):
    """Validate a volume. Returns a float rounded to 0.01 mL or raises PumpError."""
    try:
        ml = float(value)
    except (TypeError, ValueError):
        raise PumpError("Enter a number")
    if not math.isfinite(ml):
        raise PumpError("Enter a number")
    ml = round(ml, 2)
    if ml <= 0:
        raise PumpError("Volume must be above 0 mL")
    if ml > MAX_ML:
        raise PumpError(f"Maximum is {MAX_ML:g} mL")
    return ml


# Presets above the limit are dropped and reported, never silently kept.
PRESETS, CONFIG_WARNINGS = [], []
for _p in CFG.get("presets", []):
    try:
        PRESETS.append({"name": str(_p["name"]), "ml": check_ml(_p["ml"])})
    except (KeyError, PumpError) as _e:
        CONFIG_WARNINGS.append(f"Preset {_p!r} ignored: {_e}")

_busy = threading.Lock()
_mock = {"stopped": False, "abort": threading.Event()}


def _post(path, payload=None, timeout=5):
    try:
        r = requests.post(URL + path, json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise PumpError("Cannot reach Moonraker")
    except requests.exceptions.Timeout:
        raise PumpError("Command timed out - pump may still be running, press STOP")
    except requests.exceptions.RequestException as e:
        raise PumpError(f"Request failed: {e}")
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise PumpError(msg.strip().splitlines()[0] if msg.strip() else "Pump error")
    return r


def ready():
    """Returns (ok, message). ok is True only when Klipper reports 'ready'."""
    if MOCK:
        return (False, "Stopped - tap RECOVER") if _mock["stopped"] else (True, "Ready (mock pump)")
    try:
        result = requests.get(URL + "/printer/info", timeout=2).json()["result"]
    except Exception:
        return False, "Cannot reach Moonraker"
    if result.get("state") == "ready":
        return True, "Ready"
    msg = (result.get("state_message") or result.get("state") or "Not ready").strip()
    return False, msg.splitlines()[0] if msg else "Not ready"


def dispense(ml):
    """Blocking. Validates, checks Klipper is ready, then runs one dose."""
    ml = check_ml(ml)
    ok, msg = ready()
    if not ok:
        raise PumpError(msg)
    if not _busy.acquire(blocking=False):
        raise PumpError("Pump is busy")
    try:
        if MOCK:
            _mock["abort"].clear()
            if _mock["abort"].wait(timeout=ml * float(CFG.get("mock_s_per_ml", 0.3))):
                raise PumpError("Stopped")
        else:
            # The call returns only when the move finishes, so the read timeout
            # must cover the whole dose (the 3 s timeout in pump.py would not).
            _post("/printer/gcode/script", {"script": f"DISPENSE ML={ml:.2f}"},
                  timeout=(3, 30 + ml * TIMEOUT_S_PER_ML))
    finally:
        _busy.release()


def dispense_async(ml, on_done):
    def run():
        try:
            dispense(ml)
            on_done(None)
        except PumpError as e:
            on_done(e)
        except Exception as e:  # never let the worker die silently
            on_done(PumpError(f"Unexpected error: {e}"))
    threading.Thread(target=run, daemon=True).start()


def emergency_stop():
    if MOCK:
        _mock["stopped"] = True
        _mock["abort"].set()
        return
    _post("/printer/emergency_stop", timeout=3)


def recover():
    """Clear the shutdown state after an emergency stop."""
    if MOCK:
        _mock["stopped"] = False
        return
    _post("/printer/firmware_restart", timeout=10)
