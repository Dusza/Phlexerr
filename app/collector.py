"""Builds the candidate report by joining Plex, Tautulli, Radarr, Sonarr and Seerr."""
import datetime
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import store
from .clients import Plex, Tautulli, Radarr, Sonarr, Seerr, ApiError


def _guids(item):
    out = {}
    for g in item.get("Guid", []) or []:
        gid = g.get("id", "")
        if "://" in gid:
            k, v = gid.split("://", 1)
            out[k] = v
    if not out:  # legacy agents: com.plexapp.agents.imdb://tt1234567?lang=en
        m = re.match(r"com\.plexapp\.agents\.(\w+)://([^?/]+)", item.get("guid", "") or "")
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _plex_imdb_rating(item):
    for r in item.get("Rating", []) or []:
        if str(r.get("image", "")).startswith("imdb://") and r.get("value") is not None:
            try:
                return float(r["value"])
            except (TypeError, ValueError):
                pass
    return None


def _iso_ts(s):
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.timestamp() if dt.year > 1990 else None
    except (TypeError, ValueError):
        return None


def _plex_size(item):
    total = 0
    for m in item.get("Media", []) or []:
        for p in m.get("Part", []) or []:
            total += int(p.get("size") or 0)
    return total


def _num(v):
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _load_people(cfg, plex_items, progress, warnings):
    """Cast / director / studio per item, for protection rules. Cached in people.json and only re-fetched when
    Plex says the item changed (updatedAt), so just the first refresh is slow."""
    cache = store.read_json(cfg, "people.json", {})
    people, todo = {}, []
    for _s, it in plex_items:
        rk, upd = str(it.get("ratingKey")), it.get("updatedAt")
        c = cache.get(rk)
        if c and upd is not None and c.get("u") == upd:
            people[rk] = c
        else:
            todo.append((rk, it))
    if todo:
        local = threading.local()

        def work(arg):
            rk, it = arg
            if getattr(local, "plex", None) is None:
                local.plex = Plex(cfg)
            try:
                m = local.plex.metadata(rk)
            except ApiError:
                return rk, it, None
            tags = lambda key, n: [t.get("tag") for t in (m.get(key) or []) if t.get("tag")][:n]
            return rk, it, {"u": it.get("updatedAt"), "actors": tags("Role", 40), "directors": tags("Director", 10),
                            "studio": m.get("studio") or it.get("studio")}

        failed = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for n, (rk, it, rec) in enumerate(ex.map(work, todo), 1):
                if n % 50 == 0 or n == len(todo):
                    progress(f"Reading cast & crew from Plex ({n}/{len(todo)})")
                if rec:
                    people[rk] = rec
                else:
                    failed += 1
                    people[rk] = cache.get(rk) or {"studio": it.get("studio")}
        if failed:
            warnings.append(f"Couldn't read cast/crew for {failed} item(s) - actor/director protection rules may miss them until the next refresh")
    store.write_json(cfg, "people.json", people)  # also drops entries for items no longer in Plex
    return people


def live_index(cfg):
    """Cheap current snapshot of what is in Plex right now (titles + external ids only). Used to tell whether a deleted
    title has come back, without waiting for a full report refresh."""
    plex = Plex(cfg)
    sections = [s for s in plex.sections() if s.get("type") in ("movie", "show")]
    if cfg.plex_libraries:
        sections = [s for s in sections if s.get("title", "").lower() in cfg.plex_libraries]
    out = []
    for s in sections:
        for it in plex.items(s["key"]):
            g = _guids(it)
            out.append({"type": "movie" if it.get("type") == "movie" else "show", "title": it.get("title"), "year": it.get("year"),
                        "imdb_id": g.get("imdb"), "tmdb_id": g.get("tmdb")})
    return out


def build_report(cfg, progress=lambda m: None):
    warnings = []
    svc = cfg.services()
    now = time.time()
    year_ago = now - cfg.history_days * 86400

    # ---- Plex -------------------------------------------------------------
    if not svc["plex"]:
        raise ApiError("Plex isn't configured yet - open Settings and enter the Plex URL and token")
    plex = Plex(cfg)
    progress("Reading Plex libraries")
    sections = [s for s in plex.sections() if s.get("type") in ("movie", "show")]
    if cfg.plex_libraries:
        sections = [s for s in sections if s.get("title", "").lower() in cfg.plex_libraries]
    plex_items = []
    for s in sections:
        progress(f"Reading Plex library: {s.get('title')}")
        for it in plex.items(s["key"]):
            plex_items.append((s, it))

    # Collection membership straight from Plex's collections (the item list doesn't always carry the tags)
    coll_of = {}
    for s in sections:
        try:
            for c in plex.collections(s["key"]):
                if not c.get("title") or not c.get("ratingKey"):
                    continue
                for rk in plex.collection_children(c["ratingKey"]):
                    coll_of.setdefault(str(rk), []).append(c["title"])
        except ApiError as e:
            warnings.append(f"Couldn't read Plex collections for {s.get('title')}: {e}")

    # ---- Radarr / Sonarr --------------------------------------------------
    radarr_tmdb, radarr_imdb, sonarr_tvdb, sonarr_tmdb, sonarr_imdb = {}, {}, {}, {}, {}
    if svc["radarr"]:
        progress("Reading Radarr")
        try:
            for m in Radarr(cfg).movies():
                if m.get("tmdbId"):
                    radarr_tmdb[str(m["tmdbId"])] = m
                if m.get("imdbId"):
                    radarr_imdb[m["imdbId"]] = m
        except ApiError as e:
            warnings.append(str(e))
    else:
        warnings.append("Radarr not configured - movie sizes/ratings come from Plex and movies cannot be removed from Radarr")
    if svc["sonarr"]:
        progress("Reading Sonarr")
        try:
            for sr in Sonarr(cfg).series():
                if sr.get("tvdbId"):
                    sonarr_tvdb[str(sr["tvdbId"])] = sr
                if sr.get("tmdbId"):
                    sonarr_tmdb[str(sr["tmdbId"])] = sr
                if sr.get("imdbId"):
                    sonarr_imdb[sr["imdbId"]] = sr
        except ApiError as e:
            warnings.append(str(e))
    else:
        warnings.append("Sonarr not configured - show sizes/ratings come from Plex and shows cannot be removed from Sonarr")

    # ---- Seerr ------------------------------------------------------------
    seerr_movie, seerr_tv_tvdb, seerr_tv_tmdb = {}, {}, {}

    def _slot(d, key):
        return d.setdefault(str(key), {"media_id": None, "users": set(), "uids": {}})

    if svc["seerr"]:
        progress("Reading Seerr requests")
        try:
            sc = Seerr(cfg)
            for r in sc.requests():
                media = r.get("media") or {}
                by = r.get("requestedBy") or {}
                name = by.get("displayName") or by.get("plexUsername") or by.get("username") or by.get("email") or "unknown"
                if r.get("type") == "movie" or media.get("mediaType") == "movie":
                    if media.get("tmdbId"):
                        slot = _slot(seerr_movie, media["tmdbId"])
                    else:
                        continue
                else:
                    slot = _slot(seerr_tv_tvdb, media["tvdbId"]) if media.get("tvdbId") else (
                        _slot(seerr_tv_tmdb, media["tmdbId"]) if media.get("tmdbId") else None)
                    if slot is None:
                        continue
                    if media.get("tmdbId"):
                        seerr_tv_tmdb[str(media["tmdbId"])] = slot
                slot["media_id"] = media.get("id") or slot["media_id"]
                slot["users"].add(name)
                if by.get("id") is not None:
                    slot["uids"][name] = by["id"]
            # media records that have no request (added outside Seerr) still need clearing on delete
            for m in sc.media():
                if m.get("mediaType") == "movie" and m.get("tmdbId"):
                    s = _slot(seerr_movie, m["tmdbId"])
                elif m.get("mediaType") == "tv":
                    s = seerr_tv_tvdb.get(str(m.get("tvdbId"))) or seerr_tv_tmdb.get(str(m.get("tmdbId")))
                    if s is None and m.get("tvdbId"):
                        s = _slot(seerr_tv_tvdb, m["tvdbId"])
                else:
                    continue
                if s is not None and not s["media_id"]:
                    s["media_id"] = m.get("id")
        except ApiError as e:
            warnings.append(str(e))
    else:
        warnings.append("Seerr not configured - 'requested by' will be empty")

    # ---- Tautulli ---------------------------------------------------------
    movie_hist, show_hist = {}, {}
    have_history = False
    if svc["tautulli"]:
        progress("Reading Tautulli history")
        try:
            n = 0
            for row in Tautulli(cfg).history():
                n += 1
                if n % 2000 == 0:
                    progress(f"Reading Tautulli history ({n} plays so far)")
                mtype = row.get("media_type")
                ts = row.get("date") or row.get("started") or 0
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    ts = 0
                try:
                    pct = float(row.get("percent_complete") or 0)
                except (TypeError, ValueError):
                    pct = 0
                if mtype == "movie":
                    key, bucket = str(row.get("rating_key")), movie_hist
                elif mtype == "episode":
                    key, bucket = str(row.get("grandparent_rating_key")), show_hist
                else:
                    continue
                h = bucket.setdefault(key, {"plays": 0, "plays_1y": 0, "last": 0, "last_user": None, "pct": 0.0, "eps": set()})
                h["plays"] += 1
                if ts >= year_ago:
                    h["plays_1y"] += 1
                if ts > h["last"]:
                    h["last"], h["last_user"] = ts, row.get("friendly_name") or row.get("user")
                h["pct"] = max(h["pct"], pct)
                if mtype == "episode" and (row.get("watched_status") == 1 or pct >= cfg.watched_percent):
                    h["eps"].add(str(row.get("rating_key")))
            have_history = True
        except ApiError as e:
            warnings.append(str(e) + " - falling back to Plex's own play data (single account only)")
    else:
        warnings.append("Tautulli not configured - falling back to Plex's own play data (single account only)")

    # ---- Cast & crew (for protection rules) --------------------------------
    people = _load_people(cfg, plex_items, progress, warnings)

    # ---- Join -------------------------------------------------------------
    progress("Joining data")
    items = []
    for s, it in plex_items:
        is_movie = it.get("type") == "movie"
        g = _guids(it)
        rk = str(it.get("ratingKey"))
        arr = None
        if is_movie:
            arr = radarr_tmdb.get(g.get("tmdb", "")) or radarr_imdb.get(g.get("imdb", ""))
        else:
            arr = sonarr_tvdb.get(g.get("tvdb", "")) or sonarr_tmdb.get(g.get("tmdb", "")) or sonarr_imdb.get(g.get("imdb", ""))

        # IMDb rating
        rating, rating_src = None, None
        plex_r = _plex_imdb_rating(it)
        if is_movie:
            r = _num(((arr or {}).get("ratings") or {}).get("imdb", {}).get("value"))
            if r is not None:
                rating, rating_src = r, "Radarr"
            elif plex_r is not None:
                rating, rating_src = plex_r, "Plex"
        else:
            if plex_r is not None:
                rating, rating_src = plex_r, "Plex"
            else:
                r = _num(((arr or {}).get("ratings") or {}).get("value"))
                if r is not None:
                    rating, rating_src = r, "Sonarr"

        # Size
        if arr:
            size = int(arr.get("sizeOnDisk") or 0) if is_movie else int(((arr.get("statistics") or {}).get("sizeOnDisk")) or 0)
        else:
            size = 0
        if not size:
            size = _plex_size(it)

        # Added
        added = it.get("addedAt") or (_iso_ts(arr.get("added")) if arr else None)

        # Viewing
        h = (movie_hist if is_movie else show_hist).get(rk)
        leaf = int(it.get("leafCount") or 0)
        if have_history:
            plays, plays_1y = (h["plays"], h["plays_1y"]) if h else (0, 0)
            last = h["last"] if h and h["last"] else None
            last_user = h["last_user"] if h else None
            if is_movie:
                pct = min(100.0, h["pct"]) if h else 0.0
            else:
                pct = min(100.0, 100.0 * len(h["eps"]) / leaf) if (h and leaf) else 0.0
        else:
            plays = int(it.get("viewCount") or 0)
            plays_1y = None  # unknown without history
            last = it.get("lastViewedAt")
            last_user = None
            if is_movie:
                pct = 100.0 if plays else 0.0
            else:
                pct = 100.0 * int(it.get("viewedLeafCount") or 0) / leaf if leaf else 0.0

        # Seerr
        if is_movie:
            sr = seerr_movie.get(g.get("tmdb", "")) or (seerr_movie.get(str(arr.get("tmdbId"))) if arr else None)
        else:
            sr = seerr_tv_tvdb.get(g.get("tvdb", "")) or seerr_tv_tmdb.get(g.get("tmdb", "")) or \
                (seerr_tv_tvdb.get(str(arr.get("tvdbId"))) if arr else None)

        # Collections: Plex collection tags, else Radarr's collection (e.g. "Alien Collection")
        colls = [c.get("tag") for c in (it.get("Collection") or []) if c.get("tag")]
        colls += [c for c in coll_of.get(str(rk), []) if c not in colls]
        if not colls and arr:
            rc = (arr.get("collection") or {}).get("title")
            if rc:
                colls = [rc]

        keep_id = next((f"{k}:{g[k]}" for k in ("imdb", "tmdb", "tvdb") if g.get(k)), f"plex:{rk}")
        items.append({
            "id": f"{'movie' if is_movie else 'show'}:{rk}",
            "keep_id": keep_id,
            "type": "movie" if is_movie else "show",
            "title": it.get("title"),
            "year": it.get("year"),
            "library": s.get("title"),
            "collections": colls,
            "genres": [g.get("tag") for g in (it.get("Genre") or []) if g.get("tag")],
            "labels": [g.get("tag") for g in (it.get("Label") or []) if g.get("tag")],
            "actors": people.get(rk, {}).get("actors", []),
            "directors": people.get(rk, {}).get("directors", []),
            "studio": people.get(rk, {}).get("studio") or it.get("studio"),
            "section_id": str(s.get("key")),
            "plex_key": rk,
            "imdb_id": g.get("imdb") or (arr or {}).get("imdbId"),
            "tmdb_id": g.get("tmdb") or (str((arr or {}).get("tmdbId")) if (arr or {}).get("tmdbId") else None),
            "requester_ids": sr["uids"] if sr else {},
            "rating": rating,
            "rating_src": rating_src,
            "added_ts": added,
            "released": ((it.get("originallyAvailableAt") or (arr or {}).get("inCinemas") or "")[:10] or None) if is_movie else None,
            "last_viewed_ts": last,
            "last_viewed_by": last_user,
            "pct": round(pct, 1),
            "requested_by": sorted(sr["users"]) if sr else [],
            "plays_1y": plays_1y,
            "plays_total": plays,
            "size": size,
            "episodes": leaf if not is_movie else None,
            "arr": ("radarr" if is_movie else "sonarr") if arr else None,
            "arr_id": arr.get("id") if arr else None,
            "seerr_media_id": sr["media_id"] if sr else None,
        })

    items.sort(key=lambda x: x["size"], reverse=True)
    progress("Done")
    return {
        "generated_at": int(now),
        "items": items,
        "warnings": warnings,
        "history_source": "tautulli" if have_history else "plex",
    }
