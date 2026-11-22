"""'Leaving Soon': titles you have marked for deletion, shown as a Plex Home row so people can watch them first.
Off by default. Nothing is ever deleted by this feature: a mark only adds a badge and a Plex collection."""
import re

FIELDS = ("leaving_enabled", "leaving_name", "leaving_days", "leaving_pin")
DEFAULTS = {"leaving_enabled": "false", "leaving_name": "Leaving Soon", "leaving_days": "14", "leaving_pin": "true"}


def validate(values):
    for k, v in values.items():
        if k not in FIELDS:
            return f"unknown setting: {k}"
        if not isinstance(v, str) or len(v) > 200:
            return f"invalid value for {k}"
        v = v.strip()
        if k in ("leaving_enabled", "leaving_pin") and v not in ("true", "false"):
            return "invalid on/off value"
        if k == "leaving_name" and (not v or len(v) > 100 or any(ord(c) < 32 for c in v)):
            return "give the Leaving Soon row a name (up to 100 characters)"
        if k == "leaving_days" and not (v.isdigit() and 1 <= int(v) <= 365):
            return "days until a title is due must be a whole number from 1 to 365"
    return None


def parse(raw):
    d = {**DEFAULTS, **{k: str(v).strip() for k, v in (raw or {}).items() if k in FIELDS}}
    return {"enabled": d["leaving_enabled"] == "true", "name": d["leaving_name"], "days": int(d["leaving_days"]),
            "pin": d["leaving_pin"] == "true"}
