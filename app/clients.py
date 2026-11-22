"""Thin API clients for Plex, Tautulli, Radarr, Sonarr and Seerr."""
import urllib.parse

import requests


class ApiError(RuntimeError):
    pass


class Http:
    name = "service"

    def __init__(self, base, headers=None, params=None, verify=True, timeout=60):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(headers or {})
        self.s.params = params or {}
        self.s.verify = verify
        self.timeout = timeout

    def req(self, method, path, **kw):
        # Errors deliberately omit the URL/query string so API keys never leak into logs or the UI.
        try:
            r = self.s.request(method, self.base + path, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise ApiError(f"{self.name}: {method} {path} failed ({type(e).__name__})") from None
        if r.status_code >= 400:
            hint = ""
            if r.status_code in (401, 403):
                hint = " - check the API key/token and permissions"
            raise ApiError(f"{self.name}: {method} {path} returned HTTP {r.status_code}{hint}")
        return r

    def get(self, path, **params):
        r = self.req("GET", path, params=params)
        try:
            return r.json()
        except ValueError:
            raise ApiError(f"{self.name}: GET {path} did not return JSON") from None


class Plex(Http):
    name = "Plex"

    def __init__(self, cfg):
        super().__init__(
            cfg.plex_url,
            headers={"X-Plex-Token": cfg.plex_token, "Accept": "application/json"},
            verify=cfg.verify_tls,
            timeout=cfg.http_timeout,
        )

    def sections(self):
        return self.get("/library/sections")["MediaContainer"].get("Directory", [])

    def items(self, section_id, page=200):
        start = 0
        while True:
            mc = self.get(
                f"/library/sections/{section_id}/all",
                includeGuids=1,
                **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": page},
            )["MediaContainer"]
            batch = mc.get("Metadata", []) or []
            for it in batch:
                yield it
            start += len(batch)
            total = mc.get("totalSize", mc.get("size", 0))
            if not batch or start >= total:
                break

    def machine_id(self):
        return self.get("/identity")["MediaContainer"].get("machineIdentifier")

    def metadata(self, rating_key):
        mc = self.get(f"/library/metadata/{int(rating_key)}")["MediaContainer"]
        md = mc.get("Metadata") or []
        if not md:
            raise ApiError("Plex: item not found")
        return md[0]

    def poster(self, rating_key, width=240, height=360):
        """Return (bytes, content_type) for a small poster thumbnail."""
        r = self.req("GET", "/photo/:/transcode", params={
            "url": f"/library/metadata/{int(rating_key)}/thumb", "width": width, "height": height,
            "minSize": 1, "upscale": 1})
        return r.content, r.headers.get("Content-Type", "")

    # ---- collections (used for the "row on the Plex home screen" feature) ----
    def collections(self, section_id):
        mc = self.get(f"/library/sections/{section_id}/collections")["MediaContainer"]
        return mc.get("Metadata", []) or []

    def collection_children(self, cid):
        mc = self.get(f"/library/collections/{int(cid)}/children")["MediaContainer"]
        return [str(m.get("ratingKey")) for m in (mc.get("Metadata", []) or [])]

    @staticmethod
    def _uri(machine_id, rating_keys):
        return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/" + ",".join(str(int(k)) for k in rating_keys)

    def create_collection(self, section_id, title, is_show, machine_id, rating_keys):
        r = self.req("POST", "/library/collections", params={
            "type": 2 if is_show else 1, "title": title, "smart": 0, "sectionId": section_id,
            "uri": self._uri(machine_id, rating_keys)})
        md = (r.json().get("MediaContainer", {}).get("Metadata") or [{}])[0]
        if not md.get("ratingKey"):
            raise ApiError("Plex: collection was not created")
        return str(md["ratingKey"])

    def collection_add(self, cid, machine_id, rating_keys):
        self.req("PUT", f"/library/collections/{int(cid)}/items", params={"uri": self._uri(machine_id, rating_keys)})

    def collection_remove(self, cid, rating_key):
        self.req("DELETE", f"/library/collections/{int(cid)}/items/{int(rating_key)}")

    def collection_sort_custom(self, cid):
        """Switch a collection to manual ordering (Plex: collectionSort 2 = custom) so we can dictate the order."""
        self.req("PUT", f"/library/metadata/{int(cid)}/prefs", params={"collectionSort": 2})

    def collection_move(self, cid, rating_key, after=None):
        params = {"after": int(after)} if after is not None else {}
        self.req("PUT", f"/library/collections/{int(cid)}/items/{int(rating_key)}/move", params=params)

    def delete_collection(self, cid):
        self.req("DELETE", f"/library/collections/{int(cid)}")

    def promote_collection(self, section_id, cid, on=True, shared=True, own=True, recommended=True):
        """Show (or stop showing) the collection as a row on Plex's Home and the library's Recommended tab.
        `shared` = also on the Home screens of the other users (Plex Home members and people you share with)."""
        v = lambda flag: 1 if (on and flag) else 0
        self.req("POST", f"/hubs/sections/{section_id}/manage", params={
            "metadataItemId": int(cid), "promotedToRecommended": v(recommended), "promotedToOwnHome": v(own),
            "promotedToSharedHome": v(shared)})

    def hub_move(self, section_id, cid, after=None):
        """Put the collection's row on Home directly below `after` (or first, when `after` is None)."""
        params = {"metadataItemId": int(cid)}
        if after is not None:
            params["after"] = int(after)
        self.req("PUT", f"/hubs/sections/{section_id}/manage/move", params=params)

    def delete_item(self, rating_key):
        self.req("DELETE", f"/library/metadata/{rating_key}")

    def refresh(self, section_id):
        self.req("GET", f"/library/sections/{section_id}/refresh")

    def empty_trash(self, section_id):
        self.req("PUT", f"/library/sections/{section_id}/emptyTrash")

    def scanning(self):
        acts = self.get("/activities")["MediaContainer"].get("Activity", []) or []
        return any(str(a.get("type", "")).startswith("library.update") for a in acts)


class Tautulli(Http):
    name = "Tautulli"

    def __init__(self, cfg):
        super().__init__(
            cfg.tautulli_url + "/api/v2",
            params={"apikey": cfg.tautulli_key},
            verify=cfg.verify_tls,
            timeout=cfg.http_timeout,
        )

    def history(self, page=1000):
        """Yield every history row (all users, all time)."""
        start = 0
        while True:
            resp = self.get("", cmd="get_history", start=start, length=page,
                            order_column="date", order_dir="desc", grouping=0)["response"]
            if resp.get("result") != "success":
                raise ApiError(f"Tautulli: get_history failed ({resp.get('message')})")
            data = resp["data"]
            rows = data.get("data", []) or []
            for r in rows:
                yield r
            start += len(rows)
            if not rows or start >= int(data.get("recordsFiltered", 0)):
                break

    def _cmd(self, cmd, **params):
        resp = self.get("", cmd=cmd, **params)["response"]
        if resp.get("result") != "success":
            raise ApiError(f"Tautulli: {cmd} failed ({resp.get('message')})")
        return resp.get("data") or {}

    def activity(self):
        """Streams happening right now, plus Tautulli's summary counts."""
        return self._cmd("get_activity")

    def terminate(self, session_key, message=""):
        """Stop a stream; the viewer's player shows `message` (Tautulli terminate_session)."""
        params = {"session_key": session_key}
        if message:
            params["message"] = message
        self._cmd("terminate_session", **params)

    def image(self, img, width=300, fallback="poster"):
        """Poster/avatar bytes via Tautulli's image proxy (so the API key never reaches the browser)."""
        r = self.req("GET", "", params={"cmd": "pms_image_proxy", "img": img, "width": width, "height": width if fallback == "user" else int(width * 1.5), "fallback": fallback})
        return r.content, r.headers.get("Content-Type", "")

    def recent(self, start=0, length=25, user="", media_type="", search=""):
        params = dict(start=start, length=length, order_column="date", order_dir="desc", grouping=0)
        if user:
            params["user"] = user
        if media_type:
            params["media_type"] = media_type
        if search:
            params["search"] = search
        data = self._cmd("get_history", **params)
        return data.get("data") or [], int(data.get("recordsFiltered") or 0)

    def users(self):
        d = self._cmd("get_users")
        return d if isinstance(d, list) else []


class Arr(Http):
    def __init__(self, base, key, cfg):
        super().__init__(base + "/api/v3", headers={"X-Api-Key": key}, verify=cfg.verify_tls, timeout=cfg.http_timeout)


class Radarr(Arr):
    name = "Radarr"

    def __init__(self, cfg):
        super().__init__(cfg.radarr_url, cfg.radarr_key, cfg)

    def movies(self):
        return self.get("/movie")

    def delete(self, movie_id, exclusion):
        self.req("DELETE", f"/movie/{movie_id}",
                 params={"deleteFiles": "true", "addImportExclusion": str(bool(exclusion)).lower()})


class Sonarr(Arr):
    name = "Sonarr"

    def __init__(self, cfg):
        super().__init__(cfg.sonarr_url, cfg.sonarr_key, cfg)

    def series(self):
        return self.get("/series")

    def delete(self, series_id, exclusion):
        self.req("DELETE", f"/series/{series_id}",
                 params={"deleteFiles": "true", "addImportListExclusion": str(bool(exclusion)).lower()})


def test_service(cfg, service):
    """Try a cheap authenticated call. Returns a short human message; raises ApiError on failure."""
    if service == "plex":
        n = len(Plex(cfg).sections())
        return f"Connected - {n} librar{'y' if n == 1 else 'ies'} found"
    if service == "tautulli":
        resp = Tautulli(cfg).get("", cmd="get_tautulli_info")["response"]
        if resp.get("result") != "success":
            raise ApiError(f"Tautulli: {resp.get('message') or 'request rejected'} - check the API key")
        return "Connected to Tautulli " + str((resp.get("data") or {}).get("tautulli_version", ""))
    if service in ("radarr", "sonarr"):
        c = Radarr(cfg) if service == "radarr" else Sonarr(cfg)
        return f"Connected to {c.name} " + str(c.get("/system/status").get("version", ""))
    if service == "seerr":
        Seerr(cfg).get("/request", take=1, skip=0)
        return "Connected to Seerr"
    raise ApiError("unknown service")


class Seerr(Http):
    name = "Seerr"

    def __init__(self, cfg):
        super().__init__(cfg.seerr_url + "/api/v1", headers={"X-Api-Key": cfg.seerr_key},
                         verify=cfg.verify_tls, timeout=cfg.http_timeout)

    def _paged(self, path, **params):
        skip, take = 0, 100
        while True:
            data = self.get(path, take=take, skip=skip, **params)
            rows = data.get("results", []) or []
            for r in rows:
                yield r
            skip += len(rows)
            if not rows or skip >= int(data.get("pageInfo", {}).get("results", 0)):
                break

    def requests(self):
        return self._paged("/request", filter="all", sort="added")

    def media(self):
        return self._paged("/media", filter="all")

    def search(self, query):
        # Seerr wants %20 for spaces (requests would send "+"), so build the query string ourselves
        r = self.req("GET", "/search?query=" + urllib.parse.quote(query, safe="") + "&page=1")
        try:
            return r.json().get("results") or []
        except ValueError:
            raise ApiError("Seerr: search did not return JSON") from None

    def external_ids(self, media_type, tmdb_id):
        """IMDb/TVDB ids for a TMDB title, from Seerr's cached TMDB data."""
        d = self.get(f"/{'movie' if media_type == 'movie' else 'tv'}/{int(tmdb_id)}")
        return d.get("externalIds") or {}

    def request_title(self, media_type, tmdb_id, user_id=None):
        """Create a new request (as the API key's user, or as `user_id`). Seerr then sends it to Radarr/Sonarr itself."""
        body = {"mediaType": media_type, "mediaId": int(tmdb_id)}
        if media_type == "tv":
            body["seasons"] = "all"
        if user_id is not None:
            body["userId"] = int(user_id)
        return self.req("POST", "/request", json=body).json()

    def delete_media(self, media_id):
        self.req("DELETE", f"/media/{media_id}")
