# UI changes (Germanus)

New files: presets.py, reverse.py, reverse_macro.cfg. Changed: app.py. pump_backend.py is untouched.

## Install on the Pi
1. Append reverse_macro.cfg to printer.cfg (next to DISPENSE). Keep steps_per_ml and speed equal to DISPENSE. Restart Klipper.
2. Copy app.py, presets.py, reverse.py into /home/group4-5/pumpui, then `sudo systemctl restart deploypumpui`.
3. Presets are saved to presets.json in that folder. Delete it to go back to the config.json presets.

## Behaviour
- MENU (top right, every screen): Auto, Manual, Edit presets, Maintenance, Home. Disabled while a job runs.
- Manual: choose a medication (list or Other + keyboard), enter mL, DISPENSE -> confirm screen. Save preset adds it to Auto.
- Auto: tap preset -> confirm screen -> dispense. 6 per page.
- Maintenance: reverse 0.5/1/2/5 mL, confirmed, capped by the PUMP_REVERSE macro, never reported as a dose.
- STOP/RECOVER unchanged and always visible.

Backend note: reverse.py uses pump_backend._busy and _post (private names) so a dose and a reverse can never overlap.

## Update 2 Nov: prime flow (Logan's request)
New: prime_macro.cfg. Changed: app.py, reverse.py.
- Auto: tap preset -> confirm -> PRIME (default 1.5 mL forward) -> "attach the syringe" screen -> CONTINUE -> dose -> flashing DOSE COMPLETE pop-up (OK to close).
- Manual: PRIME button (top right of the side panel); DISPENSE still goes straight to confirm -> dose -> completion pop-up.
- Maintenance: PRIME LINE button.
- config.json (optional keys): "prime_ml": 1.5, "max_prime_ml": 3, "auto_prime": true (false = Auto skips priming).

## Install
1. Append prime_macro.cfg to printer.cfg. In BOTH prime_macro.cfg and reverse_macro.cfg set steps_per_ml (and speed) equal to the CURRENT values in the DISPENSE macro on the Pi (calibration may now be ~548.8, not 637). Restart Klipper.
2. scp app.py reverse.py to /home/group4-5/pumpui/, then `sudo systemctl restart pumpui`.
