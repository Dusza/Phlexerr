"""Tiny JSON persistence in the /config volume (report cache, keep-list, deletion log)."""
import json
import os
import threading
import time

_lock = threading.Lock()


def _path(cfg, name):
    return os.path.join(cfg.config_dir, name)


def read_json(cfg, name, default):
    try:
        with open(_path(cfg, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(cfg, name, data, private=False):
    try:
        os.makedirs(cfg.config_dir, exist_ok=True)
        tmp = _path(cfg, name) + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _path(cfg, name))
        return True
    except OSError as e:
        print(f"[store] could not write {name}: {e}", flush=True)
        return False


def load_keep(cfg):
    return set(read_json(cfg, "keep.json", {"ids": []}).get("ids", []))


def save_keep(cfg, ids):
    write_json(cfg, "keep.json", {"ids": sorted(ids)})


def append_log(cfg, entry):
    entry = {"time": int(time.time()), **entry}
    with _lock:
        try:
            os.makedirs(cfg.config_dir, exist_ok=True)
            with open(_path(cfg, "deletions.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            print(f"[store] could not write deletion log: {e}", flush=True)


def read_log(cfg, limit=300):
    try:
        with open(_path(cfg, "deletions.log"), "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out[::-1]
