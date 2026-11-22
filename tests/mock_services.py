"""One Flask app that imitates Plex, Tautulli, Radarr, Sonarr and Seerr under path prefixes.
Used only for testing. Records every mutating call at /__calls."""
import sys
import time

from flask import Flask, Response, jsonify, request

app = Flask(__name__)
NOW = int(time.time())
D = 86400
CALLS = []
GB = 1024 ** 3

PLEX_TOKEN, RADARR_KEY, SONARR_KEY, SEERR_KEY, TAUTULLI_KEY = "plextoken", "radarrkey", "sonarrkey", "seerrkey", "tautullikey"


def rec(service, method, what):
    CALLS.append({"service": service, "method": method, "what": what, "query": dict(request.args)})


# ------------------------------------------------------------------ data
def guid(*pairs):
    return [{"id": f"{k}://{v}"} for k, v in pairs]


PLEX = {
    "1": [  # Movies
        {"ratingKey": "101", "type": "movie", "title": "Old Unwatched Movie", "year": 2019, "addedAt": NOW - 1100 * D,
         "Guid": guid(("imdb", "tt0000101"), ("tmdb", "1001")), "Media": [{"Part": [{"size": 8 * GB}]}],
         "Collection": [{"tag": "Test Saga"}, {"tag": "Second Collection"}, {"tag": "Third Collection"}]},
        {"ratingKey": "102", "type": "movie", "title": "Popular Recent Movie", "year": 2023, "addedAt": NOW - 200 * D,
         "Guid": guid(("imdb", "tt0000102"), ("tmdb", "1002")), "Media": [{"Part": [{"size": 5 * GB}]}],
         "Collection": [{"tag": "Test Saga"}]},
        {"ratingKey": "103", "type": "movie", "title": "Half Watched <b>Movie</b>", "year": 2018, "addedAt": NOW - 900 * D,
         "Guid": guid(("imdb", "tt0000103"), ("tmdb", "1003")), "Media": [{"Part": [{"size": 12 * GB}]}]},
        {"ratingKey": "104", "type": "movie", "title": "Plex Only Movie", "year": 2015, "addedAt": NOW - 2000 * D,
         "Guid": guid(("imdb", "tt0000104"), ("tmdb", "1004")), "Media": [{"Part": [{"size": 3 * GB}]}],
         "Rating": [{"image": "imdb://image.rating", "value": 4.4, "type": "audience"}]},
    ],
    "2": [  # TV
        {"ratingKey": "201", "type": "show", "title": "Cancelled Show", "year": 2016, "addedAt": NOW - 1500 * D, "leafCount": 20,
         "viewedLeafCount": 0, "Guid": guid(("imdb", "tt0000201"), ("tvdb", "7001"), ("tmdb", "2001"))},
        {"ratingKey": "202", "type": "show", "title": "Current Show", "year": 2024, "addedAt": NOW - 100 * D, "leafCount": 10,
         "viewedLeafCount": 8, "Guid": guid(("imdb", "tt0000202"), ("tvdb", "7002"), ("tmdb", "2002"))},
    ],
}
CREW = {
    "101": {"studio": "Marvel Studios", "dir": ["Zoë Director"], "cast": ["Nobody Famous", "Tom Hanks", "Third Actor"]},
    "102": {"studio": "Pixar", "dir": ["Pete Docter"], "cast": ["Tom Hanks"]},
    "103": {"studio": "Indie Films", "dir": ["Jane Director"], "cast": ["Actor One"]},
}
import datetime as _dt
_T = _dt.date.today()
_OTHER = 1 if _T.day != 1 else 2
RELEASED = {   # theatrical dates relative to *today* so the "released on this day" tests work on any date
    "101": f"{_T.year - 15}-{_T.month:02d}-{_T.day:02d}",      # same day, 15 years ago   (IMDb 5.2)
    "102": f"{_T.year - 12}-{_T.month:02d}-{_T.day:02d}",      # same day, 12 years ago   (IMDb 8.1)
    "103": f"{_T.year - 2}-{_T.month:02d}-{_T.day:02d}",       # same day, only 2 years ago (IMDb 6.4)
    "104": f"{_T.year - 20}-{_T.month:02d}-{_OTHER:02d}",      # same month, other day, 20 years ago (IMDb 4.4)
}
FACETS = {"101": (["Drama", "Western"], ["Classic"]), "102": (["Comedy", "Animation"], []), "103": (["Drama"], []), "104": (["Horror"], ["Classic"])}
SECTIONS = [{"key": "1", "type": "movie", "title": "Movies"}, {"key": "2", "type": "show", "title": "TV Shows"}]

RADARR = [
    {"id": 11, "title": "Old Unwatched Movie", "tmdbId": 1001, "imdbId": "tt0000101", "sizeOnDisk": 8 * GB,
     "added": "2022-01-05T10:00:00Z", "ratings": {"imdb": {"value": 5.2, "votes": 100}}},
    {"id": 12, "title": "Popular Recent Movie", "tmdbId": 1002, "imdbId": "tt0000102", "sizeOnDisk": 5 * GB,
     "added": "2024-04-01T10:00:00Z", "ratings": {"imdb": {"value": 8.1, "votes": 5000}}},
    {"id": 13, "title": "Half Watched Movie", "tmdbId": 1003, "imdbId": "tt0000103", "sizeOnDisk": 12 * GB,
     "added": "2023-05-01T10:00:00Z", "ratings": {"imdb": {"value": 6.4, "votes": 800}},
     "collection": {"title": "Radarr Only Collection", "tmdbId": 77}},
]
SONARR = [
    {"id": 21, "title": "Cancelled Show", "tvdbId": 7001, "tmdbId": 2001, "imdbId": "tt0000201",
     "statistics": {"sizeOnDisk": 40 * GB, "episodeFileCount": 20}, "added": "2021-01-01T00:00:00Z", "ratings": {"value": 6.9, "votes": 50}},
    {"id": 22, "title": "Current Show", "tvdbId": 7002, "tmdbId": 2002, "imdbId": "tt0000202",
     "statistics": {"sizeOnDisk": 25 * GB, "episodeFileCount": 10}, "added": "2024-07-01T00:00:00Z", "ratings": {"value": 8.5, "votes": 900}},
]


def user(name):
    return {"id": hash(name) % 100, "displayName": name}


SEERR_REQ = [
    {"id": 1, "type": "movie", "requestedBy": user("alice"), "media": {"id": 501, "mediaType": "movie", "tmdbId": 1001}},
    {"id": 2, "type": "movie", "requestedBy": user("bob"), "media": {"id": 502, "mediaType": "movie", "tmdbId": 1002}},
    {"id": 3, "type": "tv", "requestedBy": user("alice"), "media": {"id": 503, "mediaType": "tv", "tvdbId": 7001, "tmdbId": 2001}},
    {"id": 4, "type": "tv", "requestedBy": user("carol"), "media": {"id": 503, "mediaType": "tv", "tvdbId": 7001, "tmdbId": 2001}},
]
SEERR_MEDIA = [{"id": 501, "mediaType": "movie", "tmdbId": 1001}, {"id": 502, "mediaType": "movie", "tmdbId": 1002},
               {"id": 503, "mediaType": "tv", "tvdbId": 7001, "tmdbId": 2001},
               {"id": 509, "mediaType": "movie", "tmdbId": 1003}]  # 1003 has a media record but no request


def hist():
    rows = []
    rid = 0

    def add(media_type, rk, parent, ts, pct, user_, watched=None):
        nonlocal rid
        rid += 1
        rows.append({"row_id": rid, "date": ts, "started": ts, "media_type": media_type, "rating_key": rk,
                     "grandparent_rating_key": parent, "percent_complete": pct, "watched_status": watched if watched is not None else (1 if pct >= 85 else 0),
                     "user": user_, "friendly_name": user_, "title": f"Item {rk}", "full_title": f"Item {rk}",
                     "grandparent_title": "", "duration": 3600, "player": "Chrome", "platform": "Chrome", "product": "Plex Web", "transcode_decision": "direct play",
                     "stopped": ts + 3600, "paused_counter": 120, "ip_address": "192.168.1.170" if rid % 2 else "203.0.113.9", "location": "lan" if rid % 2 else "wan",
                     "year": 2023, "media_index": 3 if media_type == "episode" else None, "parent_media_index": 1 if media_type == "episode" else None})
    # Popular Recent Movie (102): 2 plays within year, 1 older
    add("movie", 102, "", NOW - 20 * D, 100, "bob")
    add("movie", 102, "", NOW - 90 * D, 100, "alice")
    add("movie", 102, "", NOW - 500 * D, 100, "bob")
    # Half Watched Movie (103): one play 400 days ago, 45%
    add("movie", 103, "", NOW - 400 * D, 45, "carol")
    # Cancelled Show (201): 5 distinct eps ~2 years ago, one rewatched
    for e in range(5):
        add("episode", 2010 + e, 201, NOW - (760 + e) * D, 98, "alice")
    add("episode", 2010, 201, NOW - 740 * D, 90, "alice")
    # Current Show (202): 8 distinct eps in the last month
    for e in range(8):
        add("episode", 2020 + e, 202, NOW - (3 + e) * D, 96, "bob")
    # something not in Plex library (should be ignored)
    add("episode", 9999, 8888, NOW - 5 * D, 100, "bob")
    return rows


HIST = hist()


# ------------------------------------------------------------------ helpers
def need(header, key):
    return request.headers.get(header) == key


# ------------------------------------------------------------------ Plex
@app.get("/plex/library/sections")
def plex_sections():
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    return jsonify(MediaContainer={"size": len(SECTIONS), "Directory": SECTIONS})


@app.get("/plex/library/sections/<sid>/all")
def plex_all(sid):
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    items = PLEX.get(sid, [])
    start = int(request.args.get("X-Plex-Container-Start", 0))
    size = int(request.args.get("X-Plex-Container-Size", 100))
    page = [{**i, "updatedAt": 1700000000, **({"originallyAvailableAt": RELEASED[i["ratingKey"]]} if i["ratingKey"] in RELEASED else {}), **({"Genre": [{"tag": g} for g in FACETS[i["ratingKey"]][0]], "Label": [{"tag": g} for g in FACETS[i["ratingKey"]][1]]} if i["ratingKey"] in FACETS else {})} for i in items[start:start + size]]
    return jsonify(MediaContainer={"size": len(page), "totalSize": len(items), "Metadata": page})


@app.get("/plex/library/sections/<sid>/refresh")
def plex_refresh(sid):
    rec("plex", "GET", f"refresh/{sid}")
    return "", 200


@app.put("/plex/library/sections/<sid>/emptyTrash")
def plex_trash(sid):
    rec("plex", "PUT", f"emptyTrash/{sid}")
    return "", 200


@app.get("/plex/activities")
def plex_act():
    return jsonify(MediaContainer={"size": 0})


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


@app.get("/plex/photo/:/transcode")
def plex_photo():
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    CALLS.append({"service": "plex", "method": "GET", "what": "photo", "query": dict(request.args)})
    return app.response_class(PNG, mimetype="image/png")


@app.get("/plex/identity")
def plex_identity():
    return jsonify(MediaContainer={"machineIdentifier": "MOCKMACHINE", "version": "1.mock"})


@app.get("/plex/library/metadata/<rk>")
def plex_meta(rk):
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    CALLS.append({"service": "plex", "method": "GET", "what": f"info/{rk}", "query": {}})
    return jsonify(MediaContainer={"Metadata": [{
        "ratingKey": rk, "type": "show" if rk in ("201", "202") else "movie", "title": f"Title {rk}", "year": 2020,
        "summary": f"A <b>bold</b> summary for item {rk}. " + "Lorem ipsum dolor sit amet. " * 8,
        "tagline": "The tagline", "contentRating": "PG-13", "duration": 7260000,
        "studio": CREW.get(rk, {}).get("studio", "Mock Studios"),
        "Genre": [{"tag": "Drama"}, {"tag": "Thriller"}],
        "Director": [{"tag": n} for n in CREW.get(rk, {}).get("dir", ["Jane Director"])],
        "Role": [{"tag": n} for n in CREW.get(rk, {}).get("cast", ["Actor One", "Actor Two"])], "childCount": 3, "leafCount": 20}]})


@app.delete("/plex/library/metadata/<rk>")
def plex_delete(rk):
    rec("plex", "DELETE", f"metadata/{rk}")
    return "", 200


# ------------------------------------------------------------------ Tautulli
@app.get("/tautulli/api/v2")
def tautulli():
    if request.args.get("apikey") != TAUTULLI_KEY:
        return jsonify(response={"result": "error", "message": "Invalid apikey", "data": {}}), 401
    if request.args.get("cmd") == "get_tautulli_info":
        return jsonify(response={"result": "success", "message": None, "data": {"tautulli_version": "v2.mock"}})
    cmd = request.args.get("cmd")
    if cmd == "get_activity":
        return jsonify(response={"result": "success", "message": None, "data": {"stream_count": 2, "sessions": [
            {"session_key": "11", "friendly_name": "bob", "grandparent_title": "Current Show", "full_title": "Current Show - Pilot", "title": "Pilot",
             "media_type": "episode", "year": "2024", "state": "playing", "progress_percent": "42", "player": "Living Room TV",
             "platform": "Roku", "product": "Plex for Roku", "transcode_decision": "transcode", "quality_profile": "Original", "bandwidth": "8200",
             "stream_bitrate": "8000", "duration": "2700000", "view_offset": "1134000", "location": "wan", "ip_address": "203.0.113.7",
             "stream_container_decision": "transcode", "stream_container": "mkv", "stream_video_decision": "transcode",
             "stream_video_codec": "h264", "stream_video_full_resolution": "1080p", "stream_audio_decision": "direct play",
             "stream_audio_codec": "eac3", "stream_audio_channel_layout": "5.1(side)", "audio_language": "English",
             "stream_subtitle_decision": "burn", "subtitle_language": "English", "subtitle_codec": "srt",
             "grandparent_thumb": "/library/metadata/202/thumb/1", "user_thumb": "", "secure": "1"},
            {"session_key": "12", "friendly_name": "alice", "title": "Popular Recent Movie", "full_title": "Popular Recent Movie", "grandparent_title": "",
             "media_type": "movie", "year": "2023", "state": "paused", "progress_percent": "77", "player": "Chrome",
             "platform": "Chrome", "product": "Plex Web", "transcode_decision": "direct play", "quality_profile": "1080p", "bandwidth": "4000",
             "duration": "6000000", "view_offset": "4620000", "location": "lan", "ip_address": "192.168.1.170", "stream_video_decision": "direct play",
             "stream_container_decision": "direct play", "stream_container": "mp4", "stream_video_codec": "h264", "stream_video_full_resolution": "1080p",
             "stream_audio_decision": "direct play", "stream_audio_codec": "aac", "stream_audio_channel_layout": "stereo",
             "stream_subtitle_decision": "", "thumb": "/library/metadata/102/thumb/1", "user_thumb": "", "user_id": "7"}],
            "stream_count": 2, "stream_count_direct_play": 1, "stream_count_transcode": 1, "total_bandwidth": 12200, "lan_bandwidth": 4000}})
    if cmd == "pms_image_proxy":
        import base64
        return Response(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="), mimetype="image/png")
    if cmd == "terminate_session":
        rec("tautulli", "TERMINATE", f"{request.args.get('session_key')}|{request.args.get('message')}")
        return jsonify(response={"result": "success", "message": None, "data": {}})
    if cmd == "get_users":
        return jsonify(response={"result": "success", "message": None, "data": [
            {"friendly_name": "alice", "user_id": 7, "user_thumb": "https://plex.tv/users/abc/avatar?c=1"}, {"friendly_name": "bob"}, {"friendly_name": "carol"}]})
    rows = HIST
    u, mt, q = request.args.get("user"), request.args.get("media_type"), (request.args.get("search") or "").lower()
    if u:
        rows = [r for r in rows if r["friendly_name"] == u]
    if mt:
        rows = [r for r in rows if r["media_type"] == mt]
    if q:
        rows = [r for r in rows if q in (r.get("full_title") or r.get("title") or "").lower()]
    start, length = int(request.args.get("start", 0)), int(request.args.get("length", 25))
    page = rows[start:start + length]
    return jsonify(response={"result": "success", "message": None,
                             "data": {"recordsFiltered": len(rows), "recordsTotal": len(HIST), "data": page}})


# ------------------------------------------------------------------ Radarr / Sonarr
@app.get("/radarr/api/v3/system/status")
def radarr_status():
    return (jsonify(version="5.mock") if need("X-Api-Key", RADARR_KEY) else ("", 401))


@app.get("/sonarr/api/v3/system/status")
def sonarr_status():
    return (jsonify(version="4.mock") if need("X-Api-Key", SONARR_KEY) else ("", 401))


@app.get("/radarr/api/v3/movie")
def radarr_list():
    return (jsonify(RADARR) if need("X-Api-Key", RADARR_KEY) else ("", 401))


@app.delete("/radarr/api/v3/movie/<int:mid>")
def radarr_del(mid):
    if not need("X-Api-Key", RADARR_KEY):
        return "", 401
    rec("radarr", "DELETE", f"movie/{mid}")
    return "", 200


@app.get("/sonarr/api/v3/series")
def sonarr_list():
    return (jsonify(SONARR) if need("X-Api-Key", SONARR_KEY) else ("", 401))


@app.delete("/sonarr/api/v3/series/<int:sid>")
def sonarr_del(sid):
    if not need("X-Api-Key", SONARR_KEY):
        return "", 401
    rec("sonarr", "DELETE", f"series/{sid}")
    return "", 200


# ------------------------------------------------------------------ Seerr
def paged(rows):
    take, skip = int(request.args.get("take", 20)), int(request.args.get("skip", 0))
    return jsonify(pageInfo={"results": len(rows)}, results=rows[skip:skip + take])


@app.get("/seerr/api/v1/request")
def seerr_requests():
    return paged(SEERR_REQ) if need("X-Api-Key", SEERR_KEY) else ("", 403)


@app.get("/seerr/api/v1/media")
def seerr_media():
    return paged(SEERR_MEDIA) if need("X-Api-Key", SEERR_KEY) else ("", 403)


@app.get("/seerr/api/v1/search")
def seerr_search():
    if not need("X-Api-Key", SEERR_KEY):
        return "", 403
    q = request.args.get("query", "")
    pool = {"Half Watched <b>Movie</b>": ("movie", 1003, "2018-03-01"), "Plex Only Movie": ("movie", 1004, "2015-05-01"), "Cancelled Show": ("tv", 2001, "2016-01-01")}
    hit = pool.get(q)
    res = [{"id": 5, "mediaType": "movie", "title": q + " Returns", "releaseDate": "2020-01-01"}]
    if hit:
        res.append({"id": hit[1], "mediaType": hit[0], "title" if hit[0] == "movie" else "name": q, "releaseDate" if hit[0] == "movie" else "firstAirDate": hit[2]})
    return jsonify(results=res)


# titles that exist only in Seerr (not in Plex): tmdb id -> details
import base64 as _b64
_PNG = _b64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@app.get("/img/t/p/w92/<name>")
def tmdb_img(name):
    return app.response_class(_PNG, mimetype="image/png")


SEERR_TITLES = {
    7001: {"title": "Wanted Movie", "releaseDate": "2021-06-01", "voteAverage": 6.9, "posterPath": "/abcdef123.jpg"},
    7002: {"title": "Already Requested Movie", "releaseDate": "2020-01-01", "mediaInfo": {"status": 2, "requests": [{"id": 1}]}},
    7003: {"title": "Available Movie", "releaseDate": "2019-01-01", "mediaInfo": {"status": 5}},
    7004: {"name": "Wanted Show", "firstAirDate": "2022-03-15", "voteAverage": 8.2, "genres": [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}]},
}


import copy as _copy
_SEERR_TITLES0 = _copy.deepcopy(SEERR_TITLES)


@app.post("/test/reset_seerr")
def test_reset_seerr():
    SEERR_TITLES.clear()
    SEERR_TITLES.update(_copy.deepcopy(_SEERR_TITLES0))
    return "", 200


@app.get("/seerr/api/v1/<kind>/<int:tid>")
def seerr_details(kind, tid):
    if not need("X-Api-Key", SEERR_KEY) or kind not in ("movie", "tv"):
        return "", 403
    d = SEERR_TITLES.get(tid)
    if d:
        return jsonify(id=tid, externalIds={"imdbId": f"tt{tid:07d}"}, **d)
    return jsonify(id=tid, externalIds={"imdbId": f"tt{tid:07d}"})


@app.post("/seerr/api/v1/request")
def seerr_new_request():
    if not need("X-Api-Key", SEERR_KEY):
        return "", 403
    b = request.get_json(silent=True) or {}
    CALLS.append({"service": "seerr", "method": "POST", "what": "request", "query": b})
    if b.get("mediaId") in SEERR_TITLES:
        SEERR_TITLES[b["mediaId"]]["mediaInfo"] = {"status": 2, "requests": [{"id": 900}]}     # Seerr now knows it's requested
    return jsonify(id=900, status=2), 201


@app.delete("/seerr/api/v1/media/<int:mid>")
def seerr_del(mid):
    if not need("X-Api-Key", SEERR_KEY):
        return "", 403
    if mid == 502:  # simulate a Seerr-side failure for one item
        return "", 500
    rec("seerr", "DELETE", f"media/{mid}")
    return "", 204


@app.get("/__calls")
def calls():
    return jsonify(CALLS)


@app.post("/__reset")
def reset():
    CALLS.clear()
    return "", 204


# ---- IMDb list export (mock) ----------------------------------------------------
IMDB_HITS = {}


@app.get("/test/imdb_hits")
def test_imdb_hits():
    return jsonify(IMDB_HITS)


@app.get("/imdb/list/<lid>/export")
def imdb_export(lid):
    IMDB_HITS[lid] = IMDB_HITS.get(lid, 0) + 1
    if lid == "ls000000404":
        return "not found", 404
    if lid in ("ls000000403", "ls000000402"):
        return "<html>AWS WAF captcha</html>", 403
    if lid == "ls000000500":
        return "<html>nope</html>", 200
    # list order: 103, 999 (not in Plex), 101, 102
    rows = "\n".join(f"{n},{t},2024-01-01,,Title {t},," for n, t in enumerate(["tt0000103", "tt0000999", "tt0000101", "tt0000102"], 1))
    return "Position,Const,Created,Modified,Description,Title,URL\n" + rows + "\n", 200, {"Content-Type": "text/csv"}


@app.get("/imdb/list/<lid>/")
def imdb_page(lid):
    if lid == "ls000000402":
        return "<html>captcha</html>", 403
    if lid == "ls000000500":
        return "<html>a private list</html>"
    return ("<html><a href='/title/tt0000102/?ref_=x'>A</a><a href='/title/tt0000101/'>B</a><a href='/title/tt0000102/'>A again</a>"
            "<a href='/title/tt0000999/'>Not in Plex</a></html>")


@app.get("/mdblist/lists/<user>/<name>/json")
def mdblist_json(user, name):
    if name == "missing":
        return "not found", 404
    if name == "private":
        return "forbidden", 403
    if name == "wrapped":
        return jsonify(movies=[{"imdb_id": "tt0000101", "title": "x"}], shows=[{"imdb_id": "tt0000201"}])
    if name == "wanted":
        return jsonify([{"id": 7001, "title": "Wanted Movie", "release_year": 2021, "released": "2021-06-01", "imdbrating": 7.4, "mediatype": "movie", "imdb_id": "tt0007001"},
                        {"id": 7002, "title": "Already Requested Movie", "release_year": 2020, "released": "2020-01-01", "imdbrating": 6.1, "mediatype": "movie", "imdb_id": "tt0007002"},
                        {"id": 7003, "title": "Available Movie", "release_year": 2019, "mediatype": "movie", "imdb_id": "tt0007003"},
                        {"id": 7004, "title": "Wanted Show", "release_year": 2022, "mediatype": "show", "imdb_id": "tt0007004"},
                        {"id": 1, "title": "Odd Match", "release_year": 2000, "mediatype": "movie", "imdb_id": "tt0007005"},
                        {"title": "No Type", "imdb_id": "tt0007006"},
                        {"id": 1003, "title": "Half Watched <b>Movie</b>", "release_year": 2018, "mediatype": "movie", "imdb_id": "tt0000103"}])
    return jsonify([{"rank": 1, "title": "A", "imdb_id": "tt0000103", "mediatype": "movie"}, {"rank": 2, "title": "B", "imdb_id": "tt0000999"},
                    {"rank": 3, "title": "C", "imdb_id": "tt0000102"}, {"rank": 4, "title": "no id"}])


# ---- collections (mock) -----------------------------------------------------
COLL = {}   # id -> {"title","section","items":[rk]}
_cid = [9000]


@app.get("/plex/library/sections/<sid>/collections")
def plex_colls(sid):
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    return jsonify(MediaContainer={"Metadata": [{"ratingKey": str(k), "title": v["title"]} for k, v in COLL.items() if v["section"] == sid]})


@app.get("/plex/library/collections/<int:cid>/children")
def plex_coll_children(cid):
    return jsonify(MediaContainer={"Metadata": [{"ratingKey": k} for k in COLL[cid]["items"]]})


def _uri_keys():
    return request.args["uri"].rsplit("/", 1)[-1].split(",")


@app.post("/plex/library/collections")
def plex_coll_create():
    if not need("X-Plex-Token", PLEX_TOKEN):
        return "", 401
    _cid[0] += 1
    COLL[_cid[0]] = {"title": request.args["title"], "section": request.args["sectionId"], "items": _uri_keys(), "home": False}
    CALLS.append({"service": "plex", "method": "POST", "what": f"collection-create/{request.args['sectionId']}", "query": dict(request.args)})
    return jsonify(MediaContainer={"Metadata": [{"ratingKey": str(_cid[0])}]})


@app.put("/plex/library/collections/<int:cid>/items")
def plex_coll_add(cid):
    COLL[cid]["items"] += _uri_keys()
    CALLS.append({"service": "plex", "method": "PUT", "what": f"collection-add/{cid}", "query": dict(request.args)})
    return "", 200


@app.delete("/plex/library/collections/<int:cid>/items/<rk>")
def plex_coll_rm(cid, rk):
    COLL[cid]["items"].remove(rk)
    CALLS.append({"service": "plex", "method": "DELETE", "what": f"collection-rm/{cid}/{rk}", "query": {}})
    return "", 200


@app.delete("/plex/library/collections/<int:cid>")
def plex_coll_del(cid):
    COLL.pop(cid, None)
    CALLS.append({"service": "plex", "method": "DELETE", "what": f"collection-del/{cid}", "query": {}})
    return "", 200


@app.post("/plex/hubs/sections/<sid>/manage")
def plex_hub_manage(sid):
    cid = int(request.args["metadataItemId"])
    COLL[cid]["home"] = request.args.get("promotedToOwnHome") == "1"
    COLL[cid]["shared"] = request.args.get("promotedToSharedHome") == "1"
    CALLS.append({"service": "plex", "method": "POST", "what": f"hub/{sid}/{cid}", "query": dict(request.args)})
    return "", 200


HOME_ORDER = {}   # section id -> [collection ids, top first]


@app.put("/plex/hubs/sections/<sid>/manage/move")
def plex_hub_move(sid):
    cid = int(request.args["metadataItemId"])
    lst = HOME_ORDER.setdefault(sid, [])
    if cid in lst:
        lst.remove(cid)
    after = request.args.get("after")
    lst.insert(lst.index(int(after)) + 1 if after is not None and int(after) in lst else 0, cid)
    CALLS.append({"service": "plex", "method": "PUT", "what": f"hubmove/{sid}/{cid}", "query": dict(request.args)})
    return "", 200


@app.get("/test/home_order")
def test_home_order():
    return jsonify({sid: [COLL[c]["title"] for c in lst if c in COLL] for sid, lst in HOME_ORDER.items()})


@app.put("/plex/library/metadata/<int:cid>/prefs")
def plex_prefs(cid):
    CALLS.append({"service": "plex", "method": "PUT", "what": f"prefs/{cid}", "query": dict(request.args)})
    COLL[cid]["sort"] = request.args.get("collectionSort")
    return "", 200


@app.put("/plex/library/collections/<int:cid>/items/<rk>/move")
def plex_coll_move(cid, rk):
    items = COLL[cid]["items"]
    items.remove(rk)
    after = request.args.get("after")
    items.insert(0 if after is None else items.index(after) + 1, rk)
    CALLS.append({"service": "plex", "method": "PUT", "what": f"collection-move/{cid}/{rk}", "query": dict(request.args)})
    return "", 200


@app.get("/__coll")
def dump_coll():
    return jsonify({str(k): v for k, v in COLL.items()})


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 9100)
