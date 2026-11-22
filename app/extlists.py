"""External lists (IMDb lists / CSV uploads) used as the source of a Plex Home row.

Only the IMDb ids matter: the row keeps the titles that are already in Plex and ignores the rest.
IMDb has no official API, so fetching uses the public CSV export of a list; if that ever stops working the
same list can be exported by hand and uploaded as a CSV instead.
"""
import csv
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import store
from .clients import ApiError

MAX_IDS = 5000
CACHE = "lists_cache.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) Phlexerr/1.0"
_TT = re.compile(r"\btt\d{6,10}\b")
_LS = re.compile(r"\bls\d{4,12}\b")


def list_id(text):
    """'https://www.imdb.com/list/ls123456789/?ref_=x' or 'ls123456789' -> 'ls123456789' (None if not a list)."""
    m = _LS.search(str(text or ""))
    return m.group(0) if m else None


_MDB = re.compile(r"mdblist\.com/lists/([A-Za-z0-9._~-]+)/([A-Za-z0-9._~%-]+)", re.I)


def mdblist_ref(text):
    """'https://mdblist.com/lists/someone/my-list/?x=1' (or 'someone/my-list') -> 'someone/my-list'."""
    t = str(text or "").strip()
    m = _MDB.search(t)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"([A-Za-z0-9._~-]+)/([A-Za-z0-9._~%-]+)", t)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def ids_from_json(obj):
    """IMDb ids, in order, from MDBList-style JSON: a list of items, or {movies: [...], shows: [...]}."""
    if isinstance(obj, dict):
        items = []
        for k in ("movies", "shows", "items"):
            if isinstance(obj.get(k), list):
                items += obj[k]
    elif isinstance(obj, list):
        items = obj
    else:
        items = []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        v = it.get("imdb_id") or it.get("imdbid") or it.get("imdbId") or it.get("imdb") or (it.get("ids") or {}).get("imdb") or (it.get("ids") or {}).get("imdbid")
        if isinstance(v, str) and re.fullmatch(r"tt\d{6,10}", v.strip()):
            out.append(v.strip())
    return list(dict.fromkeys(out))[:MAX_IDS]


def parse_csv(text):
    """Ordered, de-duplicated IMDb title ids from an IMDb list export (the 'Const' column) or any text containing ttNNNNNNN."""
    text = str(text or "")
    ids = []
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        rows = []
    col = None
    if rows:
        head = [c.strip().lower() for c in rows[0]]
        for name in ("const", "imdb id", "imdbid", "imdb_id", "id"):
            if name in head:
                col = head.index(name)
                break
    if col is not None:
        ids = [r[col].strip() for r in rows[1:] if len(r) > col and re.fullmatch(r"tt\d{6,10}", r[col].strip())]
    if not ids:
        ids = _TT.findall(text)
    return list(dict.fromkeys(ids))[:MAX_IDS]


def _kind(t):
    t = str(t or "").lower().replace(" ", "")
    if t in ("movie", "tvmovie", "video", "short", "tvshort", "tvspecial", "film"):
        return "movie"
    if "series" in t or t in ("show", "tv", "tvshow", "tvepisode"):
        return "show"
    return None


def _year(v):
    m = re.search(r"(18|19|20)\d{2}", str(v or ""))
    return int(m.group(0)) if m else None


def _num(v):
    try:
        f = float(v)
        return f if 0 < f <= 10 else None
    except (TypeError, ValueError):
        return None


def _rating(it):
    """(value 0-10, source) if the list item carries an IMDb rating under any of the usual names, else (None, None)."""
    for k in ("imdb_rating", "imdbrating", "imdbRating"):
        if _num(it.get(k)):
            return _num(it[k]), "IMDb"
    rs = it.get("ratings")
    if isinstance(rs, list):
        for x in rs:
            if isinstance(x, dict) and str(x.get("source", "")).lower().startswith("imdb") and _num(x.get("value") or x.get("rating")):
                return _num(x.get("value") or x.get("rating")), "IMDb"
    elif isinstance(rs, dict):
        for k in ("imdb", "imdbrating", "imdb_rating"):
            v = rs.get(k)
            v = v.get("value") if isinstance(v, dict) else v
            if _num(v):
                return _num(v), "IMDb"
    return None, None


def _date(v):
    m = re.match(r"(18|19|20)\d{2}-\d{2}-\d{2}", str(v or ""))
    return m.group(0) if m else None


def meta_from_json(obj):
    """imdb id -> {title, year, type, tmdb} for MDBList-style JSON (whatever the list tells us)."""
    items = []
    if isinstance(obj, dict):
        for k in ("movies", "shows", "items"):
            if isinstance(obj.get(k), list):
                items += obj[k]
    elif isinstance(obj, list):
        items = obj
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        tt = it.get("imdb_id") or it.get("imdbid") or it.get("imdbId") or it.get("imdb") or (it.get("ids") or {}).get("imdb") or (it.get("ids") or {}).get("imdbid")
        if not (isinstance(tt, str) and re.fullmatch(r"tt\d{6,10}", tt.strip())):
            continue
        kind = _kind(it.get("mediatype") or it.get("media_type") or it.get("type"))
        tmdb = it.get("tmdb_id") or it.get("tmdbid") or (it.get("ids") or {}).get("tmdb") or (it.get("ids") or {}).get("tmdbid")
        if tmdb is None and kind and isinstance(it.get("id"), int):
            tmdb = it["id"]           # MDBList's own "id" is the TMDB id for list items
        rv, rsrc = _rating(it)
        g = it.get("genres") if it.get("genres") is not None else it.get("genre")
        g = g.split(",") if isinstance(g, str) else g if isinstance(g, list) else []
        anim = any(str(x.get("name") if isinstance(x, dict) else x).strip().lower() == "animation" for x in g)
        out[tt.strip()] = {k: v for k, v in {
            "title": (it.get("title") or it.get("name") or "")[:200] or None, "year": _year(it.get("release_year") or it.get("year")),
            "released": _date(it.get("released") or it.get("release_date") or it.get("releasedate")),
            "rating": rv, "rating_src": rsrc, "animation": True if anim else None,
            "type": kind, "tmdb": int(tmdb) if str(tmdb or "").isdigit() else None}.items() if v}
    return out


def meta_from_csv(text):
    """imdb id -> {title, year, type} from an IMDb list export (Title / Year / Title Type columns), when present."""
    try:
        rows = list(csv.reader(io.StringIO(str(text or ""))))
    except csv.Error:
        return {}
    if not rows:
        return {}
    head = [c.strip().lower() for c in rows[0]]
    col = lambda *names: next((head.index(n) for n in names if n in head), None)
    c_id, c_title, c_year, c_type = col("const", "imdb id", "imdbid", "imdb_id", "id"), col("title", "name"), col("year", "release year"), col("title type", "type")
    c_rate, c_date = col("imdb rating", "imdb_rating"), col("release date", "release_date")
    if c_id is None:
        return {}
    out = {}
    for r in rows[1:]:
        if len(r) <= c_id or not re.fullmatch(r"tt\d{6,10}", r[c_id].strip()):
            continue
        g = lambda c: r[c].strip() if c is not None and len(r) > c else None
        rv = _num(g(c_rate))
        m = {"title": (g(c_title) or "")[:200] or None, "year": _year(g(c_year)), "type": _kind(g(c_type)),
             "released": _date(g(c_date)), "rating": rv, "rating_src": "IMDb" if rv else None}
        out[r[c_id].strip()] = {k: v for k, v in m.items() if v}
    return out


HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
_TITLE_LINK = re.compile(r"/title/(tt\d{6,10})")


def _get(cfg, url, accept):
    try:
        # short connect timeout: if imdb.com is unreachable or tarpitting us, fail in seconds rather than a minute
        return requests.get(url, headers={**HEADERS, "Accept": accept}, timeout=(10, min(30, cfg.http_timeout)), allow_redirects=True)
    except requests.RequestException as e:
        raise ApiError(f"couldn't reach imdb.com ({e.__class__.__name__}: {str(e)[:120]})")


def _why(r):
    t = (r.text or "")[:3000].lower()
    if r.status_code in (202, 403, 405, 429) or "captcha" in t or "awswaf" in t or "robot" in t:
        return f"IMDb blocked the request (HTTP {r.status_code}, probably its bot protection)"
    return f"HTTP {r.status_code}"


def fetch_imdb(cfg, lid):
    """-> (ids, note). Tries the list's CSV export; if IMDb refuses, falls back to reading the list's web page
    (which may only carry the first part of a long list)."""
    base = cfg.imdb_base
    problems = []
    r = _get(cfg, f"{base}/list/{lid}/export", "text/csv,*/*")
    if r.status_code == 404:
        raise ApiError(f"list {lid} not found on IMDb (is it public?)")
    if r.status_code == 200:
        ids = parse_csv(r.text)
        if ids:
            return ids, None, meta_from_csv(r.text)
        problems.append("the export had no titles")
    else:
        problems.append(f"export: {_why(r)}")
    r = _get(cfg, f"{base}/list/{lid}/", "text/html,*/*")
    if r.status_code == 200:
        ids = list(dict.fromkeys(_TITLE_LINK.findall(r.text)))[:MAX_IDS]
        if ids:
            return ids, f"read from the list's web page because IMDb wouldn't give the CSV ({problems[0]}); long lists may be cut short - upload the CSV for the full list", {}
        problems.append("list page: no titles found (private list, or IMDb changed its page)")
    else:
        problems.append(f"list page: {_why(r)}")
    raise ApiError(f"IMDb list {lid}: " + "; ".join(problems) + ". Export the list from IMDb and upload the CSV instead")


def fetch_mdblist(cfg, ref):
    """-> (ids, note). Public lists are read from the list's /json page; if that isn't allowed and an API key is set
    (MDBLIST_API_KEY), the API is tried."""
    problems = []
    r = _get(cfg, f"{cfg.mdblist_base}/lists/{ref}/json", "application/json,*/*")
    if r.status_code == 200:
        try:
            body = r.json()
            ids = ids_from_json(body)
        except ValueError:
            body, ids = None, []
        if ids:
            return ids, None, meta_from_json(body)
        problems.append("the list page had no IMDb ids (an empty list, or not a movie/show list)")
    elif r.status_code == 404:
        problems.append("list not found (check the link; the list must be public)")
    else:
        problems.append(f"list page: {_why(r)}")
    if cfg.mdblist_key:
        r = _get(cfg, f"{cfg.mdblist_api}/lists/{ref}/items?apikey={cfg.mdblist_key}", "application/json,*/*")
        if r.status_code == 200:
            try:
                body = r.json()
                ids = ids_from_json(body)
            except ValueError:
                body, ids = None, []
            if ids:
                return ids, None, meta_from_json(body)
            problems.append("API: no IMDb ids in the answer")
        else:
            problems.append(f"API: HTTP {r.status_code}")
    else:
        problems.append("no MDBLIST_API_KEY set for the API fallback")
    raise ApiError(f"MDBList {ref}: " + "; ".join(problems))


_locks = {}
_locks_guard = threading.Lock()
FRESH_SYNC = 120        # a list downloaded in the last 2 minutes isn't downloaded again by a sync
FRESH_PREVIEW = 900     # previews (which re-run as you type) reuse a copy up to 15 minutes old


def _lock_for(lid):
    with _locks_guard:
        return _locks.setdefault(lid, threading.Lock())


def resolve(cfg, source, fetch=True, fresh=FRESH_SYNC):
    """(ids, note). `source` is a row's saved source. imdb: downloaded (the copy is reused while it's younger than
    `fresh` seconds, and used as a fallback if IMDb fails); csv: the uploaded ids. fetch=False never touches the network."""
    if not source:
        return None, None
    if source.get("type") == "csv":
        return list(source.get("ids") or []), None
    mdb = source.get("type") == "mdblist"
    lid = ("mdb:" + source.get("list")) if mdb else source.get("list")
    if not fetch:
        hit = store.read_json(cfg, CACHE, {}).get(lid)
        return (hit["ids"], None) if hit else (None, "list not downloaded yet")
    with _lock_for(lid):                      # two requests for the same list wait for one download
        cache = store.read_json(cfg, CACHE, {})
        hit = cache.get(lid)
        if hit and time.time() - hit["t"] < fresh:
            return hit["ids"], None
        try:
            ids, note, meta = fetch_mdblist(cfg, source["list"]) if mdb else fetch_imdb(cfg, lid)
        except ApiError as e:
            if hit:
                return hit["ids"], f"{e} - using the copy from {time.strftime('%Y-%m-%d %H:%M', time.localtime(hit['t']))}"
            raise
        cache = store.read_json(cfg, CACHE, {})
        cache[lid] = {"ids": ids, "t": int(time.time()), "meta": meta}
        store.write_json(cfg, CACHE, cache)
        return ids, note


MAX_SOURCES = 20


def row_sources(row):
    """A row's list sources (rows saved before multi-list support have a single 'source')."""
    if row.get("sources"):
        return row["sources"]
    return [row["source"]] if row.get("source") else []


def resolve_all(cfg, sources, fetch=True, fresh=FRESH_SYNC):
    """Union of every source's ids (each list in its own order, lists one after another). Lists download in parallel.
    -> (ids or None, [notes])."""
    if not sources:
        return None, []
    with ThreadPoolExecutor(max_workers=min(6, len(sources))) as ex:
        results = list(ex.map(lambda x: resolve(cfg, x, fetch=fetch, fresh=fresh), sources))
    ids, notes = [], []
    for got, note in results:
        if note:
            notes.append(note)
        ids += got or []
    return list(dict.fromkeys(ids)), notes


def validate_sources(d):
    """Accepts `sources` (a list) or the older single `source`. -> (clean list, error)."""
    raw = d.get("sources")
    if raw is None:
        raw = [d["source"]] if d.get("source") else []
    if not isinstance(raw, list):
        return None, "bad list sources"
    if len(raw) > MAX_SOURCES:
        return None, f"at most {MAX_SOURCES} lists per row"
    out = []
    for r in raw:
        if isinstance(r, dict) and r.get("type") == "csv" and isinstance(r.get("csvs"), list):
            for t in r["csvs"][:MAX_SOURCES]:
                c, err = validate_source({"type": "csv", "csv": t})
                if err:
                    return None, err
                out.append(c)
            continue
        c, err = validate_source(r)
        if err:
            return None, err
        if c not in out:
            out.append(c)
    return out, None


def validate_source(d):
    """-> (clean source or None, error)."""
    if not d:
        return None, None
    if not isinstance(d, dict):
        return None, "bad list source"
    t = d.get("type")
    if t == "imdb":
        lid = list_id(d.get("list"))
        if not lid:
            return None, "that doesn't look like an IMDb list link (it should contain ls and digits, e.g. imdb.com/list/ls123456789/)"
        return {"type": "imdb", "list": lid}, None
    if t == "mdblist":
        ref = mdblist_ref(d.get("list"))
        if not ref:
            return None, "that doesn't look like an MDBList link (e.g. https://mdblist.com/lists/username/list-name)"
        return {"type": "mdblist", "list": ref}, None
    if t == "csv":
        if isinstance(d.get("ids"), list):
            ids = [x for x in d["ids"] if isinstance(x, str) and re.fullmatch(r"tt\d{6,10}", x)][:MAX_IDS]
        else:
            ids = parse_csv(d.get("csv"))
        if not ids:
            return None, "no IMDb ids (tt1234567) found in that file"
        meta = d.get("meta") if isinstance(d.get("meta"), dict) else meta_from_csv(d.get("csv"))
        meta = {k: v for k, v in meta.items() if k in set(ids) and isinstance(v, dict)}
        return {"type": "csv", "ids": ids, **({"meta": meta} if meta else {})}, None
    return None, "unknown list source"


def meta_for(cfg, sources):
    """imdb id -> {title, year, type, tmdb} for everything the row's lists told us (uploaded files and cached downloads)."""
    cache = store.read_json(cfg, CACHE, {})
    out = {}
    for src in sources or []:
        if src.get("type") == "csv":
            out.update(src.get("meta") or {})
        else:
            key = ("mdb:" + src["list"]) if src.get("type") == "mdblist" else src.get("list")
            out.update((cache.get(key) or {}).get("meta") or {})
    return out


def describe_all(sources):
    return " + ".join(describe(x) for x in sources)


def describe(source):
    if not source:
        return ""
    if source.get("type") == "csv":
        return f"uploaded list ({len(source.get('ids') or [])} titles)"
    if source.get("type") == "mdblist":
        return f"MDBList {source.get('list')}"
    return f"IMDb list {source.get('list')}"
