"""
reverse.py - small capped pump moves that are NOT doses: reverse (pull back /
clear line) and prime (fill the tube with liquid before attaching the syringe).

Each uses its own Klipper macro (reverse_macro.cfg, prime_macro.cfg) with its own
volume cap. They share the backend's busy lock, so a dose, a prime and a reverse
can never run at the same time. Kept out of pump_backend.py so Jason's file is
untouched; Jason can merge it in if he prefers.
"""
import threading

import pump_backend as pump

MAX_REVERSE_ML = float(pump.CFG.get("max_reverse_ml", 5))   # keep equal to max_ml in PUMP_REVERSE
OPTIONS = [o for o in (0.5, 1.0, 2.0, 5.0) if o <= MAX_REVERSE_ML]

MAX_PRIME_ML = float(pump.CFG.get("max_prime_ml", 3))       # keep equal to max_ml in PUMP_PRIME
PRIME_ML = min(float(pump.CFG.get("prime_ml", 1.5)), MAX_PRIME_ML)   # ~1.5 mL fills the current tubing
AUTO_PRIME = bool(pump.CFG.get("auto_prime", True))         # Auto mode primes before the dose


def check_reverse(value):
    ml = pump.check_ml(value)
    if ml > MAX_REVERSE_ML:
        raise pump.PumpError(f"Reverse maximum is {MAX_REVERSE_ML:g} mL")
    return ml


def check_prime(value):
    ml = pump.check_ml(value)
    if ml > MAX_PRIME_ML:
        raise pump.PumpError(f"Prime maximum is {MAX_PRIME_ML:g} mL")
    return ml


def _run(macro, ml):
    """Blocking. Same checks as pump_backend.dispense()."""
    ok, msg = pump.ready()
    if not ok:
        raise pump.PumpError(msg)
    if not pump._busy.acquire(blocking=False):
        raise pump.PumpError("Pump is busy")
    try:
        if pump.MOCK:
            pump._mock["abort"].clear()
            if pump._mock["abort"].wait(timeout=ml * float(pump.CFG.get("mock_s_per_ml", 0.3))):
                raise pump.PumpError("Stopped")
        else:
            pump._post("/printer/gcode/script", {"script": f"{macro} ML={ml:.2f}"},
                       timeout=(3, 30 + ml * pump.TIMEOUT_S_PER_ML))
    finally:
        pump._busy.release()


def reverse(ml):
    _run("PUMP_REVERSE", check_reverse(ml))


def prime(ml):
    _run("PUMP_PRIME", check_prime(ml))


def _async(fn, ml, on_done):
    def run():
        try:
            fn(ml)
            on_done(None)
        except pump.PumpError as e:
            on_done(e)
        except Exception as e:
            on_done(pump.PumpError(f"Unexpected error: {e}"))
    threading.Thread(target=run, daemon=True).start()


def reverse_async(ml, on_done):
    _async(reverse, ml, on_done)


def prime_async(ml, on_done):
    _async(prime, ml, on_done)
