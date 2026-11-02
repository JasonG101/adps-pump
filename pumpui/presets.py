"""
presets.py - preset storage (medication name + mL). No UI code, no pump code.

Presets live in presets.json next to this file (override with PRESETS_FILE).
If the file does not exist yet, the presets from config.json are used as the
starting list; the file is created the first time a preset is added/deleted.
Every volume goes through pump_backend.check_ml, so the max limit still applies.
"""
import json
import os
import tempfile

import pump_backend as pump

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("PRESETS_FILE", os.path.join(HERE, "presets.json"))
NAME_MAX = 24
MAX_PRESETS = 24

# Demo medications from the Test D sheet (water only). Override with
# "medications": [...] in config.json.
DEFAULT_MEDICATIONS = [
    "Saline flush 0.9%",
    "Adrenaline 1:10,000",
    "Ondansetron 4 mg/2 mL",
    "Naloxone 400 mcg/1 mL",
    "Morphine 10 mg/1 mL",
]
MEDICATIONS = [str(m)[:NAME_MAX] for m in pump.CFG.get("medications", DEFAULT_MEDICATIONS)]


def clean_name(name):
    name = " ".join(str(name).split())
    if not name:
        raise pump.PumpError("Enter a medication name")
    if len(name) > NAME_MAX:
        raise pump.PumpError(f"Name too long (max {NAME_MAX} characters)")
    return name


def _clean(raw):
    """Validate a list of {'name','ml'}. Returns (good_list, warnings)."""
    good, warnings = [], []
    if not isinstance(raw, list):
        return [], ["presets file is not a list"]
    for p in raw:
        try:
            item = {"name": clean_name(p["name"]), "ml": pump.check_ml(p["ml"])}
        except (KeyError, TypeError, pump.PumpError) as e:
            warnings.append(f"Preset {p!r} ignored: {e}")
            continue
        if any(q["name"].lower() == item["name"].lower() and q["ml"] == item["ml"] for q in good):
            warnings.append(f"Duplicate preset {item['name']} {item['ml']:g} mL ignored")
            continue
        good.append(item)
    return good[:MAX_PRESETS], warnings


def load():
    """Returns (presets, warnings). Never raises; a bad file falls back to config.json presets."""
    defaults = [dict(p) for p in pump.PRESETS]
    if not os.path.exists(PATH):
        return defaults, []
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return defaults, [f"presets.json unreadable ({e}); using defaults"]
    good, warnings = _clean(data)
    return good, warnings


def save(items):
    """Atomic write (temp file + rename) so a power cut cannot leave half a file."""
    good, _ = _clean(items)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(good, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PATH)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise pump.PumpError(f"Could not save presets: {e}")
    return good


def add(items, name, ml):
    """Returns the new list (already saved). Raises PumpError; items is not modified."""
    item = {"name": clean_name(name), "ml": pump.check_ml(ml)}
    if len(items) >= MAX_PRESETS:
        raise pump.PumpError(f"Preset list full ({MAX_PRESETS})")
    if any(q["name"].lower() == item["name"].lower() and q["ml"] == item["ml"] for q in items):
        raise pump.PumpError("That preset already exists")
    return save(list(items) + [item])


def remove(items, index):
    if not 0 <= index < len(items):
        raise pump.PumpError("Preset not found")
    return save(items[:index] + items[index + 1:])
