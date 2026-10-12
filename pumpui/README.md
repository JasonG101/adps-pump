# ADPS pump UI (minimal)

- `pump_backend.py` - all pump logic (limits, Moonraker calls, stop/recover). No UI code.
- `app.py` - minimal CustomTkinter touch UI: Home, Manual (keypad), Auto (presets). STOP on every screen.
- `config.json` - `max_ml`, presets, timeouts. `max_ml` must equal `max_ml` in the DISPENSE macro.
- `dispense_macro.cfg` - append to Klipper `printer.cfg`. Holds the locked speed, calibration constants and the second max-volume check.

Run on a laptop (no hardware): `pip install customtkinter requests` then `PUMP_MOCK=1 python3 app.py`
Run on the Pi: `~/pumpui-env/bin/python app.py --fullscreen`

For the UI developer: call only `ready()`, `check_ml()`, `dispense_async()`, `emergency_stop()`, `recover()`,
and read `MAX_ML` / `PRESETS`. Never send a speed; the macro locks it.
