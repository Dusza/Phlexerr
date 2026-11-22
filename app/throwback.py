"""'Released on this day' row: movies whose release anniversary is today (or this week / month), N+ years ago."""
import datetime
import random
import re

from . import rules

FIELDS = ("throwback_enabled", "throwback_name", "throwback_scope", "throwback_min_years", "throwback_max_years",
          "throwback_min_rating", "throwback_limit", "throwback_order", "throwback_pin", "throwback_before", "throwback_after",
          "throwback_genres", "throwback_genre_mode", "throwback_tags")
SCOPES = {"day": "Same day of the year", "week": "Within 3 days of today", "month": "Same month", "custom": "Custom window (days before / after today)"}
ORDERS = {"rating": "Highest IMDb rating first", "oldest": "Oldest release first", "newest": "Newest release first",
          "title": "Title A-Z", "shuffle": "Shuffled (new order each day)"}
DEFAULTS = {"throwback_enabled": "false", "throwback_name": "Released On This Day", "throwback_scope": "day",
            "throwback_min_years": "10", "throwback_max_years": "", "throwback_min_rating": "6", "throwback_limit": "40", "throwback_before": "7", "throwback_after": "7", "throwback_genres": "", "throwback_genre_mode": "any", "throwback_tags": "",
            "throwback_order": "rating", "throwback_pin": "true"}


def split_list(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def validate(values):
    """Error string or None. Values arrive as strings (they are stored in settings.json)."""
    for k, v in values.items():
        if k not in FIELDS:
            return f"unknown setting: {k}"
        if not isinstance(v, str) or len(v) > 200:
            return f"invalid value for {k}"
        v = v.strip()
        if k in ("throwback_enabled", "throwback_pin") and v not in ("true", "false"):
            return "invalid on/off value"
        if k == "throwback_name" and (not v or len(v) > 100 or any(ord(c) < 32 for c in v)):
            return "give the row a name (up to 100 characters)"
        if k == "throwback_genre_mode" and v not in ("any", "all"):
            return "genre match must be 'any' or 'all'"
        if k in ("throwback_genres", "throwback_tags") and (len(split_list(v)) > 30 or not all(re.search(r"[^*?\s]", x) for x in split_list(v))):
            return "list up to 30 names, comma-separated (a lone * would match everything)"
        if k == "throwback_scope" and v not in SCOPES:
            return "unknown timeframe"
        if k == "throwback_order" and v not in ORDERS:
            return "unknown order"
        if k in ("throwback_min_years", "throwback_max_years", "throwback_limit", "throwback_before", "throwback_after"):
            if k in ("throwback_max_years", "throwback_limit") and v == "":
                continue    # blank = no limit
            lo, hi = {"throwback_limit": (1, 5000), "throwback_before": (0, 183), "throwback_after": (0, 183)}.get(k, (0, 150))
            if not v.isdigit() or not lo <= int(v) <= hi:
                return f"{k.replace('throwback_', '').replace('_', ' ')} must be a whole number from {lo} to {hi}"
        if k == "throwback_min_rating":
            try:
                ok = 0 <= float(v) <= 10
            except ValueError:
                ok = False
            if not ok:
                return "minimum IMDb rating must be a number from 0 to 10"
    mn, mx = values.get("throwback_min_years"), values.get("throwback_max_years")
    if mn and mx and mn.strip().isdigit() and mx.strip().isdigit() and int(mx) < int(mn):
        return "'at most' years can't be smaller than 'at least' years"
    return None


def parse(raw):
    d = {**DEFAULTS, **{k: str(v).strip() for k, v in (raw or {}).items() if k in FIELDS}}
    return {"enabled": d["throwback_enabled"] == "true", "name": d["throwback_name"], "scope": d["throwback_scope"],
            "min_years": int(d["throwback_min_years"]), "max_years": int(d["throwback_max_years"]) if d["throwback_max_years"] else None,
            "min_rating": float(d["throwback_min_rating"]), "limit": int(d["throwback_limit"]) if d["throwback_limit"] else None,
            "genres": split_list(d["throwback_genres"]), "genre_mode": d["throwback_genre_mode"], "tags": split_list(d["throwback_tags"]),
            "before": int(d["throwback_before"]), "after": int(d["throwback_after"]), "order": d["throwback_order"],
            "pin": d["throwback_pin"] == "true"}


def _anniversary(rel, year):
    try:
        return datetime.date(year, rel.month, rel.day)
    except ValueError:           # Feb 29 in a non-leap year
        return datetime.date(year, 2, 28)


def select(items, conf, today=None):
    today = today or datetime.date.today()
    gtests = [rules.name_test(g) for g in conf.get("genres", [])]
    ttests = [rules.name_test(g) for g in conf.get("tags", [])]
    out = []
    for i in items:
        if i.get("type") != "movie" or not i.get("released"):
            continue
        try:
            rel = datetime.date.fromisoformat(i["released"])
        except ValueError:
            continue
        if conf["scope"] == "month":
            if rel.month != today.month:
                continue
            age = today.year - rel.year
        else:
            before, after = {"day": (0, 0), "week": (3, 3)}.get(conf["scope"], (conf["before"], conf["after"]))
            best = None
            for y in (today.year - 1, today.year, today.year + 1):   # window can straddle New Year
                a = _anniversary(rel, y)
                if -before <= (a - today).days <= after:     # a - today < 0 means the anniversary already passed
                    best = a
            if best is None:
                continue
            age = best.year - rel.year
        if age < conf["min_years"] or (conf["max_years"] is not None and age > conf["max_years"]):
            continue
        if i.get("rating") is None or i["rating"] < conf["min_rating"]:     # unrated titles never qualify
            continue
        if gtests:       # genres: any one of them, or every one of them
            have = i.get("genres") or []
            hit = [any(t(g) for g in have) for t in gtests]
            if not (all(hit) if conf.get("genre_mode") == "all" else any(hit)):
                continue
        if ttests and not any(t(x) for x in (i.get("labels") or []) for t in ttests):     # tags: any one of them
            continue
        out.append((i, rel))
    o = conf["order"]
    if o == "rating":
        out.sort(key=lambda t: (-t[0]["rating"], t[1]))
    elif o == "oldest":
        out.sort(key=lambda t: t[1])
    elif o == "newest":
        out.sort(key=lambda t: t[1], reverse=True)
    elif o == "title":
        out.sort(key=lambda t: rules.norm(t[0].get("title")))
    else:
        random.Random(today.isoformat()).shuffle(out)
    res = [t[0] for t in out]
    return res[: conf["limit"]] if conf["limit"] else res
