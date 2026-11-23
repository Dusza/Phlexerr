"""Flask app: auth, report API, delete API."""
import copy
import datetime
import hmac
import gzip
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta

from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import clients, collector, deleter, extlists, leaving, rows, rules, store, throwback
from .clients import ApiError, Plex, Tautulli
from .config import Config

cfg = Config()
cfg.apply(store.read_json(cfg, "settings.json", {}))  # values saved from the Settings page override environment variables
_problems = cfg.problems()
if _problems:
    raise SystemExit("Phlexerr cannot start:\n  - " + "\n  - ".join(_problems))

app = Flask(__name__, static_folder="static", static_url_path="/static")
if cfg.trust_proxy:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _secret():
    if cfg.secret_key:
        return cfg.secret_key
    stored = store.read_json(cfg, "secret.json", {}).get("key")
    if stored:
        return stored
    key = secrets.token_hex(32)
    store.write_json(cfg, "secret.json", {"key": key})
    return key


app.secret_key = _secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cfg.cookie_secure,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

# ---- in-memory state --------------------------------------------------------
DATA = {"report": store.read_json(cfg, "cache.json", None)}
STATUS = {"state": "idle", "message": "", "started": None, "finished": None, "error": None}
DATA_LOCK = threading.Lock()
_fails = {}  # ip -> [count, locked_until]


def _client_ip():
    if cfg.trust_proxy and request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"]
    return request.remote_addr or "?"


# ---- auth -------------------------------------------------------------------
LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Phlexerr - sign in</title>
<link rel="icon" href="/static/icons/favicon.svg" type="image/svg+xml"><link rel="icon" href="/static/icons/favicon-32.png" sizes="32x32" type="image/png"><link rel="alternate icon" href="/static/icons/favicon.ico"><link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png"><link rel="manifest" href="/static/manifest.webmanifest"><meta name="theme-color" content="#1f2326"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Phlexerr"><script src="/static/theme.js"></script><link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><form method="post" class="card" autocomplete="on">
<img class="logo big" src="/static/icons/logo.svg" alt="" width="56" height="56"><h1>Phlexerr</h1><p class="muted">Sign in to review and clean up your library.</p>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<input type="password" name="password" placeholder="Password" autofocus required autocomplete="current-password">
<button class="btn primary" type="submit">Sign in</button></form></body></html>"""


def authed():
    return cfg.auth_disabled or session.get("auth") is True


@app.before_request
def guard():
    p = request.path
    if p in ("/healthz", "/login") or p.startswith("/static/"):
        return None
    if not authed():
        if p.startswith("/api/"):
            return jsonify(error="not signed in"), 401
        return redirect("/login")
    if request.method in ("POST", "PUT", "DELETE") and p.startswith("/api/"):
        # CSRF defence: JSON body + custom header can't be sent cross-site without a CORS preflight
        if request.headers.get("X-Requested-With") not in ("phlexerr", "media-pruner") or not request.is_json:
            return jsonify(error="bad request"), 400
    return None


@app.after_request
def headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    if request.path.startswith("/api/") and not request.path.startswith("/api/poster/"):
        # /api/items revalidates with its ETag (a 304 costs almost nothing); everything else is never stored
        resp.headers["Cache-Control"] = "no-cache" if request.path == "/api/items" else "no-store"
    if (resp.status_code == 200 and not resp.direct_passthrough and "gzip" in request.headers.get("Accept-Encoding", "")
            and (resp.mimetype or "").startswith(("application/json", "text/", "application/javascript"))
            and not resp.headers.get("Content-Encoding")):
        data = resp.get_data()
        if len(data) > 1024:
            resp.set_data(gzip.compress(data, 5))
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
    return resp


@app.get("/favicon.ico")
def favicon():
    return redirect("/static/icons/favicon.ico")


@app.route("/login", methods=["GET", "POST"])
def login():
    if cfg.auth_disabled:
        return redirect("/")
    error = None
    if request.method == "POST":
        ip, now = _client_ip(), time.time()
        count, locked = _fails.get(ip, [0, 0])
        if locked > now:
            error = f"Too many attempts. Try again in {int(locked - now) + 1}s."
        elif hmac.compare_digest(request.form.get("password", "").encode(), cfg.app_password.encode()):
            _fails.pop(ip, None)
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect("/")
        else:
            count += 1
            _fails[ip] = [count, now + 60 if count >= 5 else 0]
            if count >= 5:
                _fails[ip][0] = 0
            error = "Wrong password."
            time.sleep(0.7)
    return render_template_string(LOGIN_HTML, error=error), (401 if error else 200)


@app.post("/api/logout")   # under /api/ so the CSRF guard (custom header + JSON) applies: a cross-site page can't sign you out
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- API --------------------------------------------------------------------
@app.get("/api/config")
def api_config():
    lv = _lv_conf()
    return jsonify(dry_run=cfg.dry_run, default_password=cfg.default_password, services=cfg.services(), auth=not cfg.auth_disabled, leaving=lv["enabled"], leaving_days=lv["days"],
                   max_batch=cfg.max_batch, history_days=cfg.history_days,
                   plex_link_base=cfg.plex_link_base or "https://app.plex.tv/desktop", refresh_hours=cfg.refresh_hours)


URL_RE = re.compile(r"^https?://[^\s]+$")


def _validate(values):
    """Return an error string, or None if the submitted settings look sane."""
    for k, v in values.items():
        if k not in Config.URL_FIELDS + Config.SECRET_FIELDS + ("plex_libraries", "refresh_hours") + throwback.FIELDS + leaving.FIELDS:
            return f"unknown setting: {k}"
        if not isinstance(v, str) or len(v) > 500:
            return f"invalid value for {k}"
        v = v.strip()
        if k == "refresh_hours" and not (v.isdigit() and 0 <= int(v) <= 168):
            return "Automatic refresh must be a whole number of hours between 0 (off) and 168"
        if k in Config.URL_FIELDS and v and not URL_RE.match(v):
            return "URLs must start with http:// or https://"
        if k in Config.SECRET_FIELDS and any(c.isspace() or ord(c) < 32 for c in v):
            return f"{k.split('_')[0].title()} key/token must not contain spaces or line breaks"
    return throwback.validate({k: v for k, v in values.items() if k in throwback.FIELDS}) or \
        leaving.validate({k: v for k, v in values.items() if k in leaving.FIELDS})


@app.get("/api/settings")
def api_settings_get():
    out = {k: getattr(cfg, k) for k in Config.URL_FIELDS}
    out["plex_libraries"] = cfg.plex_libraries_raw
    out["refresh_hours"] = str(cfg.refresh_hours)
    out.update({**throwback.DEFAULTS, **cfg.tb_raw})
    out.update({**leaving.DEFAULTS, **cfg.leaving_raw})
    for k in Config.SECRET_FIELDS:  # secrets are write-only: the browser only learns whether one is set
        out[k] = {"set": bool(getattr(cfg, k))}
    return jsonify(out)


@app.post("/api/settings")
def api_settings_save():
    values = (request.get_json(silent=True) or {}).get("values") or {}
    err = _validate(values)
    if err:
        return jsonify(error=err), 400
    stored = store.read_json(cfg, "settings.json", {})
    for k, v in values.items():
        if k in Config.SECRET_FIELDS and not v.strip():
            continue  # blank key = keep the saved one
        stored[k] = v.strip()
    if not store.write_json(cfg, "settings.json", stored, private=True):
        return jsonify(error="Couldn't save: the data folder isn't writable. On Unraid run: "
                             "chown 99:100 <your data folder> and restart the container."), 500
    before_tb, before_lv = _tb_conf(), _lv_conf()
    cfg.apply(stored)
    if any(k in throwback.FIELDS for k in values) and _tb_conf() != before_tb:
        threading.Thread(target=_tb_settings_changed, args=(before_tb,), daemon=True).start()
    if any(k in leaving.FIELDS for k in values) and _lv_conf() != before_lv:
        _lv["ver"] += 1
        threading.Thread(target=_lv_settings_changed, args=(before_lv,), daemon=True).start()
    return jsonify(ok=True, services=cfg.services())


@app.post("/api/settings/test")
def api_settings_test():
    body = request.get_json(silent=True) or {}
    service = body.get("service")
    values = {k: v for k, v in (body.get("values") or {}).items()
              if not (k in Config.SECRET_FIELDS and not str(v).strip())}  # blank key -> use the saved one
    err = _validate(values)
    if err:
        return jsonify(ok=False, message=err)
    trial = copy.copy(cfg)
    trial.apply(values)
    if service not in ("plex", "tautulli", "radarr", "sonarr", "seerr"):
        return jsonify(ok=False, message="unknown service")
    if not trial.services()[service]:
        return jsonify(ok=False, message="Enter both the URL and the key first")
    try:
        return jsonify(ok=True, message=clients.test_service(trial, service))
    except ApiError as e:
        return jsonify(ok=False, message=str(e))


@app.get("/api/status")
def api_status():
    return jsonify(STATUS)


def _need_tautulli():
    if not (cfg.tautulli_url and cfg.tautulli_key):
        return jsonify(error="Tautulli isn't configured - add its URL and API key in Settings"), 400
    return None


def _num(v, d=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


_uthumb = {"at": 0, "map": {}}


def _user_thumbs():
    """user id / name -> avatar URL from Tautulli's user list (get_activity doesn't always include it). Cached 10 min."""
    if time.time() - _uthumb["at"] < 600:
        return _uthumb["map"]
    m = {}
    try:
        for u in Tautulli(cfg).users():
            t = u.get("user_thumb") or ""
            if t:
                m[str(u.get("user_id"))] = t
                m[str(u.get("friendly_name"))] = t
    except ApiError:
        pass
    _uthumb.update(at=time.time(), map=m)
    return m


def _nocache(resp):
    resp.headers["Cache-Control"] = "no-store"     # live data: never let the browser reuse an old answer
    return resp


def _mbps(kbps):
    return round(_num(kbps) / 1000, 1)


def _decision(d, detail=""):
    d = {"direct play": "Direct Play", "ignore": "Direct Play", "copy": "Direct Stream", "transcode": "Transcode"}.get(str(d).lower(), str(d or "").title())
    return f"{d} ({detail})" if d and detail else d


@app.get("/api/activity")
def api_activity():
    """Streams playing right now (Tautulli get_activity), shaped for the Activity cards."""
    bad = _need_tautulli()
    if bad:
        return bad
    try:
        data = Tautulli(cfg).activity()
    except ApiError as e:
        return jsonify(error=str(e)), 502
    thumbs = _user_thumbs()
    out = []
    for x in data.get("sessions") or []:
        dur, off = _num(x.get("duration")), _num(x.get("view_offset"))
        remain = max(0, dur - off) / 1000
        v = " ".join(t for t in (str(x.get("stream_video_codec") or "").upper(), str(x.get("stream_video_full_resolution") or "")) if t)
        ch = x.get("stream_audio_channel_layout") or ""
        au = " - ".join(t for t in (str(x.get("audio_language") or ""), (str(x.get("stream_audio_codec") or "").upper() + (" " + str(ch).split("(")[0] if ch else "")).strip()) if t)
        sub = x.get("stream_subtitle_decision") or ""
        sublang = " - ".join(t for t in (str(x.get("subtitle_language") or ""), str(x.get("subtitle_codec") or "").upper()) if t)
        qual = x.get("quality_profile") or ""
        out.append(dict(
            key=str(x.get("session_key") or ""),
            user=x.get("friendly_name") or x.get("user") or "?",
            title=x.get("grandparent_title") or x.get("title") or x.get("full_title") or "",
            subtitle=(x.get("full_title") if x.get("grandparent_title") else "") or "",
            year=x.get("year"), type=x.get("media_type") or "", state=x.get("state") or "",
            progress=int(_num(x.get("progress_percent"))), elapsed=int(off / 1000), length=int(dur / 1000),
            ends_ts=int(time.time() + remain) if str(x.get("state")) == "playing" and dur else 0,
            product=x.get("product") or x.get("platform") or "", player=x.get("player") or "", platform=x.get("platform") or "",
            quality=(qual + (f" ({_mbps(x.get('stream_bitrate'))} Mbps)" if x.get("stream_bitrate") else "")).strip(),
            stream=_decision(x.get("transcode_decision") or x.get("stream_video_decision")),
            container=_decision(x.get("stream_container_decision"), str(x.get("stream_container") or "").upper()),
            video=_decision(x.get("stream_video_decision"), v),
            audio=_decision(x.get("stream_audio_decision"), au),
            subtitles=_decision(sub, sublang) if sub and sub != "none" else "",
            location=(("LAN" if str(x.get("location")) == "lan" else "WAN") + (": " + str(x.get("ip_address")) if x.get("ip_address") else "")) if x.get("location") else "",
            secure=bool(x.get("secure") in (1, "1", True)),
            bandwidth=_mbps(x.get("bandwidth")),
            poster=str(x.get("thumb") if x.get("media_type") == "movie" else (x.get("grandparent_thumb") or x.get("thumb")) or ""),
            avatar=str(x.get("user_thumb") or thumbs.get(str(x.get("user_id"))) or thumbs.get(str(x.get("friendly_name"))) or ""),
        ))
    n = int(_num(data.get("stream_count"), len(out)))
    return _nocache(jsonify(sessions=out, at=int(time.time()), summary=dict(
        streams=n, direct=int(_num(data.get("stream_count_direct_play"))), stream_copy=int(_num(data.get("stream_count_direct_stream"))),
        transcode=int(_num(data.get("stream_count_transcode"))), bandwidth=_mbps(data.get("total_bandwidth")), lan=_mbps(data.get("lan_bandwidth")))))


@app.post("/api/activity/terminate")
def api_activity_terminate():
    """Stop a live stream, optionally showing the viewer a message (Tautulli terminate_session)."""
    bad = _need_tautulli()
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "")
    msg = str(body.get("message") or "").strip()[:200]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
        return jsonify(error="bad stream id"), 400
    try:
        Tautulli(cfg).terminate(key, msg)
    except ApiError as e:
        return jsonify(error=str(e)), 502
    app.logger.warning("Stream %s terminated from the Activity tab (message: %r)", key, msg)
    return jsonify(ok=True)


@app.get("/api/plexthumb")
def api_plexthumb():
    """Poster / avatar for Activity, fetched through Tautulli (the page's CSP only allows our own origin)."""
    img = request.args.get("img", "")
    if not (cfg.tautulli_url and cfg.tautulli_key) or not re.fullmatch(r"(/(library|:/resources|users|photo)|https://([a-z0-9-]+\.)?(plex\.tv|gravatar\.com)/(users|avatar))[A-Za-z0-9_./:%?=&-]{1,300}", img) or ".." in img:
        return "", 404
    body = ct = None
    if "avatar" in request.args and img.startswith("https://"):
        try:                                   # plex.tv / gravatar avatars are public: fetch them directly (host already whitelisted above)
            import requests
            r = requests.get(img, timeout=10)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                body, ct = r.content, r.headers["Content-Type"]
        except Exception:
            pass
    if body is None:
        try:
            body, ct = Tautulli(cfg).image(img, 90, "user") if "avatar" in request.args else Tautulli(cfg).image(img, 300)
        except ApiError:
            return "", 404
    if not ct.startswith("image/"):
        return "", 404
    resp = app.response_class(body, mimetype=ct.split(";")[0])
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/api/plays")
def api_plays():
    """Recent watch history (Tautulli get_history), filterable by user / type / title text."""
    bad = _need_tautulli()
    if bad:
        return bad
    a = request.args
    start = max(0, int(_num(a.get("start"))))
    length = min(200, max(1, int(_num(a.get("length"), 25))))
    mt = a.get("type", "")
    if mt not in ("", "movie", "episode"):
        mt = ""
    try:
        t = Tautulli(cfg)
        rows, total = t.recent(start, length, a.get("user", "")[:100], mt, a.get("q", "")[:100])
        users = sorted({(u.get("friendly_name") or u.get("username") or "") for u in t.users()} - {""}, key=str.lower) if start == 0 else None
    except ApiError as e:
        return jsonify(error=str(e)), 502
    out = []
    for r in rows:
        mt = r.get("media_type") or ""
        base = r.get("grandparent_title") or r.get("title") or r.get("full_title") or ""
        if mt == "episode":
            se = f" (S{r.get('parent_media_index')} · E{r.get('media_index')})" if r.get("parent_media_index") not in (None, "") and r.get("media_index") not in (None, "") else ""
            full = (r.get("full_title") or base) + se
        else:
            full = (r.get("title") or r.get("full_title") or "") + (f" ({r.get('year')})" if r.get("year") else "")
        started, stopped = int(_num(r.get("started") or r.get("date"))), int(_num(r.get("stopped")))
        out.append(dict(
            ts=started, stopped=stopped, user=r.get("friendly_name") or r.get("user") or "?",
            title=full, type=mt, pct=int(_num(r.get("percent_complete"))),
            watched=bool(r.get("watched_status") in (1, "1", True)), duration=int(_num(r.get("duration"))),
            paused=int(_num(r.get("paused_counter"))), ip=r.get("ip_address") or "",
            wan=str(r.get("location") or "").lower() == "wan", live=bool(started and not stopped),
            player=r.get("player") or "", platform=r.get("platform") or "", product=r.get("product") or "",
        ))
    return jsonify(plays=out, total=total, users=users)


_icache = {"sig": None, "body": None}


@app.get("/api/items")
def api_items():
    rep = DATA["report"]
    if rep is None:
        return jsonify(items=[], generated_at=None, warnings=[], empty=True)
    st, keep, sig = _protection_state(rep)
    mid = _machine_id()
    key = (sig, mid, _lv["ver"])
    with _pcache_lock:
        if _icache["sig"] != key:
            prot = st["prot"]
            marks = _lv_marks() if _lv_conf()["enabled"] else {}
            items = [{**{k: v for k, v in i.items() if k not in ("actors", "directors")},
                      "keep": i["keep_id"] in keep, "protected": prot.get(i["id"], []),
                      "leaving_due": (marks.get(i["id"]) or {}).get("due")} for i in rep["items"]]
            body = json.dumps(dict(items=items, generated_at=rep["generated_at"], warnings=rep.get("warnings", []),
                                   history_source=rep.get("history_source"), plex_machine_id=mid))
            _icache.update(sig=key, body=body.encode("utf-8"), etag='"%x"' % (hash(key) & 0xFFFFFFFFFFFF))
        body, etag = _icache["body"], _icache["etag"]
    resp = Response(body, mimetype="application/json")
    resp.headers["ETag"] = etag
    if request.headers.get("If-None-Match") == etag:
        resp = Response(status=304)
        resp.headers["ETag"] = etag
    return resp


_machine = {"id": None, "for": None}


def _machine_id():
    """Plex server identifier (needed to build 'open in Plex' links). Cached; re-fetched if the Plex URL changes."""
    if _machine["id"] and _machine["for"] == cfg.plex_url:
        return _machine["id"]
    if _machine.get("fail_until", 0) > time.time() and _machine.get("fail_for") == cfg.plex_url:
        return None                                   # Plex was unreachable a moment ago; don't make every page load wait for it
    try:
        _machine.update(id=Plex(cfg).machine_id(), **{"for": cfg.plex_url})
    except ApiError:
        _machine.update(fail_until=time.time() + 60, fail_for=cfg.plex_url)
        return None
    return _machine["id"]


_info_cache = {}


@app.get("/api/info/<rk>")
def api_info(rk):
    """Description + details for the info popover, fetched from Plex on demand."""
    rep = DATA["report"]
    if rep is None or rk not in {i["plex_key"] for i in rep["items"]}:  # only items in the report
        return jsonify(error="not found"), 404
    hit = _info_cache.get(rk)
    if hit and time.time() - hit[0] < 3600:
        return jsonify(hit[1])
    try:
        m = Plex(cfg).metadata(rk)
    except ApiError as e:
        return jsonify(error=str(e)), 502
    tags = lambda key, n: [t.get("tag") for t in (m.get(key) or []) if t.get("tag")][:n]
    dur = m.get("duration")
    out = {
        "title": m.get("title"), "year": m.get("year"), "summary": (m.get("summary") or "").strip(),
        "tagline": (m.get("tagline") or "").strip(), "genres": tags("Genre", 6), "content_rating": m.get("contentRating"),
        "runtime_min": round(dur / 60000) if isinstance(dur, (int, float)) and dur else None,
        "studio": m.get("studio"), "directors": tags("Director", 3), "cast": tags("Role", 6),
        "seasons": m.get("childCount") if m.get("type") == "show" else None,
        "episodes": m.get("leafCount") if m.get("type") == "show" else None,
        "released": m.get("originallyAvailableAt"),
    }
    if len(_info_cache) > 3000:
        _info_cache.clear()
    _info_cache[rk] = (time.time(), out)
    return jsonify(out)


def _refresh_worker():
    def progress(msg):
        STATUS["message"] = msg

    try:
        rep = collector.build_report(cfg, progress)
        with DATA_LOCK:
            DATA["report"] = rep
        store.write_json(cfg, "cache.json", rep)
        STATUS.update(state="idle", message="", error=None, finished=int(time.time()))
        try:
            _sync_auto_rows()   # keep Plex home rows current (best effort; never fails the refresh)
        except Exception as ex:  # noqa: BLE001
            print(f"[rows] auto sync error: {ex}", flush=True)
    except Exception as e:  # noqa: BLE001 - surfaced in the UI
        msg = str(e) if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
        print(f"[refresh] failed: {msg}", flush=True)
        STATUS.update(state="error", error=msg, finished=int(time.time()))


def _start_refresh():
    with DATA_LOCK:
        if STATUS["state"] == "running":
            return False
        STATUS.update(state="running", message="Starting", started=int(time.time()), error=None)
    threading.Thread(target=_refresh_worker, daemon=True).start()
    return True


@app.post("/api/refresh")
def api_refresh():
    _start_refresh()
    return jsonify(STATUS), 202


_last_delete = {"t": 0}
SCHED_CHECK = int(os.environ.get("SCHED_CHECK_SECONDS", "60"))


def _scheduler():
    """Automatic refresh: when REFRESH_HOURS (or the Settings page) says so, rebuild the report once it's that old.
    Waits for a quiet moment (not right after a deletion, so Plex's scan + trash cleanup can finish first)."""
    while True:
        time.sleep(SCHED_CHECK)
        try:
            tb = _tb_conf()
            if tb["enabled"] and DATA["report"] and cfg.services()["plex"] and STATUS["state"] != "running":
                st = store.read_json(cfg, "throwback_state.json", {})
                if (st.get("date") != datetime.date.today().isoformat() or st.get("sig") != _tb_sig(tb)) and time.time() - (st.get("time") or 0) > 120:
                    print("[throwback] new day - updating the row", flush=True)
                    try:
                        sync_throwback()
                    except ApiError as ex:
                        store.write_json(cfg, "throwback_state.json", {"date": None, "time": int(time.time()), "error": str(ex)})
                        print(f"[throwback] sync failed: {ex}", flush=True)
            hrs = cfg.refresh_hours
            if not hrs or not cfg.services()["plex"] or STATUS["state"] == "running":
                continue
            if time.time() - _last_delete["t"] < 900:
                continue
            rep = DATA["report"]
            age = time.time() - rep["generated_at"] if rep else None
            failed_recently = STATUS["state"] == "error" and time.time() - (STATUS.get("finished") or 0) < 1800
            if (age is None or age >= hrs * 3600) and not failed_recently:
                print(f"[refresh] automatic refresh (every {hrs}h)", flush=True)
                _start_refresh()
        except Exception as ex:  # noqa: BLE001 - never let the scheduler die
            print(f"[refresh] scheduler error: {type(ex).__name__}: {ex}", flush=True)


threading.Thread(target=_scheduler, daemon=True).start()


_pcache = {"sig": None, "val": None}
_pcache_lock = threading.Lock()


def _protection_state(rep):
    """Rule matching over the whole library is the expensive part, so it's computed once per
    (report, rules, keep-list) combination and reused by /api/items and /api/rules."""
    rl, keep = load_rules(), store.load_keep(cfg)
    sig = (id(rep["items"]), len(rep["items"]), rep.get("generated_at"), json.dumps(rl, sort_keys=True), hash(frozenset(keep)))
    with _pcache_lock:
        if _pcache["sig"] == sig:
            return _pcache["val"], keep, sig
        items = rep["items"]
        per_rule = {}
        for r in rl:
            m = rules.matcher(r)
            hit = [i for i in items if m(i)]
            per_rule[r["id"]] = (len(hit), sum(i.get("size") or 0 for i in hit))
        prot = rules.protection_map(items, rl, keep)
        _pcache.update(sig=sig, val={"prot": prot, "per_rule": per_rule})
        return _pcache["val"], keep, sig


def load_rules():
    r = store.read_json(cfg, "protection.json", {"rules": []}).get("rules", [])
    return r if isinstance(r, list) else []


def _rules_payload():
    rep = DATA["report"]
    items = rep["items"] if rep else []
    st, keep, _ = _protection_state(rep) if rep else ({"prot": {}, "per_rule": {}}, set(), None)
    out = []
    for r in load_rules():
        n, size = st["per_rule"].get(r["id"], (0, 0))
        out.append({**r, "label": rules.label(r), "matches": n, "size": size})
    prot = st["prot"]
    by_id = {i["id"]: i for i in items}
    sz = lambda ids: sum(by_id[x].get("size") or 0 for x in ids)
    by_rule = [x for x, why in prot.items() if any(w != "Marked Keep" for w in why)]
    by_keep = [x for x, why in prot.items() if "Marked Keep" in why]
    unw = [x for x in prot if not (by_id[x].get("plays_total") or 0)]
    summary = {"unwatched_items": len(unw), "unwatched_size": sz(unw), "items": len(prot), "size": sz(prot), "rule_items": len(by_rule), "rule_size": sz(by_rule),
               "keep_items": len(by_keep), "keep_size": sz(by_keep),
               "total_items": len(items), "total_size": sum(i.get("size") or 0 for i in items)}
    return {"rules": out, "types": rules.TYPES, "have_report": rep is not None, "summary": summary}


@app.get("/api/rules")
def api_rules():
    return jsonify(_rules_payload())


@app.post("/api/rules")
def api_rules_save():
    """body: {add: {...}} | {update: id, enabled?, ...fields} | {delete: id}"""
    body = request.get_json(silent=True) or {}
    cur = load_rules()
    with DATA_LOCK:
        if "delete" in body:
            cur = [r for r in cur if r.get("id") != body["delete"]]
        elif "update" in body:
            for r in cur:
                if r.get("id") == body["update"]:
                    if "enabled" in body and len(body) == 2:
                        r["enabled"] = bool(body["enabled"])
                    else:
                        f, err = rules.validate({**r, **body})
                        if err:
                            return jsonify(error=err), 400
                        r.update(f)
                    r.pop("source", None)         # superseded by "sources"
        elif "add" in body:
            f, err = rules.validate(body["add"] or {})
            if err:
                return jsonify(error=err), 400
            if len(cur) >= rules.MAX_RULES:
                return jsonify(error="too many rules"), 400
            if any(r["type"] == f["type"] and rules.norm(r["pattern"]) == rules.norm(f["pattern"]) and r.get("top") == f["top"] for r in cur):
                return jsonify(error="that rule already exists"), 400
            cur.append(rules.new_rule(f))
        else:
            return jsonify(error="nothing to do"), 400
        if not store.write_json(cfg, "protection.json", {"rules": cur}):
            return jsonify(error="could not save rules (is /config writable?)"), 500
    return jsonify(_rules_payload())


@app.post("/api/rules/preview")
def api_rules_preview():
    """Which titles a rule would protect, before it is saved."""
    body = request.get_json(silent=True) or {}
    saved = next((r for r in load_rules() if r.get("id") == body.get("id")), None) if body.get("id") else None
    if body.get("id") and not saved:
        return jsonify(error="rule not found"), 404
    f, err = (saved, None) if saved else rules.validate(body)
    if err:
        return jsonify(error=err), 400
    m = rules.matcher({**f, "id": "x"})
    rep = DATA["report"]
    found = [i for i in (rep["items"] if rep else []) if m(i)]
    hits = [f"{i['title']}" + (f" ({i['year']})" if i.get("year") else "") for i in found]
    cap = 500 if saved else 25
    return jsonify(count=len(hits), titles=hits[:cap], sizes=[i.get("size") or 0 for i in found[:cap]],
                   total_size=sum(i.get("size") or 0 for i in found))


@app.post("/api/keep")
def api_keep():
    body = request.get_json(silent=True) or {}
    rep = DATA["report"] or {"items": []}
    by_id = {i["id"]: i for i in rep["items"]}
    keep = store.load_keep(cfg)
    for i in body.get("ids", []):
        if i in by_id:
            (keep.add if body.get("keep", True) else keep.discard)(by_id[i]["keep_id"])
    store.save_keep(cfg, keep)
    return jsonify(ok=True)


@app.post("/api/delete")
def api_delete():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify(error="nothing selected"), 400
    if len(ids) > cfg.max_batch:
        return jsonify(error=f"too many items in one request (max {cfg.max_batch})"), 400
    o = body.get("options") or {}
    opts = {k: bool(o.get(k)) for k in ("arr", "exclusion", "seerr", "plex_cleanup", "plex_delete")}

    with DATA_LOCK:
        rep = DATA["report"]
        if rep is None:
            return jsonify(error="no report loaded"), 400
        by_id = {i["id"]: i for i in rep["items"]}
    items = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
    if not items:
        return jsonify(error="none of the selected items are in the current report - refresh and try again"), 400

    # Enforced here, whatever the browser sent: Keep marks and every enabled protection rule
    prot = rules.protection_map(items, load_rules(), store.load_keep(cfg))
    blocked = {i: "; ".join(r) for i, r in prot.items()}
    results, summary = deleter.run(cfg, items, opts, cfg.dry_run, blocked)
    if not cfg.dry_run and any(r["ok"] for r in results):
        _last_delete["t"] = time.time()

    ip = _client_ip()
    for r in results:
        store.append_log(cfg, {"dry_run": cfg.dry_run, "ip": ip, "id": r["id"], "title": r["title"], "year": r["year"],
                               "type": r["type"], "library": r["library"],
                               "requested_by": by_id.get(r["id"], {}).get("requested_by", []),
                               "imdb_id": by_id.get(r["id"], {}).get("imdb_id"),
                               "tmdb_id": by_id.get(r["id"], {}).get("tmdb_id"),
                               "requester_ids": by_id.get(r["id"], {}).get("requester_ids", {}),
                               "size": r["size"], "ok": r["ok"], "options": opts, "steps": r["steps"]})

    if not cfg.dry_run:
        gone = {r["id"] for r in results if r["ok"]}
        if gone:
            with DATA_LOCK:
                rep["items"] = [i for i in rep["items"] if i["id"] not in gone]
            store.write_json(cfg, "cache.json", rep)
            if _lv_marks():
                _lv_drop(gone)
    return jsonify(results=results, summary=summary)


def _norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", rules.norm(s)).strip()


def _log_entry(body):
    for e in store.read_log(cfg, 5000):
        if str(e.get("time")) == str(body.get("time")) and e.get("id") == body.get("id"):
            return e
    return None


def _resolve_tmdb(e):
    """TMDB id for a logged deletion: recorded when known, otherwise found by exact title + year in Seerr. -> (id, title, year)"""
    mtype = "movie" if e.get("type") == "movie" else "tv"
    if str(e.get("tmdb_id") or "").isdigit():
        return int(e["tmdb_id"]), e["title"], e.get("year")
    want = _norm_title(e["title"])
    for r in clients.Seerr(cfg).search(e["title"]):
        if r.get("mediaType") != mtype:
            continue
        name = r.get("title") or r.get("name") or ""
        date = r.get("releaseDate") or r.get("firstAirDate") or ""
        yr = int(date[:4]) if date[:4].isdigit() else None
        if _norm_title(name) == want and (not e.get("year") or yr is None or abs(yr - int(e["year"])) <= 1):
            return int(r["id"]), name, yr
    raise ApiError("couldn't find an exact match for that title in Seerr")


MAX_REQUEST_BATCH = 50


def _date10(v):
    v = str(v or "")[:10]
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None


def _seerr_lookup(sc, tt, meta):
    """Find a list title in Seerr and say whether it's already available / requested. -> dict (never raises)."""
    out = {"imdb_id": tt, "title": meta.get("title"), "year": meta.get("year"), "type": meta.get("type")}
    kind = meta.get("type")
    if kind not in ("movie", "show"):
        return {**out, "status": "unmatched", "detail": "the list doesn't say whether this is a movie or a show"}
    sk = "movie" if kind == "movie" else "tv"
    title, year = meta.get("title"), meta.get("year")

    def ok(d):
        name = d.get("title") or d.get("name") or d.get("originalTitle") or d.get("originalName") or ""
        date = d.get("releaseDate") or d.get("firstAirDate") or ""
        yr = int(date[:4]) if date[:4].isdigit() else None
        return (not title or _norm_title(name) == _norm_title(title)) and (not year or yr is None or abs(yr - int(year)) <= 1)
    try:
        details = None
        if meta.get("tmdb"):
            try:
                details = sc.get(f"/{sk}/{int(meta['tmdb'])}")
                if not ok(details):
                    details = None
            except ApiError:
                details = None
        if details is None:
            if not title:
                return {**out, "status": "unmatched", "detail": "the list gave only an IMDb id (no title to search for)"}
            hit = next((r for r in sc.search(title) if r.get("mediaType") == sk and ok(r)), None)
            if not hit:
                return {**out, "status": "unmatched", "detail": "no exact match in Seerr"}
            details = sc.get(f"/{sk}/{int(hit['id'])}")
        mi = details.get("mediaInfo") or {}
        st = mi.get("status")
        state = "available" if st in (4, 5) else "requested" if (st in (2, 3) or mi.get("requests")) else "none"
        vote = details.get("voteAverage")
        return {**out, "status": state, "tmdb": int(details["id"]), "kind": sk, "name": details.get("title") or details.get("name"),
                "poster": details.get("posterPath") if re.fullmatch(r"/[A-Za-z0-9_-]{5,60}\.(jpg|png)", str(details.get("posterPath") or "")) else None,
                "animation": any((g.get("id") == 16 or str(g.get("name", "")).lower() == "animation") for g in (details.get("genres") or []) if isinstance(g, dict)),
                "seerr_released": _date10(details.get("releaseDate") or details.get("firstAirDate")),
                "seerr_rating": round(float(vote), 1) if isinstance(vote, (int, float)) and vote > 0 else None}
    except ApiError as e:
        return {**out, "status": "error", "detail": str(e)}


def _row_by_id(rid):
    return next((r for r in load_rows() if r["id"] == rid), None)


def _row_list_meta(row):
    srcs = extlists.row_sources(row)
    ids, notes = extlists.resolve_all(cfg, srcs, fetch=True, fresh=extlists.FRESH_PREVIEW)
    return ids or [], extlists.meta_for(cfg, srcs), notes


def _rerequest_entry():
    body = request.get_json(silent=True) or {}
    if not cfg.services()["seerr"]:
        return None, None, (jsonify(error="Seerr isn't configured (Settings)"), 400)
    e = _log_entry(body)
    if not e or e.get("dry_run") or not e.get("ok"):
        return None, None, (jsonify(error="that title isn't in the deletion log"), 404)
    return body, e, None


@app.post("/api/rerequest/lookup")
def api_rerequest_lookup():
    body, e, err = _rerequest_entry()
    if err:
        return err
    try:
        tmdb, title, year = _resolve_tmdb(e)
    except ApiError as ex:
        print(f"[rerequest] lookup failed for {e.get('title')!r} ({e.get('year')}): {ex}", flush=True)
        return jsonify(error=f"{ex} ({e.get('title')}, {e.get('year')})"), 404
    return jsonify(tmdb_id=tmdb, title=title, year=year)


@app.post("/api/rerequest")
def api_rerequest():
    """Re-request a title we previously deleted (it must be in the deletion log) through Seerr."""
    body, e, err = _rerequest_entry()
    if err:
        return err
    try:
        tmdb, title, year = _resolve_tmdb(e)
        uid = None
        who = body.get("as_user")
        if who:
            uid = (e.get("requester_ids") or {}).get(who)
            if uid is None:
                return jsonify(error="unknown requester"), 400
        res = clients.Seerr(cfg).request_title("movie" if e.get("type") == "movie" else "tv", tmdb, uid)
    except ApiError as ex:
        print(f"[rerequest] failed for {e.get('title')!r}: {ex}", flush=True)
        return jsonify(error=str(ex)), 502
    print(f"[rerequest] {e.get('type')} tmdb:{tmdb} as {who or 'API key user'}", flush=True)
    return jsonify(ok=True, status=res.get("status"), as_user=who, title=title)


# ---------- Request tab: search Seerr and request any title without leaving Phlexerr ----------
def _seerr_card(r):
    kind = r.get("mediaType")
    if kind not in ("movie", "tv") or not r.get("id"):
        return None
    d = r.get("releaseDate") if kind == "movie" else r.get("firstAirDate")
    mi = r.get("mediaInfo") or {}
    st = {5: "available", 4: "partial", 3: "processing", 2: "pending", 6: "blocklisted"}.get(mi.get("status"), "")
    poster = (r.get("posterPath") or "").lstrip("/")
    return {"tmdb_id": int(r["id"]), "type": kind, "title": r.get("title") or r.get("name") or "",
            "year": (d or "")[:4], "overview": (r.get("overview") or "")[:300],
            "rating": round(float(r.get("voteAverage") or 0), 1),
            "poster": poster if re.fullmatch(r"[A-Za-z0-9_-]{5,60}\.(jpg|png)", poster) else "", "status": st}


@app.get("/api/seerr/search")
def api_seerr_search():
    if not cfg.services()["seerr"]:
        return jsonify(error="Seerr isn't configured (Settings)"), 400
    q = (request.args.get("q") or "").strip()
    if len(q) < 2 or len(q) > 100:
        return jsonify(results=[])
    try:
        raw = clients.Seerr(cfg).search(q)
    except ApiError as ex:
        return jsonify(error=str(ex)), 502
    out = [c for c in (_seerr_card(r) for r in raw) if c][:24]
    return _nocache(jsonify(results=out))


@app.post("/api/seerr/request")
def api_seerr_request():
    """Request a title through Seerr (as the API key's user). Seerr then hands it to Radarr/Sonarr itself."""
    if not cfg.services()["seerr"]:
        return jsonify(error="Seerr isn't configured (Settings)"), 400
    b = request.get_json(silent=True) or {}
    kind = b.get("type")
    try:
        tmdb = int(b.get("tmdb_id"))
    except (TypeError, ValueError):
        return jsonify(error="bad title id"), 400
    if kind not in ("movie", "tv") or tmdb <= 0:
        return jsonify(error="bad title type"), 400
    try:
        res = clients.Seerr(cfg).request_title(kind, tmdb)
    except ApiError as ex:
        print(f"[request] {kind} tmdb:{tmdb} failed: {ex}", flush=True)
        return jsonify(error=str(ex)), 502
    print(f"[request] {kind} tmdb:{tmdb} requested from the Request tab", flush=True)
    return jsonify(ok=True, status=res.get("status"))


TB_ID = "__tb__"      # the built-in "Released on this day" row takes part in the Home ordering under this id


def home_order(rws=None):
    """Ids of every row in Home order (custom rows + the built-in nostalgia row). Default: nostalgia row first."""
    rws = load_rows() if rws is None else rws
    saved = store.read_json(cfg, "rows_order.json", {"order": []}).get("order", [])
    ids = [r["id"] for r in rws] + [TB_ID]
    out = [i for i in dict.fromkeys(saved) if i in ids]
    rest = [i for i in ids if i not in out]
    return ([TB_ID] if TB_ID in rest else []) + out + [i for i in rest if i != TB_ID]


def load_rows():
    r = store.read_json(cfg, "rows.json", {"rows": []}).get("rows", [])
    return r if isinstance(r, list) else []


def save_rows(lst):
    return store.write_json(cfg, "rows.json", {"rows": lst})


_jobs = {}
_jobs_lock = threading.Lock()


def _start_job(row, kind="publish", fn=None):
    """Run a row task (publish, or request titles) in the background so the page can show progress. One running job per row and kind."""
    with _jobs_lock:
        for j in _jobs.values():
            if j["row"] == row["id"] and j["kind"] == kind and j["state"] == "running":
                return j
        for k in [k for k, j in _jobs.items() if j["state"] != "running" and time.time() - j["ended"] > 600]:
            del _jobs[k]
        j = {"id": secrets.token_hex(6), "row": row["id"], "kind": kind, "name": row["name"], "state": "running", "stage": "Starting", "pct": 1,
             "started": time.time(), "ended": 0}
        _jobs[j["id"]] = j

    def prog(stage, pct):
        j["stage"], j["pct"] = stage, max(j["pct"], min(99, int(pct)))

    def run():
        try:
            if fn:
                j.update(state="done", pct=100, stage="Done", results=fn(prog))
            else:
                done = sync_row(row, prog)
                _touch_row(row["id"], None)
                j.update(state="done", pct=100, stage="Done", results=done)
        except ApiError as e:
            if not fn:
                _touch_row(row["id"], str(e))
            j.update(state="error", error=str(e))
        except Exception as e:      # never leave the page waiting on a crashed job
            print(f"[rows] publish of {row['name']!r} crashed: {e!r}", flush=True)
            j.update(state="error", error=f"unexpected error: {e}")
        j["ended"] = time.time()
        print(f"[rows] {kind} {row['name']!r}: {j['state']} in {j['ended'] - j['started']:.0f}s", flush=True)

    threading.Thread(target=run, daemon=True).start()
    return j


def _job_view(j):
    return {k: j.get(k) for k in ("id", "row", "kind", "name", "state", "stage", "pct", "error", "results")} | {"seconds": round((j["ended"] or time.time()) - j["started"])}


def _row_ids(row, fetch, fresh=extlists.FRESH_SYNC):
    """The row with its external-list ids resolved (and a note when the list couldn't be fetched fresh)."""
    srcs = extlists.row_sources(row)
    if not srcs:
        return row, None
    ids, notes = extlists.resolve_all(cfg, srcs, fetch=fetch, fresh=fresh)
    return {**row, "_ids": ids if ids is not None else []}, "; ".join(notes) or None


def _rows_payload():
    rep = DATA["report"]
    items = rep["items"] if rep else []
    out = []
    for r in load_rows():
        rr, note = _row_ids(r, fetch=False)          # page loads never hit the network; syncs refresh the list
        shown_src = [({"type": "csv", "count": len(x.get("ids") or [])} if x.get("type") == "csv" else x) for x in extlists.row_sources(r)]
        out.append({**{k: v for k, v in r.items() if k not in ("source", "sources")}, "sources": shown_src, "summary": rows.summary(r),
                    "matches": len(rows.select(items, {**rr, "limit": 100000})), "shown": len(rows.select(items, rr)),
                    **({"list_note": note} if note else {})})
    tb = _tb_conf()
    st = store.read_json(cfg, "throwback_state.json", {})
    tbi = {"enabled": tb["enabled"], "name": tb["name"], "pin": tb["pin"], "state": st,
           "count": len(throwback.select(items, tb)) if rep and tb["enabled"] else None}
    return {"rows": out, "sorts": rows.SORTS, "have_report": rep is not None, "types": rules.TYPES, "throwback": tbi,
            "order": home_order(),
            "jobs": [_job_view(j) for j in _jobs.values() if j["state"] == "running" and j["kind"] == "publish"]}


def _apply_order(plex, cid, keys, prog=None):
    """Make the collection's manual order match `keys` (Plex 'custom' sort + item moves). Best effort."""
    plex.collection_sort_custom(cid)
    cur = [k for k in plex.collection_children(cid) if k in set(keys)]
    for n, k in enumerate(keys):
        if prog and n % 5 == 0:
            prog(f"Putting titles in order ({n} of {len(keys)})", n / max(1, len(keys)))
        if n < len(cur) and cur[n] == k:
            continue
        plex.collection_move(cid, k, keys[n - 1] if n else None)
        cur.remove(k)
        cur.insert(n, k)


def sync_items(name, chosen, pin, shared=True, prog=None):
    """Make Plex collection(s) called `name` hold exactly `chosen` (in that order), one per library, pinned or not."""
    plex = Plex(cfg)
    mid = plex.machine_id()
    groups = {}
    for i in chosen:
        groups.setdefault((str(i["section_id"]), i["type"] == "show"), []).append(i["plex_key"])
    done = []
    secs = [x for x in plex.sections() if x.get("type") in ("movie", "show")]
    note = lambda si, frac, text: prog and prog(text, 20 + 70 * (si + frac) / max(1, len(secs)))
    for si, s in enumerate(secs):
        sid, is_show = str(s["key"]), s.get("type") == "show"
        note(si, 0, f"Looking at the {s.get('title')} library")
        keys = groups.get((sid, is_show), [])
        existing = next((c for c in plex.collections(sid) if (c.get("title") or "").casefold() == name.casefold()), None)
        if not keys:
            if existing:
                plex.delete_collection(existing["ratingKey"])
                done.append({"library": sid, "action": "removed (nothing matches there now)"})
            continue
        if existing is None:
            note(si, 0.1, f"Creating the collection with {len(keys)} titles")
            cid, action = plex.create_collection(sid, name, is_show, mid, keys), "created"
        else:
            cid, action = str(existing["ratingKey"]), "updated"
            have = set(plex.collection_children(cid))
            new = [k for k in keys if k not in have]
            if new:
                note(si, 0.1, f"Adding {len(new)} titles to the collection")
                plex.collection_add(cid, mid, new)
            gone = list(have - set(keys))
            for n, k in enumerate(gone):
                note(si, 0.2, f"Removing titles no longer on the list ({n} of {len(gone)})")
                plex.collection_remove(cid, k)
        order_note = None
        try:
            _apply_order(plex, cid, keys, (lambda t, f: note(si, 0.3 + 0.6 * f, t)) if prog else None)
        except ApiError as ex:     # the collection is still correct; only the manual order failed
            order_note = f"couldn't set the order ({ex})"
        note(si, 0.95, "Showing it on Home")
        plex.promote_collection(sid, cid, pin, shared=shared)
        done.append({"library": sid, "action": action, "items": len(keys), "pinned": pin, "shared": bool(pin and shared), **({"warning": order_note} if order_note else {})})
    return done


def sync_row(row, prog=None):
    rep = DATA["report"]
    if not rep:
        raise ApiError("no report yet - click Refresh data first")
    t0 = time.time()
    if prog:
        n = len(extlists.row_sources(row))
        prog(f"Downloading {n} IMDb list{'s' if n != 1 else ''}" if any(x.get("type") == "imdb" for x in extlists.row_sources(row)) else "Choosing titles", 2)
    row, note = _row_ids(row, fetch=True)
    t1 = time.time()
    if prog:
        prog("Choosing the titles that are in your library", 18)
    done = sync_items(row["name"], rows.select(rep["items"], row), row["pin"], row.get("shared", True), prog)
    t2 = time.time()
    if prog:
        prog("Arranging your rows on Home", 92)
    order_home()
    print(f"[rows] {row['name']!r}: lists {t1 - t0:.1f}s, Plex collection {t2 - t1:.1f}s, Home order {time.time() - t2:.1f}s", flush=True)
    if note:
        done.append({"library": "-", "action": "used the saved copy of the list", "warning": note})
    done.append({"library": "-", "action": f"took {time.time() - t0:.0f}s (lists {t1 - t0:.1f}s, Plex {t2 - t1:.1f}s)"})
    return done


def order_home():
    """Arrange our pinned rows on Home in the order they're listed in the Plex Home Screen tab (top of Home, in that order).
    Best effort: a failure here never undoes the sync."""
    try:
        plex = Plex(cfg)
        rws = {r["id"]: r for r in load_rows()}
        tb = _tb_conf()
        wanted = []
        for i in home_order():
            if i == TB_ID:
                if tb["enabled"] and tb["pin"]:
                    wanted.append(tb["name"].casefold())
            elif rws[i].get("pin", True):
                wanted.append(rws[i]["name"].casefold())
        for s in plex.sections():
            if s.get("type") not in ("movie", "show"):
                continue
            have = {(c.get("title") or "").casefold(): c["ratingKey"] for c in plex.collections(s["key"])}
            ids = [have[n] for n in wanted if n in have]
            for n, cid in enumerate(ids):
                plex.hub_move(s["key"], cid, ids[n - 1] if n else None)
        return None
    except ApiError as ex:
        print(f"[rows] couldn't set the Home order: {ex}", flush=True)
        return str(ex)


# ---- "released on this day" row ------------------------------------------------
def _tb_conf(raw=None):
    return throwback.parse(cfg.tb_raw if raw is None else raw)


def _tb_payload(conf):
    rep = DATA["report"]
    items = throwback.select(rep["items"], conf) if rep else []
    st = store.read_json(cfg, "throwback_state.json", {})
    return {"enabled": conf["enabled"], "count": len(items), "have_report": rep is not None, "state": st,
            "titles": [f"{i['title']} ({(i.get('released') or '')[:4]})" for i in items[:15]],
            "all": [{"title": i["title"], "released": i.get("released"), "rating": i.get("rating")} for i in items[:2000]], "today": datetime.date.today().isoformat(),
            "scopes": throwback.SCOPES, "orders": throwback.ORDERS}


def remove_collections_named(name):
    """Delete Plex collections with this name (collections only; the media is untouched)."""
    p = Plex(cfg)
    n = 0
    for s in p.sections():
        if s.get("type") in ("movie", "show"):
            for c in p.collections(s["key"]):
                if (c.get("title") or "").casefold() == name.casefold():
                    p.delete_collection(c["ratingKey"])
                    n += 1
    return n


def _tb_sig(conf):
    return json.dumps(conf, sort_keys=True)


_tb_lock = threading.Lock()


def sync_throwback():
    with _tb_lock:          # settings-save, the daily timer and the Publish button must never run at once
        return _sync_throwback_locked()


def _sync_throwback_locked():
    conf = _tb_conf()
    rep = DATA["report"]
    if not rep:
        raise ApiError("no report yet - click Refresh data first")
    st = store.read_json(cfg, "throwback_state.json", {})
    if st.get("name") and st["name"].casefold() != conf["name"].casefold():
        remove_collections_named(st["name"])         # renamed: don't leave the old row behind
    chosen = throwback.select(rep["items"], conf)
    done = sync_items(conf["name"], chosen, conf["pin"])
    store.write_json(cfg, "throwback_state.json", {"date": datetime.date.today().isoformat(), "time": int(time.time()),
                                                  "count": len(chosen), "error": None, "name": conf["name"], "sig": _tb_sig(conf)})
    return done


def _tb_settings_changed(before_conf):
    """Called after Settings are saved: apply the row automatically (set and forget)."""
    after = _tb_conf()
    try:
        if after["enabled"]:
            if DATA["report"]:
                sync_throwback()
        elif before_conf["enabled"]:
            remove_collections_named(before_conf["name"])
            store.write_json(cfg, "throwback_state.json", {})
            print("[throwback] turned off - row removed from Plex", flush=True)
    except ApiError as ex:
        store.write_json(cfg, "throwback_state.json", {"date": None, "time": int(time.time()), "error": str(ex)})
        print(f"[throwback] couldn't apply settings: {ex}", flush=True)


# ---- Leaving Soon (off by default; manual marks only, nothing is ever deleted by it) ---------
_lv = {"ver": 0, "state_err": None}
_lv_lock = threading.Lock()


def _lv_conf(raw=None):
    return leaving.parse(cfg.leaving_raw if raw is None else raw)


def _lv_marks():
    m = store.read_json(cfg, "leaving.json", {})
    return m if isinstance(m, dict) else {}


def _lv_save(m):
    store.write_json(cfg, "leaving.json", m)
    _lv["ver"] += 1


def _lv_drop(ids):
    """Titles that were deleted (or vanished from Plex) stop being 'leaving soon'."""
    with _lv_lock:
        m = _lv_marks()
        left = {k: v for k, v in m.items() if k not in ids}
        if len(left) != len(m):
            _lv_save(left)
    if _lv_conf()["enabled"]:
        threading.Thread(target=_lv_sync_bg, daemon=True).start()


def sync_leaving():
    """Make the Plex 'Leaving Soon' row match the marks, or remove it when the feature is off / nothing is marked."""
    with _lv_lock:
        conf = _lv_conf()
        st = store.read_json(cfg, "leaving_state.json", {})
        if st.get("name") and st["name"].casefold() != conf["name"].casefold():
            remove_collections_named(st["name"])         # renamed: don't leave the old row behind
        if not conf["enabled"]:
            if st.get("name"):
                remove_collections_named(st["name"])
            store.write_json(cfg, "leaving_state.json", {})
            return []
        rep = DATA["report"]
        if not rep:
            raise ApiError("no report yet - click Refresh data first")
        marks = _lv_marks()
        by_id = {i["id"]: i for i in rep["items"]}
        stale = [k for k in marks if k not in by_id]
        if stale:
            marks = {k: v for k, v in marks.items() if k in by_id}
            _lv_save(marks)
        chosen = sorted((by_id[k] for k in marks), key=lambda i: (marks[i["id"]].get("due") or 0, i["title"].casefold()))
        done = sync_items(conf["name"], chosen, conf["pin"])
        store.write_json(cfg, "leaving_state.json", {"name": conf["name"], "time": int(time.time()), "count": len(chosen), "error": None})
        return done


def _lv_sync_bg():
    try:
        sync_leaving()
    except ApiError as ex:
        store.write_json(cfg, "leaving_state.json", {**store.read_json(cfg, "leaving_state.json", {}), "error": str(ex), "time": int(time.time())})
        print(f"[leaving] sync failed: {ex}", flush=True)


def _lv_settings_changed(before):
    try:
        if _lv_conf()["enabled"] and not DATA["report"]:
            return
        if before["enabled"] and not _lv_conf()["enabled"]:
            remove_collections_named(before["name"])      # turned off: the row disappears from Plex Home
            store.write_json(cfg, "leaving_state.json", {})
            print("[leaving] turned off - row removed from Plex", flush=True)
        else:
            sync_leaving()
    except ApiError as ex:
        store.write_json(cfg, "leaving_state.json", {"name": before["name"], "error": str(ex), "time": int(time.time())})
        print(f"[leaving] couldn't apply settings: {ex}", flush=True)


def _lv_payload():
    conf, marks = _lv_conf(), _lv_marks()
    now = time.time()
    st = store.read_json(cfg, "leaving_state.json", {})
    on = conf["enabled"]
    return {"enabled": on, "name": conf["name"], "days": conf["days"], "count": len(marks) if on else 0,
            "overdue": sum(1 for v in marks.values() if (v.get("due") or 0) < now) if on else 0, "error": st.get("error")}


@app.get("/api/leaving")
def api_leaving():
    return jsonify(_lv_payload())


def _lv_ids(body):
    rep = DATA["report"] or {"items": []}
    by_id = {i["id"]: i for i in rep["items"]}
    ids = [i for i in dict.fromkeys(body.get("ids") or []) if isinstance(i, str) and i in by_id][:500]
    return ids, by_id


@app.post("/api/leaving/mark")
def api_leaving_mark():
    """Mark titles as 'leaving soon' with a due date. A mark never deletes anything."""
    conf = _lv_conf()
    if not conf["enabled"]:
        return jsonify(error="Leaving Soon is turned off. Turn it on in Settings first."), 400
    body = request.get_json(silent=True) or {}
    days = body.get("days", conf["days"])
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        return jsonify(error="days must be a whole number from 1 to 365"), 400
    ids, by_id = _lv_ids(body)
    prot = rules.protection_map([by_id[i] for i in ids], load_rules(), store.load_keep(cfg))
    ok = [i for i in ids if i not in prot]                 # protected / Keep titles can't be deleted, so they can't be 'leaving'
    now = int(time.time())
    with _lv_lock:
        m = _lv_marks()
        for i in ok:
            m[i] = {"marked": now, "due": now + days * 86400}
        _lv_save(m)
    threading.Thread(target=_lv_sync_bg, daemon=True).start()
    return jsonify(ok=True, marked=len(ok), skipped_protected=len(ids) - len(ok), due=now + days * 86400)


@app.post("/api/leaving/unmark")
def api_leaving_unmark():
    ids, _ = _lv_ids(request.get_json(silent=True) or {})
    _lv_drop(set(ids))
    return jsonify(ok=True, unmarked=len(ids))


@app.get("/api/facets")
def api_facets():
    """Genres and tags (Plex labels) present in the library, for the pickers."""
    rep = DATA["report"]
    g, l, libs = {}, {}, set()
    for i in (rep["items"] if rep else []):
        if i.get("library"):
            libs.add(i["library"])
        for x in i.get("genres") or []:
            g[x] = g.get(x, 0) + 1
        for x in i.get("labels") or []:
            l[x] = l.get(x, 0) + 1
    srt = lambda d: sorted(d, key=lambda k: (-d[k], k))
    return jsonify(genres=srt(g), tags=srt(l), libraries=sorted(libs, key=str.casefold))


@app.get("/api/throwback")
def api_throwback():
    return jsonify(_tb_payload(_tb_conf()))


@app.post("/api/throwback/preview")
def api_throwback_preview():
    """Preview with the values currently in the form (not yet saved)."""
    vals = {k: str(v) for k, v in ((request.get_json(silent=True) or {}).get("values") or {}).items() if k in throwback.FIELDS}
    err = throwback.validate(vals)
    if err:
        return jsonify(error=err), 400
    return jsonify(_tb_payload(_tb_conf({**cfg.tb_raw, **vals})))


@app.post("/api/throwback/publish")
def api_throwback_publish():
    try:
        done = sync_throwback()
    except ApiError as e:
        store.write_json(cfg, "throwback_state.json", {"date": None, "time": int(time.time()), "error": str(e)})
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, results=done, **_tb_payload(_tb_conf()))


def _sync_auto_rows():
    if _lv_conf()["enabled"]:
        try:
            sync_leaving()
        except ApiError as ex:
            print(f"[leaving] sync failed: {ex}", flush=True)
    if _tb_conf()["enabled"]:
        try:
            sync_throwback()
        except ApiError as ex:
            print(f"[throwback] sync failed: {ex}", flush=True)
    for r in load_rows():
        if r.get("auto"):
            try:
                sync_row(r)
                _touch_row(r["id"], None)
            except ApiError as ex:
                print(f"[rows] auto sync of {r['name']!r} failed: {ex}", flush=True)
                _touch_row(r["id"], str(ex))


def _touch_row(rid, error):
    cur = load_rows()
    for r in cur:
        if r["id"] == rid:
            r["published"] = {"time": int(time.time()), "error": error}
    save_rows(cur)


@app.get("/api/rows")
def api_rows():
    return jsonify(_rows_payload())


@app.get("/api/rows/job/<jid>")
def api_rows_job(jid):
    j = _jobs.get(jid)
    if not j:
        return jsonify(error="unknown job"), 404
    return jsonify(job=_job_view(j))


@app.post("/api/rows")
def api_rows_save():
    """body: {add: {...}} | {update: id, ...fields} | {delete: id} | {sync: id} | {preview: {...}}"""
    body = request.get_json(silent=True) or {}
    cur = load_rows()
    if "preview" in body:
        f, err = rows.validate(body["preview"] or {})
        if err:
            return jsonify(error=err), 400
        rep = DATA["report"]
        try:
            f, note = _row_ids(f, fetch=True, fresh=extlists.FRESH_PREVIEW)
        except ApiError as e:
            return jsonify(error=str(e)), 502
        hits = rows.select(rep["items"] if rep else [], {**f, "limit": 100000})
        shown = rows.select(rep["items"] if rep else [], f)
        return jsonify(count=len(hits), shown=len(shown), list_total=len(f["_ids"]) if f.get("_ids") is not None else None, note=note, titles=[f"{i['title']}" + (f" ({i['year']})" if i.get("year") else "") for i in shown[:12]])
    if "order" in body:
        ids = [x for x in (body["order"] or []) if isinstance(x, str)]
        if not store.write_json(cfg, "rows_order.json", {"order": ids}):
            return jsonify(error="could not save (is /config writable?)"), 500
        note = order_home() if cfg.services()["plex"] else None
        return jsonify(order_note=note, **_rows_payload())
    if "missing" in body:
        row = _row_by_id(body["missing"])
        if not row or not extlists.row_sources(row):
            return jsonify(error="that row doesn't use a list"), 404
        try:
            ids, meta, notes = _row_list_meta(row)
        except ApiError as e:
            return jsonify(error=str(e)), 502
        rep = DATA["report"]
        have = {i.get("imdb_id") for i in (rep["items"] if rep else [])}
        want = row.get("media", "both")
        items = []
        for tt in ids:
            m = meta.get(tt, {})
            if tt in have or (want != "both" and m.get("type") and m["type"] != want):
                continue
            items.append({"imdb_id": tt, **m})
        return jsonify(total=len(ids), in_plex=len(ids) - len(items), items=items[:2000], notes=notes, seerr=cfg.services()["seerr"],
                       max_batch=MAX_REQUEST_BATCH)
    if "seerr_check" in body:
        row = _row_by_id(body["seerr_check"])
        if not row or not cfg.services()["seerr"]:
            return jsonify(error="Seerr isn't configured (Settings), or the row is gone"), 400
        want = [x for x in (body.get("ids") or []) if isinstance(x, str)][:25]
        try:
            _ids, meta, _n = _row_list_meta(row)
        except ApiError as e:
            return jsonify(error=str(e)), 502
        sc = clients.Seerr(cfg)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as ex:
            res = list(ex.map(lambda tt: _seerr_lookup(sc, tt, meta.get(tt, {})), want))
        return jsonify(results=res)
    if "seerr_request" in body:
        row = _row_by_id(body["seerr_request"])
        if not row or not cfg.services()["seerr"]:
            return jsonify(error="Seerr isn't configured (Settings), or the row is gone"), 400
        want = list(dict.fromkeys(x for x in (body.get("ids") or []) if isinstance(x, str)))
        if not want:
            return jsonify(error="nothing selected"), 400
        if len(want) > MAX_REQUEST_BATCH:
            return jsonify(error=f"request at most {MAX_REQUEST_BATCH} titles at a time"), 400
        try:
            _ids, meta, _n = _row_list_meta(row)
        except ApiError as e:
            return jsonify(error=str(e)), 502
        listed = set(_ids)
        want = [t for t in want if t in listed]          # only titles that are really on this row's lists

        def work(prog):
            sc, out = clients.Seerr(cfg), []
            for n, tt in enumerate(want):
                m = meta.get(tt, {})
                prog(f"Requesting {m.get('title') or tt} ({n + 1} of {len(want)})", 100 * n / len(want))
                lk = _seerr_lookup(sc, tt, m)
                label = f"{lk.get('title') or tt}" + (f" ({lk['year']})" if lk.get("year") else "")
                if lk["status"] in ("available", "requested"):
                    out.append({"imdb_id": tt, "title": label, "ok": False, "detail": "already " + lk["status"]})
                elif lk["status"] != "none":
                    out.append({"imdb_id": tt, "title": label, "ok": False, "detail": lk.get("detail") or lk["status"]})
                else:
                    try:
                        sc.request_title(lk["kind"], lk["tmdb"])
                        out.append({"imdb_id": tt, "title": label, "ok": True, "detail": "requested"})
                        print(f"[seerr] requested {lk['kind']} tmdb:{lk['tmdb']} {label} from list row {row['name']!r}", flush=True)
                    except ApiError as e:
                        out.append({"imdb_id": tt, "title": label, "ok": False, "detail": str(e)})
            return out
        return jsonify(job=_job_view(_start_job(row, "request", work)))
    if "members" in body:
        f, err = rows.validate({**(body["members"] or {}), "name": (body["members"] or {}).get("name") or "x"})
        if err:
            return jsonify(error=err), 400
        rep = DATA["report"]
        try:
            f, _note = _row_ids(f, fetch=True, fresh=extlists.FRESH_PREVIEW)
        except ApiError as e:
            return jsonify(error=str(e)), 502
        mem = rows.select(rep["items"] if rep else [], {**f, "limit": 5000, "sort": "manual"})
        return jsonify(items=[{"id": i["keep_id"], "title": i["title"], "year": i.get("year"), "released": i.get("released"), "type": i["type"]} for i in mem])
    if "publish" in body:
        row = next((r for r in cur if r["id"] == body["publish"]), None)
        if not row:
            return jsonify(error="unknown row"), 404
        if not DATA["report"]:
            return jsonify(error="no report yet - click Refresh data first"), 400
        return jsonify(job=_job_view(_start_job(row)))
    if "sync" in body:
        row = next((r for r in cur if r["id"] == body["sync"]), None)
        if not row:
            return jsonify(error="unknown row"), 404
        try:
            done = sync_row(row)
        except ApiError as e:
            _touch_row(row["id"], str(e))
            return jsonify(error=str(e)), 502
        _touch_row(row["id"], None)
        print(f"[rows] synced {row['name']!r}: {done}", flush=True)
        return jsonify(ok=True, results=done, **_rows_payload())
    with DATA_LOCK:
        if "delete" in body:
            row = next((r for r in cur if r["id"] == body["delete"]), None)
            if row and body.get("remove_collection", True) and cfg.services()["plex"]:
                try:   # take the row off Plex too (the media itself is never touched)
                    p = Plex(cfg)
                    for s in p.sections():
                        if s.get("type") in ("movie", "show"):
                            for c in p.collections(s["key"]):
                                if (c.get("title") or "").casefold() == row["name"].casefold():
                                    p.delete_collection(c["ratingKey"])
                except ApiError as e:
                    return jsonify(error=f"couldn't remove the Plex collection ({e}); the row was kept"), 502
            cur = [r for r in cur if r["id"] != body["delete"]]
        elif "update" in body:
            for r in cur:
                if r["id"] == body["update"]:
                    upd = {k: v for k, v in body.items() if k not in ("update", "keep_csv")}
                    if body.get("keep_csv"):      # the form can't resend uploaded ids, so carry the saved uploads over
                        upd["sources"] = (upd.get("sources") or []) + [x for x in extlists.row_sources(r) if x.get("type") == "csv"]
                    base_row = {k: v for k, v in r.items() if k != "source"} if "sources" in upd else r
                    f, err = rows.validate({**base_row, **upd})
                    if err:
                        return jsonify(error=err), 400
                    r.update(f)
        elif "add" in body:
            f, err = rows.validate(body["add"] or {})
            if err:
                return jsonify(error=err), 400
            if len(cur) >= rows.MAX_ROWS:
                return jsonify(error="too many rows"), 400
            if any(r["name"].casefold() == f["name"].casefold() for r in cur):
                return jsonify(error="a row with that name already exists"), 400
            cur.append(rows.new_row(f))
        else:
            return jsonify(error="nothing to do"), 400
        if not save_rows(cur):
            return jsonify(error="could not save (is /config writable?)"), 500
    return jsonify(_rows_payload())


_poster_keys = {"for": None, "keys": set()}


@app.get("/api/poster/<rk>")
def api_poster(rk):
    rep = DATA["report"]
    if rep is None:
        return "", 404
    if _poster_keys["for"] is not rep:  # rebuild the allow-list only when the report changes
        _poster_keys.update({"for": rep, "keys": {i["plex_key"] for i in rep["items"]}})
    if rk not in _poster_keys["keys"]:  # only items in the report: no arbitrary Plex paths
        return "", 404
    try:
        data, ctype = Plex(cfg).poster(rk)
    except ApiError:
        return "", 404
    if not ctype.startswith("image/"):
        return "", 404
    resp = app.response_class(data, mimetype=ctype.split(";")[0])
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.get("/api/tmdbimg/<name>")
def api_tmdbimg(name):
    """Small TMDB poster for the Missing list (the page's CSP only allows our own origin)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,60}\.(jpg|png)", name):
        return "", 404
    try:
        import requests
        r = requests.get(f"{cfg.tmdb_image_base}/t/p/w92/{name}", timeout=15)
    except Exception:
        return "", 404
    ct = r.headers.get("Content-Type", "")
    if r.status_code != 200 or not ct.startswith("image/"):
        return "", 404
    resp = app.response_class(r.content, mimetype=ct.split(";")[0])
    resp.headers["Cache-Control"] = "private, max-age=604800"
    return resp


_enrich = {"running": False}
_enrich_lock = threading.Lock()
MISS_RETRY = 24 * 3600


def _ekey(e):
    return f"{e.get('time')}|{e.get('id')}"


def _enrich_worker(todo):
    """Fill in IMDb/TMDB ids for log entries written before we recorded them: exact title+year match in Seerr,
    then Seerr's TMDB data for the IMDb id. Results (and misses) are cached in log_ids.json."""
    try:
        sc = clients.Seerr(cfg)
        cache = store.read_json(cfg, "log_ids.json", {})
        for n, e in enumerate(todo, 1):
            rec = {"t": int(time.time())}
            try:
                tmdb, _t, _y = _resolve_tmdb(e)
                rec["tmdb_id"] = str(tmdb)
                imdb = sc.external_ids("movie" if e.get("type") == "movie" else "tv", tmdb).get("imdbId")
                if imdb:
                    rec["imdb_id"] = imdb
            except ApiError as ex:
                print(f"[log-ids] {e.get('title')!r}: {ex}", flush=True)
            cache[_ekey(e)] = rec
            if n % 10 == 0 or n == len(todo):
                store.write_json(cfg, "log_ids.json", cache)
    finally:
        _enrich["running"] = False


_live = {"t": 0, "items": None, "running": False}
LIVE_TTL = 120


def _live_worker():
    try:
        _live["items"] = collector.live_index(cfg)
        _live["t"] = time.time()
    except ApiError as ex:
        print(f"[log] live Plex check failed: {ex}", flush=True)
    finally:
        _live["running"] = False


def _index(items):
    by_ext, by_name = {}, {}
    for i in items:
        for k in ("imdb_id", "tmdb_id"):
            if i.get(k):
                by_ext.setdefault((i["type"], k, str(i[k])), []).append(i)
        by_name.setdefault((i["type"], _norm_title(i["title"]), i.get("year")), []).append(i)
    return by_ext, by_name


def _find(idx, e):
    by_ext, by_name = idx
    t = "movie" if e.get("type") == "movie" else "show"
    cands = []
    for k in ("imdb_id", "tmdb_id"):
        if e.get(k):
            cands += by_ext.get((t, k, str(e[k])), [])
    return cands or by_name.get((t, _norm_title(e.get("title")), e.get("year")), [])


def _mark_restored(entries):
    """Flag deletions whose title is in Plex again. Two sources:
    - a live look at Plex (refreshed in the background, every couple of minutes while History is open): present now and
      deleted over 10 minutes ago (long enough for Plex to have dropped the old record) -> back.
    - the last report, which also knows Radarr/Sonarr state."""
    live_idx = _index(_live["items"]) if _live["items"] is not None else None
    rep = DATA["report"]
    rep_idx = _index(rep["items"]) if rep else None
    now = time.time()
    for e in entries:
        if e.get("dry_run") or not e.get("ok"):
            continue
        if live_idx is not None and e["time"] < now - 600 and _find(live_idx, e):
            e["restored"] = True
            continue
        if not rep_idx:
            continue
        cands = _find(rep_idx, e)
        # Plex can revive its old record when the same file path comes back, keeping the ORIGINAL date added, so any one of:
        #  - removed from Radarr/Sonarr by the deletion and managed by Radarr/Sonarr again
        #  - Plex says it was added after the deletion
        #  - the report was built 30+ minutes after the deletion and still lists it
        arr_removed = any(s.get("step") in ("Radarr", "Sonarr") and s.get("ok") for s in e.get("steps", []))
        gen = rep.get("generated_at", 0)
        if gen > e["time"] and any((arr_removed and c.get("arr")) or (c.get("added_ts") or 0) >= e["time"] - 60
                                   or gen >= e["time"] + 1800 for c in cands):
            e["restored"] = True


@app.get("/api/log")
def api_log():
    entries = store.read_log(cfg, 5000)
    cache = store.read_json(cfg, "log_ids.json", {})
    now, todo = time.time(), []
    for e in entries:
        c = cache.get(_ekey(e))
        if c:
            for k in ("imdb_id", "tmdb_id"):
                if c.get(k) and not e.get(k):
                    e[k] = c[k]
        if not e.get("imdb_id") and not e.get("dry_run") and e.get("ok") and (not c or (not c.get("imdb_id") and now - c.get("t", 0) > MISS_RETRY)):
            todo.append(e)
    _mark_restored(entries)
    if any(not e.get("dry_run") and e.get("ok") for e in entries) and cfg.services()["plex"] and time.time() - _live["t"] > LIVE_TTL:
        with _enrich_lock:
            if not _live["running"]:
                _live["running"] = True
                threading.Thread(target=_live_worker, daemon=True).start()
    pending = 0
    if todo and cfg.services()["seerr"]:
        with _enrich_lock:
            if not _enrich["running"]:
                _enrich["running"] = True
                threading.Thread(target=_enrich_worker, args=(todo,), daemon=True).start()
        pending = len(todo)
    return jsonify(entries=entries, pending=pending, checking=_live["running"] or _live["items"] is None)
