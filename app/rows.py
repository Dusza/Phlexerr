"""Home rows: saved criteria that keep a Plex collection (pinned to Plex's Home screen) up to date.

A row picks items from the library report: by actor / director / studio / collection / title keyword (same matching
as protection rules, any one criterion is enough), narrowed by optional filters. It only ever creates or edits Plex
*collections*; it never deletes media.
"""
import random
import re
import time
import uuid

from . import extlists, rules

MEDIA = ("both", "movie", "show")
SORTS = {"added": "Date added", "rating": "IMDb rating", "year": "Year", "released": "Release date", "plays": "Times watched",
         "lastwatched": "Last watched", "title": "Title", "list": "Order in the list", "manual": "My own order", "shuffle": "Random"}
DEFAULT_DIR = {"list": "asc", "manual": "asc", "added": "desc", "rating": "desc", "year": "desc", "released": "desc", "plays": "desc", "lastwatched": "desc", "title": "asc"}
LEGACY = {"newest": ("added", "desc")}      # rows saved by older versions
MAX_ROWS, MAX_CRITERIA = 50, 20


def validate(d):
    name = " ".join(str(d.get("name", "")).split())
    if not name or len(name) > 100 or re.search(r"[\x00-\x1f]", name):
        return None, "give the row a name (up to 100 characters)"
    media = d.get("media") if d.get("media") in MEDIA else "both"
    crit_in = d.get("criteria") or []
    if not isinstance(crit_in, list) or len(crit_in) > MAX_CRITERIA:
        return None, f"at most {MAX_CRITERIA} criteria"
    crit = []
    for c in crit_in:
        f, err = rules.validate(c if isinstance(c, dict) else {})
        if err:
            return None, err
        crit.append(f)

    def num(key, lo, hi, cast):
        v = d.get(key)
        if v in (None, ""):
            return None, None
        try:
            v = cast(v)
        except (TypeError, ValueError):
            return None, f"'{key}' must be a number"
        return (v, None) if lo <= v <= hi else (None, f"'{key}' must be between {lo} and {hi}")

    min_rating, err = num("min_rating", 0, 10, float)
    if err:
        return None, err
    added_days, err = num("added_days", 1, 3650, int)
    if err:
        return None, err
    limit, err = num("limit", 1, 500, int)
    if err:
        return None, err
    sort, direction = d.get("sort"), d.get("sort_dir")
    if sort in LEGACY and direction not in ("asc", "desc"):
        sort, direction = LEGACY[sort]
    elif sort in LEGACY:
        sort = LEGACY[sort][0]
    if sort not in SORTS:
        sort = "added"
    if direction not in ("asc", "desc"):
        direction = DEFAULT_DIR.get(sort, "desc")
    sources, err = extlists.validate_sources(d)
    if err:
        return None, err
    mo = d.get("manual_order") or []
    if not isinstance(mo, list) or len(mo) > 5000:
        return None, "the manual order is too long"
    manual = [x for x in dict.fromkeys(mo) if isinstance(x, str) and 0 < len(x) <= 80]
    row = {"manual_order": manual, "sources": sources, "name": name, "media": media, "criteria": crit, "min_rating": min_rating, "added_days": added_days,
           "unwatched": bool(d.get("unwatched")), "limit": limit or 50, "sort": sort, "sort_dir": direction,
           "pin": bool(d.get("pin", True)), "shared": bool(d.get("shared", True)), "auto": bool(d.get("auto", True))}
    if not crit and not sources and min_rating is None and added_days is None and not row["unwatched"] and media == "both":
        return None, "add at least one criterion or filter, otherwise the row would contain your whole library"
    return row, None


def new_row(fields):
    return {"id": uuid.uuid4().hex[:12], "created": int(time.time()), **fields}


def _never_watched(i):
    return not i.get("last_viewed_ts") and not i.get("plays_total") and not i.get("pct")


def select(items, row, now=None):
    now = now or time.time()
    ms = [rules.matcher({**c, "id": "x"}) for c in row.get("criteria", [])]
    lst = row.get("_ids")                       # ids from an external list (resolved by the caller)
    pos = {t: n for n, t in enumerate(lst)} if lst is not None else None
    out = []
    for i in items:
        if pos is not None and i.get("imdb_id") not in pos:
            continue
        if row["media"] != "both" and i["type"] != row["media"]:
            continue
        if ms and not any(m(i) for m in ms):
            continue
        if row.get("min_rating") is not None and (i.get("rating") is None or i["rating"] < row["min_rating"]):
            continue
        if row.get("added_days") and (not i.get("added_ts") or i["added_ts"] < now - row["added_days"] * 86400):
            continue
        if row.get("unwatched") and not _never_watched(i):
            continue
        out.append(i)
    s, direction = row.get("sort", "added"), row.get("sort_dir")
    if s in LEGACY:
        s, direction = LEGACY[s][0], direction or LEGACY[s][1]
    rev = (direction or DEFAULT_DIR.get(s, "desc")) == "desc"
    if s == "shuffle":
        random.shuffle(out)
    elif s == "manual":
        # your own order first; anything you haven't placed yet goes after, oldest release first
        mpos = {k: n for n, k in enumerate(row.get("manual_order") or [])}
        out.sort(key=lambda i: (mpos.get(i.get("keep_id"), len(mpos)), i.get("released") or str(i.get("year") or "9999")))
    elif s == "list" and pos is not None:
        out.sort(key=lambda i: pos[i["imdb_id"]], reverse=rev)
    else:
        # items with no value for the chosen field always go last, whichever direction is chosen
        key = {"added": lambda i: i.get("added_ts"), "rating": lambda i: i.get("rating"), "year": lambda i: i.get("year"),
               "released": lambda i: i.get("released"), "plays": lambda i: i.get("plays_total"),
               "lastwatched": lambda i: i.get("last_viewed_ts"), "title": lambda i: rules.norm(i.get("title"))}.get(s, lambda i: i.get("added_ts"))
        have = [i for i in out if key(i) not in (None, "")]
        miss = [i for i in out if key(i) in (None, "")]
        have.sort(key=lambda i: (key(i), rules.norm(i.get("title"))), reverse=rev)
        out = have + miss
    return out[: row.get("limit", 50)]


def summary(row):
    bits = []
    if extlists.row_sources(row):
        bits.append(extlists.describe_all(extlists.row_sources(row)))
    if row["criteria"]:
        bits.append(" or ".join(rules.label(c) for c in row["criteria"]))
    if row["media"] != "both":
        bits.append("movies only" if row["media"] == "movie" else "shows only")
    if row.get("min_rating") is not None:
        bits.append(f"IMDb ≥ {row['min_rating']:g}")
    if row.get("added_days"):
        bits.append(f"added in the last {row['added_days']} days")
    if row.get("unwatched"):
        bits.append("never watched")
    return "; ".join(bits)
