"""End-to-end test against the mock services. Run: python3 tests/e2e.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = "http://127.0.0.1:9100"
PW = "test-password"
procs, failures = [], []


def mkpage(b, *a, **kw):
    """New page with the Advanced-filters drawer pre-opened (it starts collapsed in the UI)."""
    pg = b.new_page(*a, **kw)
    pg.add_init_script("try{ if(!localStorage.getItem('mp-view')) localStorage.setItem('mp-view','table'); }catch(e){}; document.addEventListener('DOMContentLoaded',()=>{const f=document.getElementById('filters'); if(f) f.hidden=false;})")
    return pg


OVERFLOW_JS = """() => {
  const W = document.documentElement.clientWidth, bad = [];
  const scrolls = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true; } return false; };
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el); if (cs.display === 'none' || cs.position === 'fixed') continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    if (r.right > W + 1 && !scrolls(el)) bad.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '')) + ':' + Math.round(r.right));
  }
  return [document.documentElement.scrollWidth > W + 1, bad.slice(0, 6)];
}"""


def mobile_ok(pg, name):
    wide, bad = pg.evaluate(OVERFLOW_JS)
    check(not wide and not bad, f"phone layout: {name} has no horizontal overflow {bad if (wide or bad) else ''}")


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def wait(url, tries=60):
    for _ in range(tries):
        try:
            requests.get(url, timeout=1)
            return
        except requests.RequestException:
            time.sleep(0.25)
    raise SystemExit(f"timeout waiting for {url}")


def start_app(port, dry_run, cfgdir, extra=None):
    env = {**os.environ,
           "PLEX_URL": MOCK + "/plex", "PLEX_TOKEN": "plextoken",
           "TAUTULLI_URL": MOCK + "/tautulli", "TAUTULLI_API_KEY": "tautullikey",
           "RADARR_URL": MOCK + "/radarr", "RADARR_API_KEY": "radarrkey",
           "SONARR_URL": MOCK + "/sonarr", "SONARR_API_KEY": "sonarrkey",
           "SEERR_URL": MOCK + "/seerr", "SEERR_API_KEY": "seerrkey", "IMDB_BASE_URL": MOCK + "/imdb", "MDBLIST_BASE_URL": MOCK + "/mdblist", "TMDB_IMAGE_BASE_URL": MOCK + "/img",
           "APP_PASSWORD": PW, "DRY_RUN": "true" if dry_run else "false", "CONFIG_DIR": cfgdir, **(extra or {})}
    p = subprocess.Popen([sys.executable, "-m", "gunicorn", "-w", "1", "--threads", "4", "-b", f"127.0.0.1:{port}", "app.main:app"],
                         cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    procs.append(p)
    wait(f"http://127.0.0.1:{port}/healthz")
    return f"http://127.0.0.1:{port}"


def session(base):
    s = requests.Session()
    s.headers["X-Requested-With"] = "phlexerr"
    return s


def refresh(s, base):
    s.post(base + "/api/refresh", json={})
    for _ in range(60):
        st = s.get(base + "/api/status").json()
        if st["state"] != "running":
            assert st["state"] == "idle", st
            return
        time.sleep(0.3)
    raise SystemExit("refresh did not finish")



def wait_posters(pg):
    """Poll until every poster image has finished loading (polling instead of wait_for_function, which the app's CSP blocks)."""
    for _ in range(60):
        if pg.evaluate("() => [...document.querySelectorAll('img.poster')].every(i => i.complete)"):
            return
        time.sleep(0.25)


def by_title(items):
    return {i["title"]: i for i in items}


try:
    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_services.py"), "9100"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(mock)
    wait(MOCK + "/__calls")
    d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()

    # ---------------------------------------------------------------- auth
    print("Auth")
    base = start_app(8099, True, d1)
    check(requests.get(base + "/api/items").status_code == 401, "API requires login")
    check(requests.get(base + "/", allow_redirects=False).status_code == 302, "UI redirects to /login")
    check(requests.get(base + "/healthz").text == "ok", "healthz open")
    s = session(base)
    # default password: unset APP_PASSWORD -> "YYZ", flagged; a chosen one is not flagged
    pw_env = {**os.environ, "CONFIG_DIR": tempfile.mkdtemp()}; pw_env.pop("APP_PASSWORD", None)
    pp = subprocess.Popen([sys.executable, "-m", "gunicorn", "-w", "1", "-b", "127.0.0.1:8088", "app.main:app"], cwd=ROOT, env=pw_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    procs.append(pp); wait("http://127.0.0.1:8088/healthz")
    sd = session("http://127.0.0.1:8088")
    check(sd.post("http://127.0.0.1:8088/login", data={"password": "YYZ"}, allow_redirects=False).status_code == 302, "default password YYZ logs in")
    check(sd.get("http://127.0.0.1:8088/api/config").json().get("default_password") is True, "default password is flagged")
    r = s.post(base + "/login", data={"password": "wrong"})
    check(r.status_code == 401, "wrong password rejected")
    r = s.post(base + "/login", data={"password": PW}, allow_redirects=False)
    check(r.status_code == 302 and "session" in s.cookies, "correct password logs in")
    check(s.get(base + "/api/config").json().get("default_password") is False, "custom password is not flagged")
    r = requests.post(base + "/api/delete", json={"ids": ["movie:101"]}, cookies=s.cookies)
    check(r.status_code == 400, "CSRF header required on POST")

    # ---------------------------------------------------------------- report data
    print("Report")
    refresh(s, base)
    d = s.get(base + "/api/items").json()
    items = by_title(d["items"])
    check(len(d["items"]) == 6, f"6 items in report (got {len(d['items'])})")
    check(d["warnings"] == [], f"no warnings with all services up ({d['warnings']})")

    m = items["Old Unwatched Movie"]
    check(m["rating"] == 5.2 and m["rating_src"] == "Radarr", "movie IMDb rating from Radarr")
    check(m["size"] == 8 * 1024 ** 3, "movie size from Radarr")
    check(m["plays_1y"] == 0 and m["plays_total"] == 0 and m["last_viewed_ts"] is None and m["pct"] == 0, "never-watched movie: 0 plays, no last viewed")
    check(m["requested_by"] == ["alice"] and m["seerr_media_id"] == 501, "requested by alice, Seerr media 501")

    m = items["Popular Recent Movie"]
    check(m["plays_1y"] == 2 and m["plays_total"] == 3, f"plays: 2 in past year / 3 total (got {m['plays_1y']}/{m['plays_total']})")
    check(m["pct"] == 100 and m["last_viewed_by"] == "bob", "100% viewed, last by bob")
    check(abs(m["last_viewed_ts"] - (time.time() - 20 * 86400)) < 120, "last viewed ~20 days ago")

    m = items["Half Watched <b>Movie</b>"]
    check(m["pct"] == 45 and m["plays_1y"] == 0 and m["plays_total"] == 1, "half-watched: 45%, 0 plays in year, 1 total")
    check(m["seerr_media_id"] == 509 and m["requested_by"] == [], "Seerr media record without a request is still found (id 509)")

    check(items["Old Unwatched Movie"]["collections"] == ["Test Saga", "Second Collection", "Third Collection"], "Plex collection tags captured")
    check(items["Half Watched <b>Movie</b>"]["collections"] == ["Radarr Only Collection"], "Radarr collection used when Plex has none")
    check(items["Plex Only Movie"]["collections"] == [] and items["Cancelled Show"]["collections"] == [], "no collection -> empty list")

    m = items["Plex Only Movie"]
    check(m["arr"] is None and m["size"] == 3 * 1024 ** 3, "Plex-only movie: size from Plex, not in Radarr")
    check(m["rating"] == 4.4 and m["rating_src"] == "Plex", "Plex-only movie: rating falls back to Plex IMDb entry")

    m = items["Cancelled Show"]
    check(m["pct"] == 25.0, f"show % = distinct eps watched / eps in library = 5/20 (got {m['pct']})")
    check(m["plays_total"] == 6 and m["plays_1y"] == 0, "show plays count episode plays (6 total, 0 this year)")
    check(m["requested_by"] == ["alice", "carol"] and m["seerr_media_id"] == 503, "show requested by 2 users, merged")
    check(m["size"] == 40 * 1024 ** 3 and m["arr"] == "sonarr" and m["arr_id"] == 21, "show size/arr id from Sonarr")

    m = items["Current Show"]
    check(m["pct"] == 80.0 and m["plays_1y"] == 8, "current show: 80% viewed, 8 plays this year")
    check(d["items"][0]["title"] == "Cancelled Show", "default order: biggest first")

    # ---------------------------------------------------------------- settings
    print("Settings")
    r = s.get(base + "/api/settings")
    st = r.json()
    check(st["plex_url"] == MOCK + "/plex" and st["plex_token"] == {"set": True}, "settings: URLs shown, tokens only reported as 'set'")
    check(all(x not in r.text for x in ("plextoken", "radarrkey", "sonarrkey", "seerrkey", "tautullikey")), "settings: no secret ever returned to the browser")
    check(requests.get(base + "/api/settings").status_code == 401, "settings requires login")
    t = lambda svc, vals=None: s.post(base + "/api/settings/test", json={"service": svc, "values": vals or {}}).json()
    check(all(t(x)["ok"] for x in ("plex", "tautulli", "radarr", "sonarr", "seerr")), "test connection OK for all 5 services using saved keys")
    bad = t("radarr", {"radarr_key": "wrongkey"})
    check(bad["ok"] is False and "401" in bad["message"] and "wrongkey" not in bad["message"], f"test with wrong key fails cleanly ({bad['message']})")
    check(t("radarr", {"radarr_url": "http://127.0.0.1:1"})["ok"] is False, "test with unreachable URL fails")
    check(t("radarr", {"radarr_url": "ftp://x"})["ok"] is False, "test rejects non-http URL")
    check(s.post(base + "/api/settings", json={"values": {"plex_url": "javascript:alert(1)"}}).status_code == 400, "save rejects bad URL")
    check(s.post(base + "/api/settings", json={"values": {"evil": "x"}}).status_code == 400, "save rejects unknown setting")
    check(s.post(base + "/api/settings", json={"values": {"radarr_key": "a b"}}).status_code == 400, "save rejects key with whitespace")
    r = s.post(base + "/api/settings", json={"values": {"plex_libraries": "Movies", "radarr_key": ""}})
    check(r.status_code == 200 and s.get(base + "/api/settings").json()["radarr_key"] == {"set": True}, "blank key on save keeps the saved key")
    check(s.get(base + "/api/settings").json()["plex_libraries"] == "Movies", "libraries setting saved")
    refresh(s, base)
    check({i["library"] for i in s.get(base + "/api/items").json()["items"]} == {"Movies"}, "plex_libraries=Movies limits the report to that library")
    s.post(base + "/api/settings", json={"values": {"plex_libraries": ""}})
    refresh(s, base)
    check(len(s.get(base + "/api/items").json()["items"]) == 6, "clearing it restores all libraries")
    mode = oct(os.stat(os.path.join(d1, "settings.json")).st_mode & 0o777)
    check(mode == "0o600", f"settings.json is private ({mode})")
    # a brand-new instance with NO env credentials picks everything up from settings.json
    s.post(base + "/api/settings", json={"values": {"plex_url": MOCK + "/plex", "plex_token": "plextoken", "tautulli_url": MOCK + "/tautulli",
                                                     "tautulli_key": "tautullikey", "radarr_url": MOCK + "/radarr", "radarr_key": "radarrkey",
                                                     "sonarr_url": MOCK + "/sonarr", "sonarr_key": "sonarrkey",
                                                     "seerr_url": MOCK + "/seerr", "seerr_key": "seerrkey"}})
    blank = {k: "" for k in ("PLEX_URL", "PLEX_TOKEN", "TAUTULLI_URL", "TAUTULLI_API_KEY", "RADARR_URL", "RADARR_API_KEY",
                             "SONARR_URL", "SONARR_API_KEY", "SEERR_URL", "SEERR_API_KEY")}
    b2 = start_app(8096, True, d1, blank)
    s4 = session(b2); s4.post(b2 + "/login", data={"password": PW}); refresh(s4, b2)
    check(len(s4.get(b2 + "/api/items").json()["items"]) == 6, "new instance with no env credentials works from saved settings")
    # fresh install: nothing configured
    b3 = start_app(8095, True, tempfile.mkdtemp(), blank)
    s5 = session(b3); s5.post(b3 + "/login", data={"password": PW})
    check(s5.get(b3 + "/api/config").json()["services"]["plex"] is False, "fresh install starts with Plex unconfigured (no crash)")
    s5.post(b3 + "/api/refresh", json={}); time.sleep(1)
    check("Settings" in (s5.get(b3 + "/api/status").json()["error"] or ""), "refresh on fresh install explains to open Settings")
    ro = tempfile.mkdtemp(); os.chmod(ro, 0o500)
    if os.geteuid() != 0:  # root ignores directory permissions
        b4 = start_app(8094, True, ro, blank); s6 = session(b4); s6.post(b4 + "/login", data={"password": PW})
        r = s6.post(b4 + "/api/settings", json={"values": {"plex_url": "http://x"}})
        check(r.status_code == 500 and "chown" in r.json()["error"], "unwritable data folder gives a helpful error")

    # ---------------------------------------------------------------- leaving soon
    print("Leaving Soon")
    bl = start_app(8089, True, tempfile.mkdtemp()); sl = session(bl); sl.post(bl + "/login", data={"password": PW}); refresh(sl, bl)
    calls = lambda: requests.get(MOCK + "/__calls").json()
    check(sl.get(bl + "/api/config").json()["leaving"] is False and sl.get(bl + "/api/leaving").json()["enabled"] is False, "Leaving Soon is OFF by default")
    its = sl.get(bl + "/api/items").json()["items"]
    ids = [i["id"] for i in its if not i["protected"]][:2]
    check(sl.post(bl + "/api/leaving/mark", json={"ids": ids}).status_code == 400, "marking is refused while the feature is off")
    check(all(i.get("leaving_due") is None for i in its) and not any("Leaving Soon" in str(c.get("query")) for c in calls()), "off: no badges and no 'Leaving Soon' row created in Plex")
    check(sl.post(bl + "/api/settings", json={"values": {"leaving_days": "0"}}).status_code == 400 and sl.post(bl + "/api/settings", json={"values": {"leaving_enabled": "maybe"}}).status_code == 400, "bad Leaving Soon settings rejected")
    n0 = len(calls())
    sl.post(bl + "/api/settings", json={"values": {"leaving_enabled": "true", "leaving_name": "Leaving Soon", "leaving_days": "10"}}); time.sleep(1.5)
    check(sl.get(bl + "/api/config").json()["leaving"] is True, "turning it on is saved")
    check(not any("collection-create" in c["what"] and "Leaving Soon" in str(c.get("query")) for c in calls()[n0:]), "turned on but nothing marked: still no row (an empty row is never shown)")
    r = sl.post(bl + "/api/leaving/mark", json={"ids": ids, "days": 7}).json(); time.sleep(1.5)
    check(r["marked"] == 2 and abs(r["due"] - (time.time() + 7 * 86400)) < 120, "marking sets the due date from the days chosen")
    mk = {i["id"]: i for i in sl.get(bl + "/api/items").json()["items"]}
    check(all(mk[i]["leaving_due"] == r["due"] for i in ids), "marked titles carry their due date")
    made = [c for c in calls()[n0:] if c["what"].startswith("collection-create") and c["query"].get("title") == "Leaving Soon"]
    check(len(made) >= 1, "a 'Leaving Soon' collection is created in Plex once something is marked")
    prot_id = next((i["id"] for i in its if i["protected"]), None)
    if prot_id:
        check(sl.post(bl + "/api/leaving/mark", json={"ids": [prot_id]}).json()["skipped_protected"] == 1, "protected titles can't be marked")
    check(sl.post(bl + "/api/leaving/mark", json={"ids": ids, "days": 0}).status_code == 400, "days out of range rejected")
    check(sl.get(bl + "/api/leaving").json()["count"] == 2, "status endpoint counts the marks")
    n1 = len(calls())
    sl.post(bl + "/api/leaving/unmark", json={"ids": ids}); time.sleep(1.5)
    check(sl.get(bl + "/api/leaving").json()["count"] == 0 and any(c["what"].startswith("collection-del") for c in calls()[n1:]), "unmarking everything removes the row from Plex")
    sl.post(bl + "/api/leaving/mark", json={"ids": ids}); time.sleep(1.5); n2 = len(calls())
    sl.post(bl + "/api/settings", json={"values": {"leaving_enabled": "false"}}); time.sleep(1.5)
    check(any(c["what"].startswith("collection-del") for c in calls()[n2:]), "turning the feature off removes the row from Plex Home")
    check(all(i.get("leaving_due") is None for i in sl.get(bl + "/api/items").json()["items"]) and sl.post(bl + "/api/leaving/mark", json={"ids": ids}).status_code == 400, "off again: badges gone and marking refused")
    check("deletions" not in str([c for c in calls() if "delete" in c["what"].lower() and c["service"] in ("radarr", "sonarr")]), "marking never deletes anything in Radarr/Sonarr")

    # ---------------------------------------------------------------- posters
    print("Posters")
    check(requests.get(base + "/api/poster/101").status_code == 401, "poster endpoint requires login")
    r = s.get(base + "/api/poster/101")
    check(r.status_code == 200 and r.headers["Content-Type"] == "image/png" and r.content[:4] == b"\x89PNG", "poster proxied as image/png")
    check("max-age" in r.headers.get("Cache-Control", ""), "poster is browser-cacheable")
    check(s.get(base + "/api/poster/999999").status_code == 404, "key not in report -> 404")
    check(s.get(base + "/api/poster/..%2F..%2Fidentity").status_code == 404, "path tricks -> 404")
    pc = [c for c in requests.get(MOCK + "/__calls").json() if c["what"] == "photo"]
    check(pc and pc[0]["query"]["url"] == "/library/metadata/101/thumb", "Plex asked for the item's thumb with the server-side token")
    check("plextoken" not in r.text and "plextoken" not in json.dumps(dict(r.headers)), "token not exposed to the browser")

    # ---------------------------------------------------------------- info + plex links
    print("Info / Plex links")
    check(requests.get(base + "/api/info/101").status_code == 401, "info endpoint requires login")
    r = s.get(base + "/api/info/101").json()
    check("summary for item 101" in r["summary"] and r["genres"] == ["Drama", "Thriller"] and r["directors"] == ["Zoë Director"]
          and r["cast"] == ["Nobody Famous", "Tom Hanks", "Third Actor"] and r["runtime_min"] == 121 and r["content_rating"] == "PG-13", "info: summary, genres, director, cast, runtime, rating")
    check(s.get(base + "/api/info/201").json()["seasons"] == 3, "info: shows include season count")
    check(s.get(base + "/api/info/999999").status_code == 404 and s.get(base + "/api/info/..%2Fidentity").status_code == 404, "info: only items in the report (404 otherwise)")
    n1 = len([c for c in requests.get(MOCK + "/__calls").json() if c["what"] == "info/101"])
    s.get(base + "/api/info/101"); s.get(base + "/api/info/101")
    n2 = len([c for c in requests.get(MOCK + "/__calls").json() if c["what"] == "info/101"])
    check(n2 == n1, "info is cached server-side (no repeat Plex calls)")
    check(s.get(base + "/api/items").json()["plex_machine_id"] == "MOCKMACHINE", "machine id available for Plex links")
    check(s.get(base + "/api/config").json()["plex_link_base"] == "https://app.plex.tv/desktop", "default Plex link base is app.plex.tv")

    # ---------------------------------------------------------------- dry run
    print("Dry run delete")
    requests.post(MOCK + "/__reset")
    ids = [items["Old Unwatched Movie"]["id"], items["Cancelled Show"]["id"]]
    opts = {"arr": True, "exclusion": True, "seerr": True, "plex_cleanup": True, "plex_delete": False}
    r = s.post(base + "/api/delete", json={"ids": ids, "options": opts}).json()
    check(r["summary"]["dry_run"] and r["summary"]["ok"] == 2, "dry run reports 2 ok")
    check(requests.get(MOCK + "/__calls").json() == [], "dry run made NO mutating calls")
    check(len(s.get(base + "/api/items").json()["items"]) == 6, "dry run leaves report unchanged")
    log = s.get(base + "/api/log").json()["entries"]
    check(len(log) == 2 and all(e["dry_run"] for e in log), "dry run is logged")

    # keep list
    print("Keep list")
    s.post(base + "/api/keep", json={"ids": [items["Old Unwatched Movie"]["id"]], "keep": True})
    flagged = by_title(s.get(base + "/api/items").json()["items"])
    check(flagged["Old Unwatched Movie"]["keep"] is True, "keep flag set")
    r = s.post(base + "/api/delete", json={"ids": [items["Old Unwatched Movie"]["id"]], "options": opts}).json()
    check(r["summary"]["failed"] == 1 and "Keep" in r["results"][0]["steps"][0]["detail"], "kept item is refused")
    s.post(base + "/api/keep", json={"ids": [items["Old Unwatched Movie"]["id"]], "keep": False})

    # branding / PWA
    print("Branding")
    anon = requests.Session()
    for path, ct in (("/static/manifest.webmanifest", "json"), ("/static/icons/icon-512.png", "png"), ("/static/icons/icon-maskable-512.png", "png"),
                     ("/static/icons/favicon.svg", "svg"), ("/static/icons/apple-touch-icon.png", "png")):
        r = anon.get(base + path)
        check(r.status_code == 200 and ct in r.headers.get("Content-Type", ""), f"{path} served without login")
    man = anon.get(base + "/static/manifest.webmanifest").json()
    check(man["display"] == "standalone" and {i["sizes"] for i in man["icons"]} >= {"192x192", "512x512"} and any(i["purpose"] == "maskable" for i in man["icons"]), "manifest has name, standalone display, 192/512 and maskable icons")
    check(anon.get(base + "/favicon.ico", allow_redirects=False).status_code in (301, 302), "/favicon.ico redirects to the icon")
    page = s.get(base + "/").text
    check('rel="manifest"' in page and 'rel="apple-touch-icon"' in page and 'rel="icon"' in page, "page links favicon, apple-touch-icon and manifest")

    # released-before-year filter (UI)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw3:
            br = pw3.chromium.launch(); pg3 = mkpage(br)
            pg3.goto(base + "/login"); pg3.fill("input[name=password]", PW); pg3.click("button[type=submit]")
            pg3.wait_for_selector("#rows tr[data-id]")
            yrs = sorted({i["year"] for i in s.get(base + "/api/items").json()["items"]})
            cut = yrs[1]   # strictly before the second-oldest year -> only the oldest year's items
            exp = len([i for i in s.get(base + "/api/items").json()["items"] if i["year"] < cut])
            pg3.fill("#fYear", str(cut))
            check(pg3.locator("#rows tr[data-id]").count() == exp and exp >= 1, f"'Released before {cut}' shows only older titles ({exp}); the cut-off year itself is excluded")
            pg3.fill("#fYear", str(yrs[0]))
            check(pg3.locator("#rows tr[data-id]").count() == 0, "nothing is released before the oldest year")
            pg3.click("#btnReset")
            check(pg3.input_value("#fYear") == "" and pg3.locator("#rows tr[data-id]").count() >= 1, "Reset filters clears the year filter")
            br.close()
    except ImportError:
        pass

    # never-watched filter (UI)
    print("Never watched")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw2:
            br = pw2.chromium.launch(); pg2 = mkpage(br)
            pg2.goto(base + "/login"); pg2.fill("input[name=password]", PW); pg2.click("button[type=submit]")
            pg2.wait_for_selector("#rows tr[data-id]")
            exp = {i["title"] for i in s.get(base + "/api/items").json()["items"] if not i["last_viewed_ts"] and not i["plays_total"] and not i["pct"]}
            pg2.select_option("#fWatched", "never")
            got = set(pg2.locator("#rows tr[data-id] .t").all_inner_texts())
            check(len(got) == len(exp) and len(exp) >= 1, f"'Never watched' filter shows exactly the unplayed items ({len(exp)})")
            pg2.select_option("#fWatched", "some")
            check(pg2.locator("#rows tr[data-id]").count() == 6 - len(exp), "'Watched at least once' shows the rest")
            br.close()
    except ImportError:
        pass

    # released-on-this-day row
    print("Throwback row")
    coll = lambda: requests.get(MOCK + "/__coll").json()
    tbp = lambda **v: s.post(base + "/api/throwback/preview", json={"values": {k: str(x) for k, x in v.items()}})
    base_v = {"throwback_enabled": "true", "throwback_scope": "day", "throwback_min_years": "10", "throwback_min_rating": "6", "throwback_order": "rating"}
    ttl = lambda r: [t.split(" (")[0] for t in r.json()["titles"]]
    check(ttl(tbp(**base_v)) == ["Popular Recent Movie"], f"same day, 10+ years ago, IMDb 6+ -> only the 12-year-old 8.1 movie ({ttl(tbp(**base_v))})")
    check(ttl(tbp(**{**base_v, "throwback_min_rating": "5"})) == ["Popular Recent Movie", "Old Unwatched Movie"], "lower rating adds the 5.2 movie, highest-rated first")
    check(ttl(tbp(**{**base_v, "throwback_min_rating": "5", "throwback_order": "oldest"})) == ["Old Unwatched Movie", "Popular Recent Movie"], "'oldest release first' order")
    check(ttl(tbp(**{**base_v, "throwback_min_years": "1", "throwback_min_rating": "5"})).count("Half Watched <b>Movie</b>") == 1, "min years 1 admits the 2-year-old movie")
    check(tbp(**{**base_v, "throwback_min_years": "13", "throwback_min_rating": "5"}).json()["count"] == 1, "min years 13 drops the 12-year-old movie")
    check(tbp(**{**base_v, "throwback_max_years": "13", "throwback_min_rating": "5"}).json()["count"] == 1, "max years 13 drops the 15-year-old movie")
    mth = ttl(tbp(**{**base_v, "throwback_scope": "month", "throwback_min_rating": "4"}))
    check("Plex Only Movie" in mth and len(mth) == 3, f"'same month' also finds movies released other days this month ({mth})")
    cw = {**base_v, "throwback_scope": "custom", "throwback_min_rating": "4"}
    check(tbp(**{**cw, "throwback_before": "0", "throwback_after": "0"}).json()["count"] == 2, "custom window 0/0 behaves like 'same day' (min IMDb 4, 10+ years)")
    check(tbp(**{**cw, "throwback_before": "30", "throwback_after": "30"}).json()["count"] == 3, "custom window 30/30 days also pulls in the other-day release this month")
    check(tbp(**{**cw, "throwback_before": "30", "throwback_after": "30", "throwback_limit": "1"}).json()["count"] == 1, "a maximum caps the row")
    allr = tbp(**{**cw, "throwback_before": "30", "throwback_after": "30", "throwback_limit": ""}).json()
    check(allr["count"] == 3 and len(allr["all"]) == 3 and all(a["title"] for a in allr["all"]), "blank maximum = every match, and the full list is available to 'view all'")
    check(tbp(**{**cw, "throwback_before": "999"}).status_code == 400, "window out of range rejected")
    # genres and tags
    gv = {**base_v, "throwback_min_rating": "4", "throwback_min_years": "10", "throwback_scope": "month"}
    check(ttl(tbp(**{**gv, "throwback_genres": "drama"})) == ["Old Unwatched Movie"], "genre filter (case-insensitive) keeps only Drama films that meet the other rules")
    check(sorted(ttl(tbp(**{**gv, "throwback_genres": "Drama, Comedy"}))) == ["Old Unwatched Movie", "Popular Recent Movie"], "several genres = any one of them")
    check(ttl(tbp(**{**gv, "throwback_genres": "Drama, Western", "throwback_genre_mode": "all"})) == ["Old Unwatched Movie"], "'every one of those genres' mode")
    check(ttl(tbp(**{**gv, "throwback_genres": "Drama, Comedy", "throwback_genre_mode": "all"})) == [], "'every genre' with no film in both -> nothing")
    check(sorted(ttl(tbp(**{**gv, "throwback_tags": "classic"}))) == ["Old Unwatched Movie", "Plex Only Movie"], "tag / Plex label filter")
    check(ttl(tbp(**{**gv, "throwback_genres": "Horror", "throwback_tags": "Classic"})) == ["Plex Only Movie"], "genres and tags combine (both must hold)")
    check(ttl(tbp(**{**gv, "throwback_genres": "Dra*"})) == ["Old Unwatched Movie"], "genre wildcards work")
    check(tbp(**{**gv, "throwback_genres": "*"}).status_code == 400, "a lone * is rejected")
    fx = s.get(base + "/api/facets").json()
    check("Drama" in fx["genres"] and "Classic" in fx["tags"], "facets endpoint lists the library's genres and tags for the pickers")
    check(tbp(**{**base_v, "throwback_min_rating": "11"}).status_code == 400 and tbp(throwback_min_years="20", throwback_max_years="5").status_code == 400, "bad rating / years range rejected")
    check(tbp(throwback_scope="century").status_code == 400, "unknown timeframe rejected")
    check(not coll(), "previewing never touches Plex")
    save = s.post(base + "/api/settings", json={"values": {**{k: str(v) for k, v in base_v.items()}, "throwback_name": "Throwback Test", "throwback_min_rating": "5", "throwback_pin": "true"}})
    check(save.status_code == 200 and s.get(base + "/api/settings").json()["throwback_name"] == "Throwback Test", "throwback settings saved and returned")
    def waitc(title, n=30):
        for _ in range(n):
            f = [c for c in coll().values() if c["title"] == title]
            if f:
                return f
            time.sleep(0.3)
        return []
    check(len(waitc("Throwback Test")) == 1, "set and forget: saving the settings publishes the row by itself (no Publish click)")
    pub = s.post(base + "/api/throwback/publish", json={})
    cs = [c for c in coll().values() if c["title"] == "Throwback Test"]
    check(pub.status_code == 200 and len(cs) == 1 and cs[0]["items"] == ["102", "101"] and cs[0]["home"] is True and cs[0].get("sort") == "2", f"published with the chosen name, pinned, custom sort ({cs[0]['items'] if cs else pub.text[:80]})")
    s.post(base + "/api/settings", json={"values": {"throwback_order": "oldest"}})
    s.post(base + "/api/throwback/publish", json={})
    cs = [c for c in coll().values() if c["title"] == "Throwback Test"]
    check(len(cs) == 1 and cs[0]["items"] == ["101", "102"], f"changing the order re-orders the existing collection ({cs[0]['items'] if cs else None})")
    s.post(base + "/api/settings", json={"values": {"throwback_name": "Nostalgia Night"}})
    ren = waitc("Nostalgia Night")
    time.sleep(0.5)
    check(len(ren) == 1 and not [c for c in coll().values() if c["title"] == "Throwback Test"], "renaming the row moves it (old name removed from Plex, no duplicates)")
    s.post(base + "/api/settings", json={"values": {"throwback_enabled": "false"}})
    for _ in range(30):
        if not [c for c in coll().values() if c["title"] == "Nostalgia Night"]:
            break
        time.sleep(0.3)
    check(not [c for c in coll().values() if c["title"] == "Nostalgia Night"], "turning it off removes the row from Plex")

    # custom Plex home rows
    print("Plex Home Screen")
    rw = lambda **b: s.post(base + "/api/rows", json=b)
    coll = lambda: requests.get(MOCK + "/__coll").json()
    check(rw(add={"name": "Everything"}).status_code == 400, "a row with no criteria or filters is rejected (would be the whole library)")
    check(rw(add={"name": "x", "criteria": [{"type": "actor", "pattern": "*"}]}).status_code == 400, "wildcard-only criterion rejected")
    r = rw(add={"name": "Hanks Films", "media": "movie", "criteria": [{"type": "actor", "pattern": "tom hanks"}], "sort": "title", "pin": True})
    check(r.status_code == 200 and r.json()["rows"][0]["matches"] == 2, f"row saved; preview counts its matches ({r.json()['rows'][0]['matches'] if r.ok else r.text})")
    check(rw(add={"name": "hanks films", "criteria": [{"type": "actor", "pattern": "x"}]}).status_code == 400, "duplicate row name rejected")
    pv = rw(preview={"name": "p", "media": "movie", "criteria": [{"type": "studio", "pattern": "Pix*"}]}).json()
    check(pv["count"] == 1, "preview works before saving")
    rid = r.json()["rows"][0]["id"]
    check(not [c for c in coll().values() if c["title"] == "Hanks Films"], "saving a row does not touch Plex until you publish")
    out = rw(sync=rid)
    check(out.status_code == 200, f"publish ok ({out.text[:80]})")
    cs = [c for c in coll().values() if c["title"] == "Hanks Films"]
    check(len(cs) == 1 and sorted(cs[0]["items"]) == ["101", "102"] and cs[0]["section"] == "1", f"collection created in the Movies library with the matching items ({cs[0]['items'] if cs else None})")
    check(cs[0]["home"] is True, "collection pinned to Plex Home")
    check(cs[0]["shared"] is True, "collection also shown on other users' Home screens (default)")
    import sys as _s; _s.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); from app import rows as _rows
    _its = [{"type": "movie", "title": "B", "year": 2001, "rating": 5.0, "added_ts": 300, "released": "2001-05-01", "plays_total": 9, "last_viewed_ts": 50},
            {"type": "movie", "title": "A", "year": 1999, "rating": 8.0, "added_ts": 100, "released": "1999-01-01", "plays_total": 1, "last_viewed_ts": 90},
            {"type": "movie", "title": "C", "year": None, "rating": None, "added_ts": 200, "released": None, "plays_total": 0, "last_viewed_ts": None}]
    def _t(sort, d): return "".join(i["title"] for i in _rows.select(_its, {"media": "both", "criteria": [], "sort": sort, "sort_dir": d, "limit": 50}))
    check(_t("rating", "desc") == "ABC" and _t("rating", "asc") == "BAC", "sort by IMDb rating both directions (unrated last)")
    check(_t("year", "desc") == "BAC" and _t("year", "asc") == "ABC", "sort by year (no year last)")
    check(_t("released", "desc") == "BAC" and _t("added", "desc") == "BCA" and _t("added", "asc") == "ACB", "sort by release date / date added")
    check(_t("plays", "desc") == "BAC" and _t("lastwatched", "desc") == "ABC", "sort by times watched / last watched")
    check(_t("title", "asc") == "ABC" and _t("title", "desc") == "CBA" and _t("newest", None) == "BCA", "title sort + legacy 'newest' still works")
    rw(add={"name": "Second Row", "media": "movie", "criteria": [{"type": "studio", "pattern": "Pix*"}], "shared": False})
    rows_now = rw().json()["rows"] if False else s.get(base + "/api/rows").json()["rows"]
    r2 = next(x for x in rows_now if x["name"] == "Second Row")
    check(r2["shared"] is False and r2["sort"] == "added" and r2["sort_dir"] == "desc", "new rows default to Date added, newest first; shared flag saved")
    rw(sync=r2["id"])
    c2 = next(c for c in coll().values() if c["title"] == "Second Row")
    check(c2["home"] is True and c2["shared"] is False, "'show on other users' Home' off -> only your own Home")
    rw(update=r2["id"], shared=True); rw(sync=r2["id"])
    ids_now = [x["id"] for x in s.get(base + "/api/rows").json()["rows"]]
    cur_order = s.get(base + "/api/rows").json()["order"]
    check("__tb__" in cur_order and len(cur_order) == len(ids_now) + 1, f"order includes the built-in row ({cur_order})")
    o = s.post(base + "/api/rows", json={"order": [r2["id"], rid, "__tb__"]}).json()
    check(o["order"] == [r2["id"], rid, "__tb__"], "row order saved (custom rows and the built-in row)")
    ho = requests.get(MOCK + "/test/home_order").json()
    check(ho.get("1", [])[:2] == ["Second Row", "Hanks Films"], f"order applied to Plex's Home ({ho})")
    rw(delete=r2["id"])
    # external lists
    pv = rw(preview={"name": "L", "source": {"type": "imdb", "list": "https://www.imdb.com/list/ls000000001/?ref_=x"}, "sort": "list"}).json()
    check(pv["count"] == 3 and pv["list_total"] == 4 and pv["titles"][0].startswith("Half Watched"), f"IMDb list preview: only titles in Plex ({pv['count']} of {pv['list_total']}), in list order ({pv['titles']})")
    check(rw(preview={"name": "L", "source": {"type": "imdb", "list": "not a list"}}).status_code == 400, "bad list link rejected")
    check(rw(preview={"name": "L", "source": {"type": "imdb", "list": "ls000000404"}}).status_code == 502, "missing IMDb list -> clear error")
    check(rw(preview={"name": "L", "source": {"type": "imdb", "list": "ls000000500"}}).status_code == 502, "IMDb returning a web page instead of a CSV -> clear error")
    fb = rw(preview={"name": "F", "source": {"type": "imdb", "list": "ls000000403"}, "sort": "list"}).json()
    check(fb["count"] == 2 and fb["titles"][0].startswith("Popular Recent") and "web page" in (fb.get("note") or ""), f"IMDb refuses the CSV -> falls back to the list page and says so ({fb.get('note','')[:70]!r})")
    bad = rw(preview={"name": "F", "source": {"type": "imdb", "list": "ls000000402"}})
    check(bad.status_code == 502 and "blocked" in bad.json()["error"] and "upload the CSV" in bad.json()["error"], f"both IMDb routes blocked -> explains and points to CSV upload ({bad.json()['error'][:90]!r})")
    md = rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "https://mdblist.com/lists/someone/cool-list/?x=1"}], "sort": "list"}).json()
    check(md["count"] == 2 and md["list_total"] == 3 and md["titles"][0].startswith("Half Watched"), f"MDBList list: Plex titles only, in list order ({md.get('titles')})")
    mw = rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "someone/wrapped"}]}).json()
    check(mw["count"] == 2, f"MDBList {{movies, shows}} shape is understood ({mw.get('count')})")
    check(rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "someone/missing"}]}).status_code == 502, "missing MDBList list -> clear error")
    pv_priv = rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "someone/private"}]})
    check(pv_priv.status_code == 502 and "MDBLIST_API_KEY" in pv_priv.json()["error"], "private MDBList list -> explains the API key option")
    check(rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "just text"}]}).status_code == 400, "bad MDBList link rejected")
    mix = rw(preview={"name": "M", "sources": [{"type": "mdblist", "list": "someone/wrapped"}, {"type": "imdb", "list": "ls000000001"}]}).json()
    check(mix["count"] == 4, f"MDBList + IMDb lists combine ({mix.get('count')})")
    csvtxt = "Const,Title\ntt0000102,A\ntt0000101,B\ntt0000555,C\n"
    pc = rw(preview={"name": "C", "source": {"type": "csv", "csv": csvtxt}, "sort": "list", "sort_dir": "asc"}).json()
    check(pc["count"] == 2 and pc["titles"][0].startswith("Popular Recent"), f"CSV upload: matches Plex titles in file order ({pc['titles']})")
    check(rw(preview={"name": "C", "source": {"type": "csv", "csv": "nothing useful"}}).status_code == 400, "CSV with no IMDb ids rejected")
    h0 = requests.get(MOCK + "/test/imdb_hits").json().get("ls000000001", 0)
    for _ in range(3):
        rw(preview={"name": "L", "source": {"type": "imdb", "list": "ls000000001"}, "sort": "list"})
    check(requests.get(MOCK + "/test/imdb_hits").json().get("ls000000001", 0) == h0, "repeated previews (typing in the form) reuse the downloaded list instead of hitting IMDb again")
    check(rw(add={"name": "From list", "source": {"type": "imdb", "list": "ls000000002"}, "sort": "list", "limit": 100}).status_code == 200, "row with only an IMDb list saves (no other criteria needed)")
    lr = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "From list")
    check(lr["sources"] == [{"type": "imdb", "list": "ls000000002"}] and lr["matches"] == 0 and lr.get("list_note"), "page load doesn't download the list (cached copy only)")
    pj = rw(publish=lr["id"]).json()["job"]
    seen, t_end = [], time.time() + 30
    while time.time() < t_end:
        j = s.get(base + f"/api/rows/job/{pj['id']}").json()["job"]
        seen.append((j["pct"], j["stage"]))
        if j["state"] != "running":
            break
        time.sleep(0.02)
    check(j["state"] == "done" and j["pct"] == 100 and j["results"], f"background publish finishes with 100% ({j['state']}, {j.get('error')})")
    pcts = [x[0] for x in seen]
    check(pcts == sorted(pcts) and pcts[0] < 100, f"progress only moves forward ({pcts[:12]})")
    check(rw(publish="nope").status_code == 404 and s.get(base + "/api/rows/job/zzz").status_code == 404, "unknown row / job -> 404")
    sy = rw(sync=lr["id"])
    check(sy.status_code == 200 and any("took" in x.get("action", "") for x in sy.json()["results"]), "publishing downloads the list and syncs (and reports how long each part took)")
    lc = next(c for c in coll().values() if c["title"] == "From list")
    check(lc["items"] == ["103", "101", "102"], f"collection holds the listed titles in list order ({lc['items']})")
    lr = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "From list")
    check(lr["matches"] == 3 and not lr.get("list_note"), "after publishing the saved copy is used for the table")
    rw(delete=lr["id"])
    check(not [c for c in coll().values() if c["title"] == "From list"], "removing the row removes its collection")
    # manual order
    mm = rw(preview={"name": "Chrono", "media": "movie", "criteria": [{"type": "title", "pattern": "Movie"}], "sort": "manual"})
    mem = rw(members={"name": "Chrono", "media": "movie", "criteria": [{"type": "title", "pattern": "Movie"}]}).json()["items"]
    check(len(mem) >= 3 and all(x["id"] and x["title"] for x in mem), f"arrange screen gets the row's titles ({[x['title'] for x in mem]})")
    chosen = [x["id"] for x in reversed(mem)]
    check(rw(add={"name": "Chrono", "media": "movie", "criteria": [{"type": "title", "pattern": "Movie"}], "sort": "manual", "manual_order": chosen}).status_code == 200, "row with a manual order saves")
    cr0 = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "Chrono")
    check(cr0["manual_order"] == chosen and cr0["sort"] == "manual", "manual order is stored with the row")
    mem2 = rw(members={"name": "Chrono", "media": "movie", "criteria": [{"type": "title", "pattern": "Movie"}], "manual_order": chosen}).json()["items"]
    check([x["id"] for x in mem2] == chosen, "arrange screen shows titles in the saved order")
    rw(sync=cr0["id"])
    cc = next(c for c in coll().values() if c["title"] == "Chrono")
    want_rks = [i["plex_key"] for k in chosen for i in s.get(base + "/api/items").json()["items"] if i["keep_id"] == k]
    check(cc["items"] == want_rks, f"Plex collection follows the manual order ({cc['items']} vs {want_rks})")
    # a title that appears later goes to the end of the order, not the front
    rw(update=cr0["id"], manual_order=chosen[:-1])
    cr1 = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "Chrono")
    check(cr1["manual_order"] == chosen[:-1], "manual order can be shortened")
    check(rw(add={"name": "Chrono2", "criteria": [{"type": "title", "pattern": "Movie"}], "sort": "manual", "manual_order": "bad"}).status_code == 400, "bad manual order rejected")
    rw(delete=cr1["id"])
    # missing from Plex + request through Seerr
    wr = rw(add={"name": "Wanted", "sources": [{"type": "mdblist", "list": "someone/wanted"}], "sort": "list", "limit": 100})
    check(wr.status_code == 200, "row from a list saves")
    wid = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "Wanted")["id"]
    ms = rw(missing=wid).json()
    miss_ids = [x["imdb_id"] for x in ms["items"]]
    check(ms["total"] == 7 and ms["in_plex"] == 1 and "tt0000103" not in miss_ids and len(miss_ids) == 6, f"missing view: 7 titles, 1 in Plex, 6 missing ({ms['total']}/{ms['in_plex']})")
    check(ms["items"][0]["title"] == "Wanted Movie" and ms["max_batch"] == 50 and ms["seerr"] is True, "missing view carries titles, the batch cap and Seerr availability")
    ck = {x["imdb_id"]: x for x in rw(seerr_check=wid, ids=miss_ids).json()["results"]}
    check(ck["tt0007001"]["status"] == "none" and ck["tt0007002"]["status"] == "requested" and ck["tt0007003"]["status"] == "available", f"Seerr status: new / already requested / already available ({[ck[k]['status'] for k in sorted(ck)][:4]})")
    check(ck["tt0007004"]["status"] == "none" and ck["tt0007004"]["kind"] == "tv", "shows are looked up as TV")
    check(ck["tt0007005"]["status"] == "unmatched" and ck["tt0007006"]["status"] == "unmatched", "mismatching or type-less titles are 'unmatched', never requested")
    check(rw(seerr_request=wid, ids=["tt%07d" % n for n in range(1, 52)]).status_code == 400, "more than 50 titles per batch refused")
    check(rw(seerr_request=wid, ids=[]).status_code == 400, "nothing selected refused")
    job = rw(seerr_request=wid, ids=["tt0007001", "tt0007002", "tt0007004", "tt0007005", "tt0000999"]).json()["job"]
    t_end = time.time() + 30
    while time.time() < t_end:
        jj = s.get(base + f"/api/rows/job/{job['id']}").json()["job"]
        if jj["state"] != "running":
            break
        time.sleep(0.05)
    res = {x["imdb_id"]: x for x in jj["results"]}
    check(jj["state"] == "done" and res["tt0007001"]["ok"] and res["tt0007004"]["ok"], f"request job ran ({jj['state']}, {jj.get('error')})")
    check(not res["tt0007002"]["ok"] and "already" in res["tt0007002"]["detail"], "already-requested title skipped")
    check(not res["tt0007005"]["ok"] and "tt0000999" not in res, "unmatched titles skipped; ids not on the row's lists are ignored")
    sent = [c for c in requests.get(MOCK + "/__calls").json() if c.get("service") == "seerr" and c.get("what") == "request"]
    check(sorted((c["query"]["mediaType"], c["query"]["mediaId"]) for c in sent[-2:]) == [("movie", 7001), ("tv", 7004)], f"exactly the two new titles were requested in Seerr ({[c['query'] for c in sent[-2:]]})")
    rw(delete=wid)
    # several lists combined
    two = {"name": "Combined", "sources": [{"type": "csv", "csvs": ["Const\ntt0000101\n", "Const\ntt0000102\ntt0000101\n"]}, {"type": "imdb", "list": "ls000000004"}], "sort": "list", "limit": 100}
    pm = rw(preview=two).json()
    check(pm["count"] == 3 and pm["list_total"] == 4, f"several lists are combined and de-duplicated ({pm['count']} in Plex, {pm['list_total']} unique ids)")
    check(rw(add={**two, "name": "Combined"}).status_code == 200, "row with several lists saves")
    cr = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "Combined")
    check(len(cr["sources"]) == 3 and cr["sources"][0] == {"type": "csv", "count": 1} and "ids" not in str(cr["sources"]), f"saved sources shown without the raw ids ({cr['sources']})")
    check(rw(update=cr["id"], sources=[{"type": "imdb", "list": "ls000000005"}], keep_csv=True).status_code == 200, "editing keeps previously uploaded CSVs when asked")
    cr = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "Combined")
    check(len(cr["sources"]) == 3 and cr["sources"][-1]["type"] == "csv", "uploaded CSVs survived the edit")
    check(rw(update=cr["id"], sources=[]).status_code == 400, "removing every list from a list-only row is refused (it would match the whole library)")
    rw(delete=cr["id"])
    tbp = s.get(base + "/api/rows").json()["throwback"]
    check("enabled" in tbp and "name" in tbp, "rows payload includes the built-in Released on this day row")
    rw(update=rid, criteria=[{"type": "actor", "pattern": "tom hanks", "top": 1}], pin=False)
    rw(sync=rid)
    cs = [c for c in coll().values() if c["title"] == "Hanks Films"]
    check(len(cs) == 1 and cs[0]["items"] == ["102"] and cs[0]["home"] is False, "re-publish replaces contents (top-billed only) and unpins")
    pubs = [x for x in s.get(base + "/api/rows").json()["rows"] if x["id"] == rid][0]
    check(pubs["published"]["error"] is None, "last publish time recorded")
    rw(delete=rid)
    check(not [c for c in coll().values() if c["title"] == "Hanks Films"] and s.get(base + "/api/rows").json()["rows"] == [], "removing a row removes its Plex collection too")
    check(not any(c["what"].startswith("metadata/") and c["method"] == "DELETE" for c in requests.get(MOCK + "/__calls").json() if c["service"] == "plex"), "no media was deleted from Plex by any of this")

    # re-request API guards
    rj = lambda path="", **b: s.post(base + "/api/rerequest" + path, json=b)
    check(rj(time=1, id="movie:nope").status_code == 404, "re-request refuses entries that aren't in the deletion log")
    check(rj("/lookup", time=1, id="movie:nope").status_code == 404, "lookup refuses unknown entries")

    # protection rules
    print("Protection rules")
    def add(**f):
        return s.post(base + "/api/rules", json={"add": f})
    def prot():
        return {i["title"]: i["protected"] for i in s.get(base + "/api/items").json()["items"]}
    check(add(type="title", pattern="*").status_code == 400, "wildcard-only rule rejected")
    check(add(type="bogus", pattern="x").status_code == 400, "unknown rule type rejected")
    check(add(type="actor", pattern="tom hanks", top="2").status_code == 200, "actor rule added (top 2 billed)")
    check(add(type="actor", pattern="Tom Hanks", top="2").status_code == 400, "duplicate rule rejected")
    p = prot()
    check(bool(p["Old Unwatched Movie"]) and bool(p["Popular Recent Movie"]), "actor match is case-insensitive and honors top-billed")
    check(not p["Half Watched <b>Movie</b>"], "unrelated item not protected")
    add(type="director", pattern="zoe director"); add(type="studio", pattern="pix*"); add(type="collection", pattern="*saga")
    add(type="title", pattern="plex only"); add(type="title", pattern="Cancelled ?how")
    p = prot()
    check(any("Director" in r for r in p["Old Unwatched Movie"]), "director rule is accent-insensitive")
    check(any("Studio" in r for r in p["Popular Recent Movie"]), "studio wildcard matches")
    check(any("Collection" in r for r in p["Popular Recent Movie"]), "collection wildcard matches")
    r1 = s.get(base + "/api/items"); et = r1.headers.get("ETag")
    check(bool(et) and s.get(base + "/api/items", headers={"If-None-Match": et}).status_code == 304, "items endpoint answers 304 when nothing changed")
    check(r1.headers.get("Content-Encoding") == "gzip" or "gzip" not in r1.request.headers.get("Accept-Encoding", ""), "large JSON is gzip-compressed")
    s.post(base + "/api/rules", json={"add": {"type": "title", "pattern": "zzzcachetest"}})
    check(s.get(base + "/api/items").headers.get("ETag") != et, "changing rules invalidates the items cache")
    a = s.get(base + "/api/activity").json()
    check("Direct Play (MKV" not in str(a) and a["sessions"][1]["container"] == "Direct Play (MP4)" and a["summary"]["streams"] == 2 and a["sessions"][0]["subtitles"].startswith("Burn"), "API: /api/activity gives Tautulli-style stream details")
    check(s.get(base + "/api/plexthumb", params={"img": "/library/metadata/102/thumb/1"}).headers.get("Content-Type", "").startswith("image/") and s.get(base + "/api/plexthumb", params={"img": "http://evil/x"}).status_code == 404, "API: /api/plexthumb proxies posters and rejects other URLs")
    check(a["sessions"][1]["avatar"].startswith("https://plex.tv/users/") and s.get(base + "/api/plexthumb", params={"img": a["sessions"][1]["avatar"], "avatar": "1"}).headers.get("Content-Type", "").startswith("image/"), "API: avatar falls back to Tautulli's user list and proxies")
    check(len(a["sessions"]) == 2 and a["sessions"][0]["user"] == "bob" and "ip_address" not in a["sessions"][0], "API: /api/activity returns streams")
    check(any(x["se"] == "S1 · E3" for x in a["sessions"] if x["type"] == "episode") and all(x["se"] == "" for x in a["sessions"] if x["type"] != "episode"), "API: now-playing episodes carry season and episode")
    pl = s.get(base + "/api/plays", params={"user": "alice", "type": "episode", "length": 3}).json()
    check(pl["total"] >= 5 and len(pl["plays"]) == 3 and all(p["user"] == "alice" for p in pl["plays"]) and "alice" in pl["users"], "API: /api/plays filters and pages")
    check(s.get(base + "/api/plays", params={"q": "zzzznomatch"}).json()["total"] == 0, "API: /api/plays search")
    for x in s.get(base + "/api/rules").json()["rules"]:
        if x["pattern"] == "zzzcachetest":
            s.post(base + "/api/rules", json={"delete": x["id"]})
    libs = s.get(base + "/api/facets").json()["libraries"]
    check("Movies" in libs, f"facets list libraries {libs}")
    lp = s.post(base + "/api/rules/preview", json={"type": "library", "pattern": "Movies"}).json()
    check(lp["count"] >= 1 and all(t for t in lp["titles"]), f"library rule previews its items ({lp['count']})")
    import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import rules as _r
    _m = _r.matcher({"type": "collection", "pattern": "Alien"})
    check(_m({"collections": ["Alien Collection"]}) and _m({"collections": ["alien"]}) and not _m({"collections": ["Aliens Collection"]}) and not _m({"collections": ["Alien Resurrection Saga"]}), "collection rule 'Alien' matches 'Alien Collection' but not look-alikes")
    check(p["Plex Only Movie"] and p["Cancelled Show"], "title keyword and ? wildcard match")
    check(not p["Current Show"], "non-matching show stays unprotected")
    ids = [items[t]["id"] for t in ("Old Unwatched Movie", "Plex Only Movie", "Half Watched <b>Movie</b>")]
    r = s.post(base + "/api/delete", json={"ids": ids, "options": opts}).json()
    st = {x["title"]: x for x in r["results"]}
    check(r["summary"]["ok"] == 1 and not st["Old Unwatched Movie"]["ok"] and not st["Plex Only Movie"]["ok"], "server refuses rule-protected items")
    check("protected" in st["Old Unwatched Movie"]["steps"][0]["detail"], "refusal explains why")
    rules = s.get("%s/api/rules" % base).json()["rules"]
    check(all(x["matches"] >= 1 for x in rules), "rule match counts reported")
    pay = s.get("%s/api/rules" % base).json()
    check(all(x["size"] > 0 for x in pay["rules"]), "each rule reports its disk size")
    sm = pay["summary"]
    lo = requests.post(base + "/api/logout")      # no CSRF header / JSON body: must be refused
    check(lo.status_code in (400, 401), f"logout refuses cross-site style POST ({lo.status_code})")
    check(sm["size"] > 0 and sm["items"] >= 1 and sm["size"] <= sm["total_size"] and sm["size"] <= sm["rule_size"] + sm["keep_size"], f"protection scorecard totals ({sm['size']})")
    pv = s.post(base + "/api/rules/preview", json={"type": "studio", "pattern": "Indie*"}).json()
    check(pv["count"] == 1, "preview counts matches without saving")
    sp = s.post(base + "/api/rules/preview", json={"id": rules[0]["id"]}).json()
    check(sp["count"] == rules[0]["matches"] and len(sp["titles"]) == sp["count"], "saved-rule preview lists every protected title")
    exp = sum(i["size"] for i in s.get(base + "/api/items").json()["items"] if i["title"] in " ".join(sp["titles"]))
    check(sp["total_size"] > 0 and len(sp["sizes"]) == len(sp["titles"]) and sum(sp["sizes"]) == sp["total_size"], f"preview reports total disk size ({sp['total_size']})")
    check(s.post(base + "/api/rules/preview", json={"id": "nope"}).status_code == 404, "preview of unknown rule id -> 404")
    for x in rules:
        s.post(base + "/api/rules", json={"update": x["id"], "enabled": False})
    check(all(not v for v in prot().values()), "disabled rules protect nothing")
    for x in rules:
        s.post(base + "/api/rules", json={"delete": x["id"]})
    check(s.get(base + "/api/rules").json()["rules"] == [], "rules deleted")

    # ---------------------------------------------------------------- UI screenshot (dry-run instance)
    print("UI")
    try:
        from playwright.sync_api import sync_playwright
        shots = os.environ.get("SHOT_DIR", tempfile.mkdtemp())
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = mkpage(b, viewport={"width": 1440, "height": 900}, color_scheme="dark")
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "400 (BAD REQUEST)" not in m.text else None)
            pg.goto(base + "/login")
            pg.fill("input[name=password]", PW)
            pg.click("button[type=submit]")
            pg.wait_for_selector("#rows tr[data-id]")
            check(pg.locator("#rows tr[data-id]").count() == 6, "UI renders 6 rows")
            check("Half Watched <b>Movie</b>" in pg.inner_text("#rows"), "UI escapes HTML in titles (literal text, not markup)")
            check(pg.locator("#rows b").count() == 0, "no injected <b> element in table")
            pg2 = mkpage(b, ); pg2.goto(base + "/login"); pg2.fill("input[name=password]", PW); pg2.click("button[type=submit]"); pg2.wait_for_selector("#rows tr[data-id]")
            pg2.click("#logoutBtn"); pg2.wait_for_url("**/login")
            check("/login" in pg2.url, "UI: Sign out returns to the login page"); pg2.close()
            # larger posters
            wait_posters(pg)
            pw_, ph_ = pg.evaluate("() => { const r = document.querySelector('img.poster').getBoundingClientRect(); return [r.width, r.height]; }")
            check(pw_ >= 80 and ph_ >= 120, f"posters are larger (rendered {pw_:.0f}x{ph_:.0f})")
            # click-through to Plex
            first = pg.locator("#rows tr[data-id]").first
            href = first.locator("a.plink").first.get_attribute("href")
            check(href.startswith("https://app.plex.tv/desktop/#!/server/MOCKMACHINE/details?key=%2Flibrary%2Fmetadata%2F"), f"title/poster link to Plex web ({href[:70]}…)")
            check(first.locator("a.plink").count() == 2 and first.locator("a.plink").first.get_attribute("target") == "_blank"
                  and "noopener" in first.locator("a.plink").first.get_attribute("rel"), "poster + title are links opening in a new tab")
            sel0 = pg.inner_text("#selCount") if pg.is_visible("#selbar") else "0"
            first.locator("button.info").click()
            check((pg.inner_text("#selCount") if pg.is_visible("#selbar") else "0") == sel0, "clicking (i) does not toggle row selection")
            pg.wait_for_selector("#infoPop:not([hidden]) .sum")
            pop_txt = pg.inner_text("#infoPop")
            check("summary for item" in pop_txt and "drama" in pop_txt.lower() and ("Zoë Director" in pop_txt or "Jane Director" in pop_txt or "Pete Docter" in pop_txt) and "2h 1m" in pop_txt, "info popover shows description, genres, director, runtime")
            check("<b>bold</b>" in pop_txt, "popover escapes HTML from Plex text (shown literally)")
            check(pg.locator("#infoPop a.btn").first.get_attribute("href").startswith("https://app.plex.tv/desktop/#!/server/MOCKMACHINE"), "popover has Open in Plex button")
            imdb_href = pg.locator("#infoPop a.imdbBtn").get_attribute("href")
            check(imdb_href.startswith("https://www.imdb.com/") and pg.locator("#infoPop a.imdbBtn").get_attribute("target") == "_blank", f"popover has IMDb button next to Open in Plex ({imdb_href})")
            check(pg.locator("#infoPop.pinned").count() == 1, "click pins the popover")
            pg.screenshot(path=os.path.join(shots, "ui_info.png"))
            # theme toggle + mobile
            bg0 = pg.evaluate("getComputedStyle(document.body).backgroundColor")
            pg.click("#btnTheme")
            th = pg.evaluate("document.documentElement.getAttribute('data-theme')")
            bg1 = pg.evaluate("getComputedStyle(document.body).backgroundColor")
            check(th in ("light", "dark") and bg0 != bg1, f"UI: theme toggle switches colors ({th})")
            pg.reload(); pg.wait_for_selector("#rows tr[data-id]")
            check(pg.evaluate("document.documentElement.getAttribute('data-theme')") == th, "UI: theme choice is remembered after reload")
            pg.screenshot(path=os.path.join(shots, f"ui_theme_{th}.png"))
            pg.click("#btnTheme")
            pg.set_viewport_size({"width": 390, "height": 800}); pg.reload(); pg.wait_for_selector("#rows tr[data-id]")
            check(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "UI: no horizontal page scroll at phone width")
            check(not pg.is_visible("#tbl thead") and pg.is_visible("#mSort"), "UI: phone shows cards + sort dropdown instead of table header")
            pg.click("#btnFilters"); pg.wait_for_selector("#filters", state="visible")
            check(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "UI: expanded filters fit phone width")
            pg.screenshot(path=os.path.join(shots, "ui_mobile_filters.png"))
            pg.click("#btnFilters"); pg.wait_for_selector("#filters", state="hidden")
            pg.screenshot(path=os.path.join(shots, "ui_mobile.png"))
            pg.click("#rows tr[data-id] .rowchk"); pg.wait_for_selector("#selbar:not([hidden])")
            check(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "UI: selection bar fits phone width")
            pg.screenshot(path=os.path.join(shots, "ui_mobile_sel.png"))
            pg.click("#btnClearSel")
            for tab in ("#tabProt", "#tabHist"):
                pg.click(tab)
                check(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"UI: {tab} fits phone width")
            pg.set_viewport_size({"width": 1440, "height": 900}); pg.goto(base + "/"); pg.wait_for_selector("#rows tr[data-id]")
            # default sort: date added, newest first
            pg.goto(base + "/"); pg.wait_for_selector("#rows tr[data-id]")
            check(pg.inner_text("th[data-sort=added_ts] .arrow").strip() == "▼", "Candidates default to Date added, newest first")
            # protection tab
            pg.click("#tabProt"); pg.wait_for_selector("#rForm")
            pg.select_option("#rType", "studio"); pg.fill("#rPattern", "Pix*")
            pg.wait_for_selector("#rPreview b")
            check("1" in pg.inner_text("#rPreview"), "UI: live preview shows how many items a rule protects")
            pg.click("#rForm button[type=submit]"); pg.wait_for_selector("#rList tr")
            check("Pix*" in pg.inner_text("#rList"), "UI: rule appears in the list")
            pg.select_option("#rType", "library")
            check(pg.is_visible("#rLib") and not pg.is_visible("#rPattern") and pg.locator("#rLib option").count() >= 1, "UI: Library rule shows a dropdown picker")
            pg.select_option("#rType", "studio")
            check(pg.is_visible("#rPattern") and not pg.is_visible("#rLib"), "UI: other rule types go back to the text box")
            check("DISK SPACE PROTECTED" in pg.inner_text("#pStats").upper() and "GB" in pg.inner_text("#pStats"), "UI: protection scorecard shows protected disk space " + repr(pg.inner_text("#pStats")))
            check(" GB" in pg.locator("#rList tr[data-rid]").first.inner_text(), "UI: disk size visible in each rule row without clicking Preview")
            pg.click("th[data-psort=size]")
            sz = [float(t.split()[0]) for t in pg.locator("#rList tr[data-rid] td:nth-child(5)").all_inner_texts()]
            check(sz == sorted(sz, reverse=True) and "▼" in pg.inner_text("th[data-psort=size]"), f"UI: protection table sorts by Disk (desc) {sz}")
            pg.click("th[data-psort=size]")
            sz = [float(t.split()[0]) for t in pg.locator("#rList tr[data-rid] td:nth-child(5)").all_inner_texts()]
            check(sz == sorted(sz), "UI: second click reverses the sort")
            pg.click("th[data-psort=pattern]")
            pt = [t.lower() for t in pg.locator("#rList tr[data-rid] td:nth-child(3) b").all_inner_texts()]
            check(pt == sorted(pt), "UI: protection table sorts by Rule name")
            pg.locator("#rList button.rPrev").first.click(); pg.wait_for_selector("#rList .rPrevBox li")
            check(pg.locator("#rList .rPrevBox li").count() >= 1, "UI: per-rule Preview lists protected titles")
            check(" on disk" in pg.inner_text("#rList .rPrevBox"), "UI: per-rule Preview shows total disk size")
            pg.locator("#rList button.rPrev").first.click()
            check(pg.locator("#rList .rPrevRow").count() == 0, "UI: Preview toggles closed")
            pg.fill("#rPattern", "*"); pg.click("#rForm button[type=submit]")
            pg.wait_for_selector("#rErr:not(:empty)")
            check("everything" in pg.inner_text("#rErr"), "UI: dangerous wildcard-only rule shows an error")
            pg.screenshot(path=os.path.join(shots, "ui_protect.png"))
            pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(shots, "m_protect_data.png"), full_page=True); mobile_ok(pg, "Protect with rules")
            pg.set_viewport_size({"width": 1440, "height": 900})
            s.post(base + "/api/rows", json={"add": {"name": "UI Order Row", "media": "movie", "criteria": [{"type": "studio", "pattern": "Pix*"}]}})
            pg.click("#tabRows"); pg.wait_for_selector("#wList tr[data-tb]")
            check("Released on this day" in pg.inner_text("#wList tr[data-tb]") or "this day" in pg.inner_text("#wList tr[data-tb]").lower(), "UI: built-in 'Released on this day' row is listed on the Plex Home Screen tab")
            check(pg.locator("#wList tr[data-tb] .tbSet").count() == 1, "UI: it links to its Settings")
            # row from an IMDb list through the UI
            pg.evaluate("() => { document.getElementById('wAdvanced').open = true; }"); pg.fill("#wName", "UI List Row"); pg.fill("#wListUrl", "https://www.imdb.com/list/ls000000003/")
            pg.select_option("#wSort", "list")
            pg.wait_for_selector("#wPreview b")
            check("the list has 4" in pg.inner_text("#wPreview") and "Matches 3 items" in pg.inner_text("#wPreview"), f"UI: list preview says how many are in Plex ({pg.inner_text('#wPreview')[:90]!r})")
            pg.fill("#wListUrl", "https://www.imdb.com/list/ls000000003/\nhttps://www.imdb.com/list/ls000000006/, ls000000007")
            pg.wait_for_selector("#wPreview b")
            pg.fill("#wListUrl", "https://mdblist.com/lists/someone/cool-list\nhttps://www.imdb.com/list/ls000000003/")
            pg.wait_for_selector("#wPreview b")
            pg.click("#wSave"); pg.wait_for_selector("#wList tr:has-text('UI List Row')")
            check("MDBList someone/cool-list + IMDb list ls000000003" in pg.inner_text("#wList"), "UI: an MDBList link and an IMDb link in the same box are each recognised")
            pg.locator("#wList tr:has-text('UI List Row') .wEdit").click()
            check("https://mdblist.com/lists/someone/cool-list" in pg.input_value("#wListUrl"), "UI: editing shows the MDBList link again")
            pg.click("#wSave"); pg.wait_for_selector("#rNotice:not([hidden])")
            pg.locator("#wList tr:has-text('UI List Row') .wEdit").click()
            pg.fill("#wListUrl", "https://www.imdb.com/list/ls000000003/\nhttps://www.imdb.com/list/ls000000006/, ls000000007")
            pg.click("#wSave"); pg.wait_for_selector("#wList tr:has-text('ls000000006')")
            check("ls000000003 + IMDb list ls000000006 + IMDb list ls000000007" in pg.inner_text("#wList"), "UI: several lists (new lines / commas) saved on one row")
            check("IMDb list ls000000003" in pg.inner_text("#wList"), "UI: saved row shows its IMDb lists")
            pg.locator("#wList tr:has-text('UI List Row') .wDel").click(); pg.locator("#wList tr:has-text('UI List Row') .wDel").click()
            pg.wait_for_selector("#wList tr:has-text('UI List Row')", state="detached")
            # manual order editor through the UI
            pg.fill("#wName", "UI Manual"); pg.select_option("#wSort", "manual")
            pg.fill("#wListUrl", "")
            pg.select_option("#wType", "title"); pg.fill("#wPattern", "Movie"); pg.click("#wAddCrit")
            check(pg.is_visible("#wArrange"), "UI: 'My own order' shows the Arrange button")
            pg.click("#wArrange"); pg.wait_for_selector("#dlgOrder[open] #oList li")
            ttl0 = pg.locator("#oList li .t").all_inner_texts()
            check(len(ttl0) >= 3, f"UI: arrange screen lists the row's titles ({len(ttl0)})")
            pg.locator("#oList li").nth(1).locator(".oUp").click()
            ttl1 = pg.locator("#oList li .t").all_inner_texts()
            check(ttl1[0] == ttl0[1] and ttl1[1] == ttl0[0], "UI: ↑ moves a title up")
            pg.locator("#oList li").nth(0).locator(".opos").fill("3"); pg.locator("#oList li").nth(0).locator(".opos").dispatch_event("change")
            ttl2 = pg.locator("#oList li .t").all_inner_texts()
            check(ttl2[2] == ttl1[0], "UI: typing a position moves the title there")
            pg.fill("#oFilter", ttl2[0].split(" (")[0][:6])
            check(pg.locator("#oList li:not([hidden])").count() >= 1, "UI: filter box narrows the list")
            pg.fill("#oFilter", "")
            pg.click("#oSave"); pg.wait_for_selector("#dlgOrder", state="hidden")
            check("placed" in pg.inner_text("#wArrange"), "UI: the order is kept in the form ('N placed')")
            pg.click("#wSave"); pg.wait_for_selector("#wList tr:has-text('UI Manual')")
            saved = next(x for x in s.get(base + "/api/rows").json()["rows"] if x["name"] == "UI Manual")
            check(len(saved["manual_order"]) == len(ttl0) and saved["sort"] == "manual", "UI: saved row carries the manual order")
            pg.locator("#wList tr:has-text('UI Manual') .wDel").click(); pg.locator("#wList tr:has-text('UI Manual') .wDel").click()
            pg.wait_for_selector("#wList tr:has-text('UI Manual')", state="detached")
            # request titles missing from Plex, through the UI
            requests.post(MOCK + "/test/reset_seerr")
            pg.fill("#wName", "UI Wanted"); pg.select_option("#wSort", "list"); pg.fill("#wListUrl", "https://mdblist.com/lists/someone/wanted")
            pg.click("#wSave"); pg.wait_for_selector("#wList tr:has-text('UI Wanted')")
            pg.locator("#wList tr:has-text('UI Wanted') .wMiss").click()
            pg.wait_for_selector("#mRows tr")
            pg.wait_for_selector("#mCheck:has-text('can be requested')", timeout=20000)
            check("not." in pg.inner_text("#mSummary") and "7 titles" in pg.inner_text("#mSummary"), f"UI: missing screen summary ({pg.inner_text('#mSummary')!r})")
            check(pg.locator("#mRows input.mSel:not([disabled])").count() == 2 and pg.locator("#mRows input.mSel[disabled]").count() == 4, "UI: only titles Seerr doesn't have yet can be ticked")
            pg.wait_for_timeout(500)
            check(pg.locator("#mRows img.mthumb").count() == 1 and pg.evaluate("document.querySelector('#mRows img.mthumb').naturalWidth") > 0, "UI: Missing list shows a poster thumbnail")
            check("Already requested" in pg.inner_text("#mRows") and "Already available" in pg.inner_text("#mRows") and "Can't match" in pg.inner_text("#mRows"), "UI: Seerr status shown for every title")
            titles_in_order = [t.split("\n")[0].split(" (")[0].replace(" Movie", " Movie").strip() for t in pg.locator("#mRows tr td:nth-child(2)").all_inner_texts()]
            check(titles_in_order[:5] == ["Wanted Show", "Wanted Movie", "Already Requested Movie", "Available Movie", "Odd Match"] and titles_in_order[-1].startswith("No Type"), f"UI: default sort is release date, newest first, undated last ({titles_in_order})")
            rtxt = pg.inner_text("#mRows tr:has-text('Wanted Movie')") + "|" + pg.inner_text("#mRows tr:has-text('Wanted Show')")
            check("7.4" in rtxt and "IMDb" in rtxt and "8.2" in rtxt and "TMDB" in rtxt, f"UI: rating shown next to each title (list's IMDb rating, else Seerr's TMDB) ({rtxt!r})")
            pg.select_option("#mvDir", "asc")
            check(pg.locator("#mRows tr td:nth-child(2)").first.inner_text().startswith("Odd Match"), "UI: direction flips the sort")
            pg.select_option("#mvDir", "desc"); pg.select_option("#mvSort", "rating")
            check(pg.locator("#mRows tr td:nth-child(2)").first.inner_text().startswith("Wanted Show"), "UI: sort by rating (highest first)")
            pg.select_option("#mvSort", "title"); pg.select_option("#mvDir", "asc")
            check(pg.locator("#mRows tr td:nth-child(2)").first.inner_text().startswith("Already Requested"), "UI: sort by title")
            pg.select_option("#mvSort", "released"); pg.select_option("#mvDir", "desc")
            pg.select_option("#mStatus", "none")
            check(pg.locator("#mRows tr").count() == 2, "UI: filter 'can be requested' -> 2")
            pg.select_option("#mStatus", "")
            pg.fill("#mRate", "7")
            check(pg.locator("#mRows tr").count() == 2, "UI: rating filter (at least 7) -> 2")
            pg.fill("#mRate", "")
            pg.select_option("#mType", "show")
            check(pg.locator("#mRows tr").count() == 1 and "Wanted Show" in pg.inner_text("#mRows"), "UI: type filter (shows) -> 1")
            pg.select_option("#mType", "")
            pg.fill("#mQ", "avail")
            check(pg.locator("#mRows tr").count() == 1, "UI: text search")
            pg.fill("#mQ", "")
            check(pg.is_disabled("#mRequest"), "UI: request button is off until something is selected")
            pg.check("#mAll")
            check(pg.locator("#mRows input.mSel:checked").count() == 2 and "Request 2 in Seerr" in pg.inner_text("#mRequest"), "UI: select-all ticks only requestable titles")
            n_before = len([c for c in requests.get(MOCK + "/__calls").json() if c.get("service") == "seerr" and c.get("what") == "request"])
            pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(shots, "m_missing.png")); mobile_ok(pg, "Missing popup")
            check(pg.evaluate("() => { const b=document.querySelector('#dlgMissing .ft').getBoundingClientRect(); return b.bottom <= innerHeight && b.top >= 0; }"), "phone layout: Missing popup buttons are on screen")
            pg.set_viewport_size({"width": 1440, "height": 900})
            pg.click("#mRequest"); pg.wait_for_selector("#mResult:has-text('2 requested in Seerr')", timeout=20000)
            n_after = len([c for c in requests.get(MOCK + "/__calls").json() if c.get("service") == "seerr" and c.get("what") == "request"])
            check(n_after - n_before == 2, "UI: requesting sent exactly the 2 selected titles to Seerr")
            check(pg.locator("#mRows input.mSel:not([disabled])").count() == 0, "UI: requested titles now show as already requested")
            pg.click("#mClose")
            pg.locator("#wList tr:has-text('UI Wanted') .wDel").click(); pg.locator("#wList tr:has-text('UI Wanted') .wDel").click()
            pg.wait_for_selector("#wList tr:has-text('UI Wanted')", state="detached")
            names0 = pg.locator("#wList tr td:nth-child(2) b").all_inner_texts()
            check(len(names0) >= 2 and pg.locator("#wList tr .wDown:not([disabled])").count() >= 1, f"UI: reorder arrows are enabled when there are 2+ rows ({names0})")
            pg.locator("#wList tr").first.locator(".wDown").click()
            pg.wait_for_selector("#rNotice:has-text('Order saved')")
            names1 = pg.locator("#wList tr td:nth-child(2) b").all_inner_texts()
            check(names1[0] == names0[1] and names1[1] == names0[0], f"UI: clicking ↓ moves the first row down ({names0} -> {names1})")
            pg.locator("#wList tr:has-text('UI Order Row') .wPub").click()
            pg.wait_for_selector("#rNotice:has-text('Published “UI Order Row” in')", timeout=20000)
            check(pg.is_hidden("#wProg") and "synced" in pg.inner_text("#wList tr:has-text('UI Order Row')"), "UI: Publish runs in the background, shows the result and the row's synced status")
            pg.click("#tabMain"); pg.wait_for_selector("#rows tr[data-id]")
            pg.uncheck("#fHideKeep")
            lock = pg.locator("#rows tr.kept")
            check(lock.count() >= 1 and lock.first.locator(".rowchk").is_disabled(), "UI: rule-protected row shows a lock badge and a disabled checkbox")
            pg.check("#fHideKeep")
            check(pg.locator("#rows tr.kept").count() == 0, "UI: 'Hide protected items' hides them")
            for rid in [x["id"] for x in s.get(base + "/api/rules").json()["rules"]]:
                s.post(base + "/api/rules", json={"delete": rid})
            pg.goto(base + "/"); pg.wait_for_selector("#rows tr[data-id]")
            pg.wait_for_function("() => !document.getElementById('actBadge').hidden")
            check(pg.inner_text("#actBadge") == "2", "UI: Activity tab shows a bubble with the stream count from any tab")
            pg.screenshot(path=os.path.join(shots, "badge.png"), clip={"x": 0, "y": 0, "width": 1400, "height": 140})
            pg.click("#tabAct"); pg.wait_for_selector("#aNow .nowitem")
            check(pg.locator("#aNow .nowitem").count() == 2 and "Current Show" in pg.inner_text("#aNow"), "UI: Activity shows the live streams")
            check(pg.locator("#aRows tr").count() >= 5 and pg.locator("#aUser option").count() >= 4 and pg.locator("#aTbl th").count() == 12 and pg.locator("#aRows .wcirc").count() >= 5, "UI: Activity lists recent plays in the Tautulli-style table")
            pg.click("#aNow .stopbtn >> nth=0"); pg.fill("#stopMsg", "Server restarting"); pg.click("#stopGo"); pg.wait_for_timeout(500)
            check(not pg.is_visible("#dlgStop") and any(c["what"] == "11|Server restarting" for c in requests.get(MOCK + "/__calls").json()), "UI: Stop stream dialog sends the message to Tautulli and closes")
            w0 = pg.evaluate("() => document.querySelector('#aNow .nowbar span').style.width"); pg.wait_for_timeout(2200)
            check(pg.evaluate("() => document.querySelector('#aNow .nowclk').textContent") != "0:00 / 0:00" and pg.evaluate("() => document.querySelector('#aNow .nowbar span').style.width") != w0, "UI: Activity progress advances on its own (no refresh)")
            pg.select_option("#aUser", "bob"); pg.wait_for_timeout(600)
            check(pg.locator("#aRows tr").count() > 0 and all("bob" in t for t in pg.locator("#aRows tr td:nth-child(2)").all_inner_texts()), "UI: Activity user filter works")
            pg.screenshot(path=os.path.join(shots, "activity.png")); pg.locator("#aTbl").screenshot(path=os.path.join(shots, "act_table.png"))
            pg.click("#tabHelp")
            check(pg.is_visible("#viewHelp") and not pg.is_visible("#viewMain"), "UI: Help tab opens")
            pg.fill("#helpQ", "mdblist")
            check(pg.locator("#viewHelp .hs:visible").count() >= 2 and pg.locator("#viewHelp .hs:visible").count() < 9, "UI: Help search narrows the sections")
            pg.fill("#helpQ", "zzzznothing")
            check(pg.is_visible("#helpNone"), "UI: Help search shows 'nothing matches'")
            pg.fill("#helpQ", ""); pg.click("#tabMain")
            pg.keyboard.press("Escape")
            check(pg.locator("#infoPop").is_hidden(), "Escape closes the popover")
            pg.locator("#rows tr[data-id]").nth(1).locator("button.info").hover()
            pg.wait_for_selector("#infoPop:not([hidden]) .sum")
            check(pg.locator("#infoPop.pinned").count() == 0, "hover shows popover without pinning")
            pg.mouse.move(5, 5)
            pg.wait_for_selector("#infoPop", state="hidden")
            check(True, "popover hides when the mouse leaves")
            pg.locator("#rows tr[data-id]").first.locator("button.info").click()
            pg.wait_for_selector("#infoPop.pinned")
            pg.click("header.top h1")
            check(pg.locator("#infoPop").is_hidden(), "clicking elsewhere closes a pinned popover")
            wait_posters(pg)
            check(pg.evaluate("[...document.querySelectorAll('img.poster')].every(i => i.naturalWidth > 0)"), "all 6 posters loaded in the browser")
            # sorting
            pg.click("th[data-sort=title]")
            first = pg.locator("#rows tr[data-id] .t").first.inner_text()
            check(first.startswith("Cancelled Show"), f"sort by title asc -> Cancelled Show first (got {first!r})")
            pg.click("th[data-sort=last_viewed_ts]")
            first = pg.locator("#rows tr[data-id] .t").first.inner_text()
            check("Never" in pg.locator("#rows tr[data-id]").first.inner_text(), "sort by last viewed asc puts never-viewed first")
            # filters
            pg.click("[data-preset=never]")
            n = pg.locator("#rows tr[data-id]").count()
            titles = pg.inner_text("#rows")
            check(n == 2 and "Old Unwatched Movie" in titles and "Plex Only Movie" in titles, f"'never watched, 1y+' preset -> 2 items (got {n})")
            # automated-download filter: recently added + nobody requested it + not watched lately
            import time as _t
            api_items = s.get(base + "/api/items").json()["items"]
            def _exp(new_days, stale_days):
                now = _t.time(); out = set()
                for i in api_items:
                    if i["protected"] or i["requested_by"] or not i["added_ts"] or i["added_ts"] < now - new_days * 86400: continue
                    if i["last_viewed_ts"] and i["last_viewed_ts"] > now - stale_days * 86400: continue
                    out.add(i["title"])
                return out
            pg.click("[data-preset=auto]")
            check(pg.input_value("#fNew") == "30" and pg.input_value("#fStale") == "14" and pg.input_value("#fUser") == "__none", "'unrequested downloads' preset fills editable filters")
            for nd, sd in ((30, 14), (36500, 14), (36500, 0), (1, 14)):
                pg.fill("#fNew", str(nd)); pg.fill("#fStale", str(sd))
                got = set(t.split("\n")[0].strip() for t in pg.locator("#rows tr[data-id] .t").all_inner_texts())
                want = _exp(nd, sd)
                check(len(got) == len(want) and all(any(w in g for g in got) for w in want), f"auto-download filter added<={nd}d, unwatched {sd}d -> {len(want)} items (got {len(got)})")
            pg.click("#btnReset")
            check(pg.input_value("#fNew") == "", "Reset clears the new 'added within' filter")
            pg.fill("#fQ", "show")
            check(pg.locator("#rows tr[data-id]").count() == 2, "text search 'show' -> 2")
            pg.fill("#fQ", "")
            pg.select_option("#fUser", "carol")
            check(pg.locator("#rows tr[data-id]").count() == 1, "requested-by filter carol -> 1")
            pg.select_option("#fUser", "")
            # IMDb filter must not match unrated items; separate 'Unrated' filter controls them
            pg.click("#btnReset")
            pg.evaluate("() => { S.items.find(i => i.title === 'Old Unwatched Movie').rating = null; applyFilters(); }")
            pg.fill("#fRating", "5")
            check(pg.locator("#rows tr[data-id]").count() == 1 and "Plex Only Movie" in pg.inner_text("#rows"),
                  "IMDb ≤ 5 shows only rated items (unrated '-' excluded)")
            pg.fill("#fRating", "")
            pg.select_option("#fUnrated", "only")
            check(pg.locator("#rows tr[data-id]").count() == 1 and "Old Unwatched Movie" in pg.inner_text("#rows"), "Unrated filter 'Only unrated' -> just the unrated item")
            pg.select_option("#fUnrated", "hide")
            check(pg.locator("#rows tr[data-id]").count() == 5, "Unrated filter 'Hide' -> 5 rated items")
            pg.select_option("#fUnrated", "")
            check(pg.locator("#rows tr[data-id]").count() == 6, "Unrated filter 'Include' (default) -> all 6, no threshold set")
            pg.click("#btnReset")
            # collections
            check(pg.locator("#rows .badge.coll").count() == 5, f"collection badges shown (2 + '+1' + 1 + 1) (got {pg.locator('#rows .badge.coll').count()})")
            pg.select_option("#fColl", "Test Saga")
            check(pg.locator("#rows tr[data-id]").count() == 2, "filter by collection 'Test Saga' -> 2")
            pg.select_option("#fColl", "__none")
            check(pg.locator("#rows tr[data-id]").count() == 3, "filter 'not in a collection' -> 3")
            pg.select_option("#fColl", "__any")
            check(pg.locator("#rows tr[data-id]").count() == 3, "filter 'in any collection' -> 3")
            pg.select_option("#fColl", "")
            # settings dialog
            pg.click("#btnSettings")
            pg.wait_for_selector("#dlgSettings[open] #s_plex_url")
            check(pg.is_visible("#s_tb_name") and pg.input_value("#s_tb_name") == "Nostalgia Night" and pg.input_value("#s_tb_min") == "10", "Settings shows the saved 'released on this day' values")
            pg.check("#s_tb_enabled"); pg.select_option("#s_tb_scope", "month"); pg.fill("#s_tb_rating", "4"); pg.click("#tbPreview")
            pg.wait_for_selector("#tbRes.ok")
            check("movie" in pg.inner_text("#tbRes"), f"Preview today lists matches ({pg.inner_text('#tbRes')[:70]})")
            check(not pg.is_visible("#s_tb_before"), "custom-window inputs are hidden until 'Custom window' is chosen")
            pg.select_option("#s_tb_scope", "custom")
            check(pg.is_visible("#s_tb_before") and pg.is_visible("#s_tb_after"), "choosing 'Custom window' reveals the days before / after inputs")
            pg.fill("#s_tb_before", "40"); pg.fill("#s_tb_after", "40"); pg.fill("#s_tb_limit", ""); pg.fill("#s_tb_rating", "0"); pg.fill("#s_tb_min", "0")
            pg.click("#tbPreview"); pg.wait_for_selector("#tbRes.ok")
            check("movie" in pg.inner_text("#tbRes"), "preview works with a custom window and no maximum")
            pg.uncheck("#s_tb_enabled")
            check(pg.get_attribute("#s_radarr_key", "type") == "password" and pg.input_value("#s_radarr_key") == "", "settings dialog: key fields are password inputs and empty")
            check("Saved - leave blank" in pg.get_attribute("#s_radarr_key", "placeholder"), "settings dialog: placeholder says key is saved")
            pg.click("[data-test=radarr]")
            pg.wait_for_selector("#t_radarr.ok")
            check("5.mock" in pg.inner_text("#t_radarr"), "settings dialog: Test connection shows success")
            pg.fill("#s_sonarr_key", "nope")
            pg.click("[data-test=sonarr]")
            pg.wait_for_selector("#t_sonarr.bad")
            check("401" in pg.inner_text("#t_sonarr"), "settings dialog: bad key shows failure")
            pg.screenshot(path=os.path.join(shots, "ui_settings.png"))
            pg.click("#setCancel")
            check(pg.locator("#zoomIn, #zoomOut").count() == 0, "text size buttons removed")
            check(not pg.is_visible("#modeChip") or "LIVE" not in pg.inner_text("#modeChip"), "no LIVE banner")
            # selection + shift range
            pg.click("#btnReset")
            pg.click("th[data-sort=size]")  # asc then
            pg.locator("#rows tr[data-id] .rowchk").nth(0).click()
            pg.locator("#rows tr[data-id] .rowchk").nth(2).click(modifiers=["Shift"])
            check(pg.inner_text("#selCount") == "3", "shift-click selects a range of 3")
            pg.click("#chkAll")
            check(pg.inner_text("#selCount") == "6", "select-all selects the view")
            pg.screenshot(path=os.path.join(shots, "ui_main.png"))
            # delete modal in dry-run
            pg.click("#btnDelete")
            pg.wait_for_selector("#dlgDelete[open]")
            check("Dry-run mode is ON" in pg.inner_text("#delBody"), "dry-run notice in modal")
            pg.screenshot(path=os.path.join(shots, "ui_modal.png"))
            pg.click("#delGo")
            pg.wait_for_selector("#delDone")
            pg.screenshot(path=os.path.join(shots, "ui_result.png"))
            check("would be removed" in pg.inner_text("#delBody"), "dry-run result shown")
            pg.click("#delDone")
            # history in dry-run instance: no real deletions -> zero stats, dry runs still browsable
            pg.click("#tabHist")
            pg.wait_for_selector("#hStats .stat")
            check("nothing deleted yet" in pg.inner_text("#hStats") and "0 GB" in pg.inner_text("#hStats"), "history with only dry runs: zero space freed")
            pg.select_option("#hMode", "dry")
            check(pg.locator("#hRows tr").count() > 0, "dry runs listed under 'Dry runs'")
            pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(shots, "m_history_data.png"), full_page=True); mobile_ok(pg, "History with entries")
            pg.set_viewport_size({"width": 1440, "height": 900})
            pg.click("#tabMain")
            # light + mobile
            pg2 = mkpage(b, viewport={"width": 390, "height": 800}, color_scheme="light")
            pg2.goto(base + "/login"); pg2.fill("input[name=password]", PW); pg2.click("button[type=submit]")
            pg2.wait_for_selector("#rows tr[data-id]")
            pg2.screenshot(path=os.path.join(shots, "ui_mobile.png"))
            check(not errs, f"no JS console errors ({errs[:2]})")
            # redesigned filter bar: drawer starts closed, toggles, presets show pills that can be removed
            pgx = b.new_page(); xerrs = []
            pgx.on("pageerror", lambda e: xerrs.append(str(e)))
            pgx.goto(base + "/login"); pgx.fill("input[name=password]", PW); pgx.click("button[type=submit]"); pgx.wait_for_selector("#rows tr[data-id]")
            check(pgx.locator("#tbl.cardview").count() == 1 and not pgx.is_visible("#tbl thead"), "UI: Prune screen defaults to card view")
            check(pgx.locator("#tabMain").inner_text().strip() == "Prune" and pgx.locator("#tabProt").inner_text().strip() == "Protect", "UI: tabs are named Prune and Protect")
            cw = pgx.evaluate("() => { const t=[...document.querySelectorAll('#rows tr[data-id]')].slice(0,2).map(r=>Math.round(r.getBoundingClientRect().left)); return t; }")
            pgx.set_viewport_size({"width": 1700, "height": 900}); pgx.wait_for_timeout(200)
            tops = pgx.evaluate("() => [...document.querySelectorAll('#rows tr[data-id]')].map(r => Math.round(r.getBoundingClientRect().top))")
            check(len(set(tops)) < len(tops), f"UI: cards sit side by side on a wide screen ({tops})")
            pgx.locator("#rows tr[data-id] .rowchk:not([disabled])").first.check()
            check(pgx.locator("#rows tr.sel").count() == 1 and pgx.is_visible("#selbar"), "UI: selecting a card works")
            pgx.click("#chkAll2"); check(pgx.locator("#rows tr.sel").count() >= 2, "UI: card view has its own select-all")
            pgx.click("#chkAll2")
            pgx.click("#vTable"); check(pgx.is_visible("#tbl thead") and pgx.evaluate("() => localStorage.getItem('mp-view')") == "table", "UI: Table button switches layout and remembers it")
            pgx.click("#vCards")
            pgx.set_viewport_size({"width": 1700, "height": 1000}); pgx.wait_for_timeout(300)
            pgx.screenshot(path=os.path.join(shots, "cards_wide.png"))
            pgx.click("#tabProt"); pgx.wait_for_timeout(500); pgx.screenshot(path=os.path.join(shots, "protect_spacing.png"))
            pgx.click("#tabHelp"); pgx.fill("#helpQ", "plex"); pgx.wait_for_timeout(200); pgx.screenshot(path=os.path.join(shots, "help_hl.png"))
            pgx.fill("#helpQ", ""); pgx.click("#tabMain")
            pgx.set_viewport_size({"width": 1200, "height": 560}); pgx.click("#btnSettings"); pgx.wait_for_selector("#dlgSettings[open]")
            sc = pgx.evaluate("() => { const d=document.getElementById('dlgSettings'), b=d.querySelector('.bd'); return [d.scrollHeight - d.clientHeight, b.scrollHeight > b.clientHeight]; }")
            check(sc[0] <= 1 and sc[1], f"UI: popup scrolls inside its body only (no second scrollbar) {sc}")
            pgx.click("#setCancel"); pgx.set_viewport_size({"width": 1440, "height": 900})
            # ---- phone audit of every view and popup ----
            pgx.set_viewport_size({"width": 390, "height": 800}); pgx.wait_for_timeout(200)
            for tab, nm in (("#tabMain", "Prune (cards)"), ("#tabAct", "Activity"), ("#tabProt", "Protect"), ("#tabRows", "Plex Home Screen"), ("#tabHist", "History"), ("#tabHelp", "Help")):
                pgx.click(tab); pgx.wait_for_timeout(500)
                pgx.screenshot(path=os.path.join(shots, "m_" + nm.split()[0].lower() + ".png"), full_page=True)
                mobile_ok(pgx, nm)
            for w in (390, 600, 900, 1440):
                pgx.set_viewport_size({"width": w, "height": 900}); pgx.click("#tabMain"); pgx.click("#vCards"); pgx.wait_for_timeout(250)
                geo = pgx.evaluate("""() => {
                  const tr = document.querySelector('#rows tr[data-id]'), R = (e) => e.getBoundingClientRect();
                  const po = R(tr.querySelector('.poster')), t = R(tr.querySelector('.title-cell .t')), cb = R(tr.querySelector('.rowchk')), im = R(tr.querySelector('td[data-l="IMDb"]'));
                  const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
                  return [hit(po, t), hit(po, cb), hit(po, im), hit(cb, t), Math.round(po.width)];
                }""")
                check(not any(geo[:4]), f"card view at {w}px: poster, title, checkbox and facts do not overlap {geo}")
            pgx.set_viewport_size({"width": 390, "height": 800}); pgx.wait_for_timeout(200)
            pgx.click("#tabMain"); pgx.screenshot(path=os.path.join(shots, "m_cards.png"))
            pgx.click("#tabMain"); pgx.click("#btnFilters"); pgx.wait_for_timeout(200)
            pgx.screenshot(path=os.path.join(shots, "m_filters.png"), full_page=True); mobile_ok(pgx, "Prune with filters open")
            pgx.click("#btnFilters"); pgx.click("#vTable"); pgx.wait_for_timeout(200); mobile_ok(pgx, "Prune (table view)"); pgx.click("#vCards")
            pgx.locator("#rows tr[data-id] .rowchk:not([disabled])").first.check(); pgx.click("#btnDelete"); pgx.wait_for_selector("#dlgDelete[open]")
            pgx.screenshot(path=os.path.join(shots, "m_delete.png")); mobile_ok(pgx, "delete popup")
            check(pgx.evaluate("() => { const b=document.querySelector('#dlgDelete .ft').getBoundingClientRect(); return b.bottom <= innerHeight && b.top >= 0; }"), "phone layout: delete popup buttons are on screen")
            pgx.click("#delCancel"); pgx.click("#btnClearSel")
            pgx.click("#btnSettings"); pgx.wait_for_selector("#dlgSettings[open]"); pgx.screenshot(path=os.path.join(shots, "m_settings.png")); mobile_ok(pgx, "settings popup")
            check(pgx.evaluate("() => { const b=document.querySelector('#dlgSettings .ft').getBoundingClientRect(); return b.bottom <= innerHeight && b.top >= 0; }"), "phone layout: settings buttons are on screen")
            pgx.click("#setCancel"); pgx.set_viewport_size({"width": 1440, "height": 900})
            check(not pgx.is_visible("#filters"), "UI: advanced filters start collapsed")
            pgx.click("#btnAdvanced"); check(pgx.is_visible("#filters"), "UI: Advanced filters button opens the drawer")
            pgx.fill("#fQ", "show"); check(pgx.locator("#filterPills .filter-pill").count() == 1, "UI: an active filter shows as a pill")
            pgx.click("#filterPills .filter-pill button")
            pgx.click("[data-preset=never]"); check(pgx.locator("#filterPills .filter-pill").count() >= 1, "UI: quick filter preset shows its pills")
            pgx.click("#btnReset")
            # the Candidates sort headers must be unaffected by History's sortable headers (they once crashed render())
            pgx.click("#vTable"); pgx.click("#tbl th[data-sort=title]"); check("▲" in pgx.inner_text("#tbl th[data-sort=title]"), "UI: Candidates header sort works")
            pgx.click("#tabHist"); pgx.wait_for_selector("#hEmpty:visible, #hRows tr")
            pgx.click("#hTbl th[data-sort=size]"); pgx.click("#hTbl th[data-sort=title]")
            check("▲" in pgx.inner_text("#hTbl th[data-sort=title]"), "UI: History column sort shows its arrow")
            pgx.click("#tabHelp"); pgx.click("#helpToc a[href='#help-faq']")
            check(pgx.is_visible("#viewHelp") and pgx.evaluate("() => document.getElementById('help-faq').open"), "UI: Help contents link stays on Help and opens the section")
            pgx.click("#tabMain"); pgx.wait_for_selector("#rows tr[data-id]")
            check(not xerrs, f"no JS errors across Candidates/History switching ({xerrs[:2]})"); pgx.close()
            # Leaving Soon in the UI (own instance): hidden while off, usable when on
            pgl = mkpage(b); lerrs = []; pgl.on("pageerror", lambda e: lerrs.append(str(e)))
            pgl.goto(bl + "/login"); pgl.fill("input[name=password]", PW); pgl.click("button[type=submit]"); pgl.wait_for_selector("#rows tr[data-id]")
            check(not pgl.is_visible("[data-preset=leaving]") and not pgl.is_visible("#fLeave"), "UI: Leaving Soon controls are hidden while the feature is off")
            pgl.click("#rows tr[data-id] .rowchk >> nth=0")
            check(not pgl.is_visible("#btnLeave"), "UI: no 'Leaving soon' button while off")
            sl.post(bl + "/api/settings", json={"values": {"leaving_enabled": "true"}}); time.sleep(1)
            pgl.reload(); pgl.wait_for_selector("#rows tr[data-id]")
            check(pgl.is_visible("[data-preset=leaving]") and pgl.is_visible("[data-preset=leaving_due]"), "UI: Leaving Soon quick filters appear once it is on")
            pgl.click("#rows tr[data-id] .rowchk >> nth=0"); pgl.click("#btnLeave"); pgl.wait_for_selector(".badge.leave")
            check("leaving" in pgl.inner_text(".badge.leave >> nth=0").lower() and pgl.is_visible("#toast.show"), "UI: marking shows the ⏳ badge and a confirmation")
            pgl.click("#rows tr[data-id] .rowchk >> nth=0")
            check(pgl.is_visible("#btnUnleave"), "UI: 'Not leaving' is offered for a marked title")
            pgl.screenshot(path=os.path.join(shots, "leaving.png"))
            # Request tab: search Seerr and request a title from inside Phlexerr
            requests.post(MOCK + "/__reset")
            pgl.click("#tabReq"); pgl.fill("#reqQ", "Dune"); pgl.wait_for_selector(".reqcard .reqbtn")
            check("Dune" in pgl.inner_text(".reqcard") and pgl.is_visible(".reqcard .reqbtn"), "UI: Request tab shows search results with a Request button")
            pgl.screenshot(path=os.path.join(shots, "request.png"))
            pgl.click(".reqcard .reqbtn >> nth=0"); pgl.wait_for_selector(".reqcard .badge:text('Requested')")
            rc = [c for c in requests.get(MOCK + "/__calls").json() if c.get("what") == "request"]
            check(len(rc) == 1 and rc[0]["query"].get("mediaType") == "movie" and rc[0]["query"].get("mediaId") == 5, f"UI: Request button sends the request to Seerr ({rc})")
            r = sl.post(bl + "/api/seerr/request", json={"type": "bogus", "tmdb_id": 5}); check(r.status_code == 400, "request API rejects a bad type")
            r = sl.post(bl + "/api/seerr/request", json={"type": "movie", "tmdb_id": "x"}); check(r.status_code == 400, "request API rejects a bad id")
            check(sl.get(bl + "/api/seerr/search?q=a").json() == {"results": []}, "search ignores one-letter queries")
            pgl.click("#tabMain")
            pgl.click("#btnSettings") if pgl.locator("#btnSettings").count() else pgl.click("text=Settings")
            pgl.wait_for_selector("#s_lv_enabled")
            check(pgl.is_checked("#s_lv_enabled") and pgl.input_value("#s_lv_name") == "Leaving Soon", "UI: Settings has the Leaving Soon section")
            check(not lerrs, f"no JS errors with Leaving Soon ({lerrs[:2]})"); pgl.close()
            sl.post(bl + "/api/settings", json={"values": {"leaving_enabled": "false"}})
            b.close()
        print("  screenshots in", shots)
    except ImportError:
        print("  (playwright not available, UI test skipped)")

    # ---------------------------------------------------------------- live instance
    print("Live delete")
    base2 = start_app(8098, False, d2)
    s2 = session(base2)
    s2.post(base2 + "/login", data={"password": PW})
    refresh(s2, base2)
    items2 = by_title(s2.get(base2 + "/api/items").json()["items"])
    requests.post(MOCK + "/__reset")
    pick = [items2[t]["id"] for t in ("Old Unwatched Movie", "Popular Recent Movie", "Cancelled Show", "Plex Only Movie")]
    opts = {"arr": True, "exclusion": True, "seerr": True, "plex_cleanup": True, "plex_delete": True}
    r = s2.post(base2 + "/api/delete", json={"ids": pick, "options": opts}).json()
    res = {x["title"]: x for x in r["results"]}
    calls = requests.get(MOCK + "/__calls").json()
    what = {(c["service"], c["what"]): c for c in calls}
    check(("radarr", "movie/11") in what and what[("radarr", "movie/11")]["query"] == {"deleteFiles": "true", "addImportExclusion": "true"},
          "Radarr delete with deleteFiles=true & addImportExclusion=true")
    check(("sonarr", "series/21") in what and what[("sonarr", "series/21")]["query"] == {"deleteFiles": "true", "addImportListExclusion": "true"},
          "Sonarr delete with deleteFiles=true & addImportListExclusion=true")
    check(("seerr", "media/501") in what and ("seerr", "media/503") in what, "Seerr media removed for movie + show")
    check(("plex", "metadata/104") in what, "Plex-only movie deleted through Plex API")
    check(res["Popular Recent Movie"]["ok"] and any(not st["ok"] for st in res["Popular Recent Movie"]["steps"]),
          "Seerr failure is reported but item still counts as removed")
    check(("seerr", "media/502") not in what, "(Seerr 502 was simulated as failing)")
    check(r["summary"]["ok"] == 4 and r["summary"]["bytes"] == (8 + 5 + 40 + 3) * 1024 ** 3, "summary: 4 ok, 56 GB")
    time.sleep(8.5)  # background Plex cleanup waits ~5s before emptying trash
    calls = requests.get(MOCK + "/__calls").json()
    check(any(c["what"] == "refresh/1" for c in calls) and any(c["what"] == "refresh/2" for c in calls), "Plex scan triggered on both libraries")
    check(any(c["what"] == "emptyTrash/1" for c in calls) and any(c["what"] == "emptyTrash/2" for c in calls), "Plex trash emptied after scan")
    left = s2.get(base2 + "/api/items").json()["items"]
    check(len(left) == 2, f"deleted items removed from the report (left {len(left)})")
    log = s2.get(base2 + "/api/log").json()["entries"]
    check(len([e for e in log if not e["dry_run"]]) == 4, "live deletions logged")

    ent = [e for e in s2.get(base2 + "/api/log").json()["entries"] if not e["dry_run"]]
    check(all(e.get("type") in ("movie", "show") and e.get("library") for e in ent), "live log entries record type + library")

    # History screen with real stats (4 live deletions = 56 GB: 3 movies, 1 show)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = mkpage(b, viewport={"width": 1440, "height": 900}, color_scheme="dark")
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            pg.goto(base2 + "/login"); pg.fill("input[name=password]", PW); pg.click("button[type=submit]")
            pg.wait_for_selector("#rows tr[data-id]")
            # live mode: a checkbox (not typing a word) confirms deletion
            pg.uncheck("#fHideKeep"); pg.locator("#rows tr[data-id] .rowchk:not([disabled])").first.check(); pg.click("#btnDelete")
            pg.wait_for_selector("#dlgDelete[open] #oConfirm")
            check(pg.get_attribute("#oConfirm", "type") == "checkbox" and pg.is_disabled("#delGo"), "delete needs a tick-box, and the Delete button is disabled until it's ticked")
            pg.check("#oConfirm")
            check(pg.is_enabled("#delGo"), "ticking the box enables Delete")
            pg.uncheck("#oConfirm")
            check(pg.is_disabled("#delGo"), "un-ticking disables it again")
            pg.click("#delCancel"); pg.wait_for_selector("#dlgDelete", state="hidden")
            pg.click("#tabHist")
            pg.wait_for_selector("#hStats .stat")
            check(pg.is_visible("#viewHist") and not pg.is_visible("#viewMain") and not pg.is_visible("#selbar"), "History is its own screen (candidates + selection bar hidden)")
            check(pg.evaluate("location.hash") == "#history", "History has its own URL (#history)")
            stats = pg.inner_text("#hStats")
            check("56.0 GB" in stats, f"stats: total space freed = 56.0 GB ({stats[:60]!r})")
            check("3 movies · 1 show" in stats, "stats: 3 movies + 1 show deleted")
            check(pg.locator("#hChart .col").count() == 3 and "56 GB" in pg.inner_text("#hChart"), "monthly chart: only as many months as the data needs (min 3), this month shows 56 GB")
            check(pg.locator("#hTop .toprow").first.inner_text().startswith("Cancelled Show"), "biggest deletion listed first")
            check(pg.locator("#hRows tr").count() == 4, "table defaults to real deletions only (4)")
            check(pg.input_value("#hSize2") == "25" and "1–4 of 4" in pg.inner_text("#hPageInfo") and pg.is_disabled("#hNext") and pg.is_disabled("#hPrev"), "history paginates: default 25 per page")
            check([o for o in pg.locator("#hSize2 option").all_inner_texts()] == ["25", "50", "100", "250", "All"], "page-size choices 25/50/100/250/All")
            pg.select_option("#hSize2", "0"); check("1–4 of 4" in pg.inner_text("#hPageInfo"), "'All' per page works")
            pg.select_option("#hSize2", "25")
            pg.select_option("#hMode", "all")
            check(pg.locator("#hRows tr").count() == 4, "'Everything' shows all 4 log entries")
            pg.select_option("#hMode", "dry")
            check(pg.locator("#hRows tr").count() == 0 and pg.is_visible("#hEmpty"), "'Dry runs' empty on an instance with only live deletions")
            pg.select_option("#hMode", "skipped")
            check(pg.locator("#hRows tr").count() == 0, "no skipped/failed live entries yet")
            pg.select_option("#hMode", "live"); pg.fill("#hQ", "cancelled")
            check(pg.locator("#hRows tr").count() == 1, "history search works")
            pg.fill("#hQ", "")
            pg.select_option("#hMode", "all")
            opts_txt = pg.locator("#hUser option").all_inner_texts()
            check("alice" in opts_txt, f"history user filter lists requesters ({opts_txt})")
            pg.select_option("#hUser", "alice")
            n_alice = pg.locator("#hRows tr").count()
            check(0 < n_alice < 4 and all("alice" in t for t in pg.locator("#hRows tr").all_inner_texts()), f"history filtered to alice's requests ({n_alice})")
            pg.select_option("#hUser", "__none")
            check(pg.locator("#hRows tr").count() + n_alice <= 4, "history '(nobody)' filter works")
            hrefs = pg.locator("#hRows a.imdbtag").evaluate_all("els => els.map(e => e.href)")
            check(hrefs and all("imdb.com" in h for h in hrefs), f"history rows link to IMDb ({hrefs[0] if hrefs else None})")
            check(any("/title/tt" in h for h in hrefs), "logged IMDb id gives a direct title link")
            pg.select_option("#hUser", ""); pg.select_option("#hMode", "live")
            requests.get(MOCK + "/__calls")  # (calls are checked below via the recorder)
            rr = pg.locator("#hRows button.rr").first
            check(rr.count() == 1 and pg.locator("#hRows button.rr").count() >= 1, "history rows offer Re-request")
            rr.click(); pg.wait_for_selector("#hRows button.rr:has-text('Confirm?')")
            check("Will request" in rr.get_attribute("title"), "first click looks the title up and previews what will be requested")
            pg.check("#hAsUser"); rr.click(); pg.wait_for_selector("#hRows button.rr.ok")
            calls = [c for c in requests.get(MOCK + "/__calls").json() if c["service"] == "seerr" and c["what"] == "request"]
            check(len(calls) == 1 and calls[0]["query"].get("mediaId") and calls[0]["query"]["mediaType"] in ("movie", "tv"), f"re-request POSTed to Seerr ({calls[0]['query'] if calls else None})")
            check(pg.is_visible("#hNotice") and "Sent" in pg.inner_text("#hNotice"), "History shows a visible confirmation after re-requesting")
            pg.uncheck("#hAsUser")
            pg.locator("#hRows details summary").first.click()
            check("step" in pg.inner_text("#hRows tr >> nth=0"), "row details expand")
            pg.screenshot(path=os.path.join(os.environ.get("SHOT_DIR", tempfile.mkdtemp()), "ui_history.png"))
            pg.goto(base2 + "/#history"); pg.wait_for_selector("#hStats .stat")
            check(pg.is_visible("#viewHist"), "deep link to #history opens the History screen")
            pg.click("#tabMain")
            check(pg.is_visible("#viewMain"), "Candidates tab returns to the candidates screen")
            check(not errs, f"no JS console errors on History ({errs[:2]})")
            b.close()
    except ImportError:
        pass

    # without arr option, unmanaged + plex_delete off -> skipped, nothing touched
    requests.post(MOCK + "/__reset")
    r = s2.post(base2 + "/api/delete", json={"ids": [items2["Half Watched <b>Movie</b>"]["id"]],
                                            "options": {"arr": False, "seerr": True, "plex_delete": False}}).json()
    check(r["summary"]["failed"] == 1 and requests.get(MOCK + "/__calls").json() == [], "no delete path selected -> skipped, nothing touched, Seerr untouched")

    # limits & unknown ids
    r = s2.post(base2 + "/api/delete", json={"ids": ["movie:nope"], "options": opts})
    check(r.status_code == 400, "unknown ids rejected")
    r = s2.post(base2 + "/api/delete", json={"ids": [f"movie:{i}" for i in range(500)], "options": opts})
    check(r.status_code == 400, "batch size limit enforced")

    # legacy log entry (no TMDB id recorded) is resolved by exact title + year through Seerr
    with open(os.path.join(d2, "deletions.log"), "a") as fh:
        fh.write(json.dumps({"time": 111, "id": "movie:x", "title": "Plex Only Movie", "year": 2015, "type": "movie", "ok": True, "dry_run": False, "size": 1, "library": "Movies", "steps": []}) + "\n")
        fh.write(json.dumps({"time": 113, "id": "movie:z", "title": "Half Watched <b>Movie</b>", "year": 2018, "type": "movie", "ok": True, "dry_run": False, "size": 1, "library": "Movies", "steps": []}) + "\n")
        fh.write(json.dumps({"time": 112, "id": "movie:y", "title": "Unknown Thing", "year": 2001, "type": "movie", "ok": True, "dry_run": False, "size": 1, "library": "Movies", "steps": []}) + "\n")
    n0 = len([c for c in requests.get(MOCK + "/__calls").json() if c["what"] == "request"])
    lk = s2.post(base2 + "/api/rerequest/lookup", json={"time": 111, "id": "movie:x"}).json()
    check(lk.get("tmdb_id") == 1004 and lk.get("title") == "Plex Only Movie", f"legacy entry resolved via Seerr search ({lk})")
    check(s2.post(base2 + "/api/rerequest/lookup", json={"time": 112, "id": "movie:y"}).status_code == 404, "no exact match -> refuses instead of guessing")
    rq = s2.post(base2 + "/api/rerequest", json={"time": 111, "id": "movie:x"})
    sent = [c for c in requests.get(MOCK + "/__calls").json() if c["what"] == "request"]
    check(rq.status_code == 200 and len(sent) == n0 + 1 and sent[-1]["query"]["mediaId"] == 1004, "legacy entry re-requested with the resolved TMDB id")

    # old log entries get their real IMDb id filled in (Seerr title match -> TMDB external ids)
    got = None
    for _ in range(20):
        ents = {x["time"]: x for x in s2.get(base2 + "/api/log").json()["entries"]}
        got = ents[111].get("imdb_id") if ents.get(113, {}).get("imdb_id") else None
        if got:
            break
        time.sleep(0.5)
    check(got == "tt0001004", f"legacy log entry gained its IMDb id ({got})")
    check(not ents[112].get("imdb_id"), "unmatched legacy entry stays on the search fallback")
    check(ents[113].get("restored") is True and not ents[112].get("restored"), "title that is in Plex again (added after the deletion) is flagged restored")
    # Plex can keep the OLD date added when a file comes back; being managed by Radarr/Sonarr again is enough
    gen = s2.get(base2 + "/api/items").json()["generated_at"]
    t0 = gen - 60   # deleted just before the report was built; too recent for the "report is old" rule
    with open(os.path.join(d2, "deletions.log"), "a") as fh:
        for tid, steps in (("a", [{"step": "Sonarr", "ok": True, "detail": "removed with files"}]), ("b", [{"step": "Plex", "ok": True, "detail": "deleted via Plex API"}])):
            fh.write(json.dumps({"time": t0 + (1 if tid == "a" else 2), "id": "show:" + tid, "title": "Current Show", "year": 2024, "type": "show", "ok": True, "dry_run": False,
                                 "size": 1, "library": "TV Shows", "steps": steps}) + "\n")
    ents2 = {x["id"]: x for x in s2.get(base2 + "/api/log").json()["entries"]}
    check(ents2["show:a"].get("restored") is True, "arr-managed again after an arr removal -> restored even with an old date added")
    check(not ents2["show:b"].get("restored"), "no evidence (Plex-only removal, old date added, fresh report) -> not restored")
    # live look at Plex: deleted 20 min ago and Plex lists it right now -> back, with no report refresh needed
    with open(os.path.join(d2, "deletions.log"), "a") as fh:
        fh.write(json.dumps({"time": int(time.time()) - 1200, "id": "movie:live1", "title": "Plex Only Movie", "year": 2015, "type": "movie", "ok": True, "dry_run": False, "size": 1, "library": "Movies", "steps": []}) + "\n")
        fh.write(json.dumps({"time": int(time.time()) - 30, "id": "movie:live2", "title": "Plex Only Movie", "year": 2015, "type": "movie", "ok": True, "dry_run": False, "size": 1, "library": "Movies", "steps": []}) + "\n")
    lv = {}
    for _ in range(20):
        lv = {x["id"]: x for x in s2.get(base2 + "/api/log").json()["entries"]}
        if lv["movie:live1"].get("restored"):
            break
        time.sleep(0.5)
    check(lv["movie:live1"].get("restored") is True, "live Plex check marks a title that is listed in Plex again as restored")
    check(not lv["movie:live2"].get("restored"), "a deletion under 10 minutes old is never judged by the live check (Plex may not have dropped it yet)")
    real = [x for x in ents.values() if x["id"] not in ("movie:x", "movie:y", "movie:z", "show:a", "show:b", "movie:live1", "movie:live2") and x.get("ok") and not x.get("dry_run")]
    check(real and not any(x.get("restored") for x in real), "titles deleted in this run and still gone are not flagged restored")

    # automatic refresh
    print("Automatic refresh")
    auto = start_app(8093, True, tempfile.mkdtemp(), {"REFRESH_HOURS": "1", "SCHED_CHECK_SECONDS": "1"})
    sa = session(auto); sa.post(auto + "/login", data={"password": PW})
    check(sa.get(auto + "/api/config").json()["refresh_hours"] == 1, "refresh interval comes from REFRESH_HOURS")
    n = 0
    for _ in range(40):
        n = len(sa.get(auto + "/api/items").json()["items"])
        if n:
            break
        time.sleep(0.5)
    check(n == 6, f"report builds by itself when there is none ({n} items) - nobody clicked Refresh")
    off = start_app(8092, True, tempfile.mkdtemp(), {"SCHED_CHECK_SECONDS": "1"})
    so = session(off); so.post(off + "/login", data={"password": PW}); time.sleep(3)
    check(so.get(off + "/api/items").json().get("empty") is True, "interval 0 (default): nothing refreshes on its own")
    check(so.post(off + "/api/settings", json={"values": {"refresh_hours": "abc"}}).status_code == 400, "bad interval rejected")
    check(so.post(off + "/api/settings", json={"values": {"refresh_hours": "999"}}).status_code == 400, "out-of-range interval rejected")
    so.post(off + "/api/settings", json={"values": {"refresh_hours": "6"}})
    check(so.get(off + "/api/settings").json()["refresh_hours"] == "6" and so.get(off + "/api/config").json()["refresh_hours"] == 6, "interval saved from Settings and applied immediately")
    for _ in range(40):
        if so.get(off + "/api/items").json().get("items"):
            break
        time.sleep(0.5)
    check(len(so.get(off + "/api/items").json()["items"]) == 6, "turning the interval on starts an automatic refresh")

    # secret leakage
    print("Error handling")
    bad = start_app(8097, True, tempfile.mkdtemp(), {"TAUTULLI_API_KEY": "SECRETKEY123"})
    s3 = session(bad); s3.post(bad + "/login", data={"password": PW}); refresh(s3, bad)
    d3 = s3.get(bad + "/api/items").json()
    check(len(d3["items"]) == 6 and d3["history_source"] == "plex", "Tautulli auth failure -> falls back to Plex data, report still builds")
    check(any("Tautulli" in w for w in d3["warnings"]) and "SECRETKEY123" not in json.dumps(d3), "warning shown, API key not leaked")
    check(all(i["plays_1y"] is None for i in d3["items"]), "plays_1y unknown (null) without Tautulli")

finally:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(5)
        except Exception:
            p.kill()

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PASSED")
