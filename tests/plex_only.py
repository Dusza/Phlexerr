"""Only Plex is configured (Tautulli, Radarr, Sonarr, Seerr all blank): nothing may break, and the UI must explain what's missing.
Run: python3 tests/plex_only.py"""
import os, subprocess, sys, tempfile, time
import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK, PW, failures, procs = "http://127.0.0.1:9100", "test-password", [], []
SHOT = os.environ.get("SHOT_DIR", tempfile.mkdtemp())
os.makedirs(SHOT, exist_ok=True)


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        failures.append(m)


def wait(url):
    for _ in range(80):
        try:
            requests.get(url, timeout=1); return
        except requests.RequestException:
            time.sleep(0.25)
    raise SystemExit("timeout " + url)


try:
    procs.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_services.py"), "9100"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait(MOCK + "/__calls")
    env = {**os.environ, "PLEX_URL": MOCK + "/plex", "PLEX_TOKEN": "plextoken", "APP_PASSWORD": PW, "DRY_RUN": "true", "CONFIG_DIR": tempfile.mkdtemp(),
           **{k: "" for k in ("TAUTULLI_URL", "TAUTULLI_API_KEY", "RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY", "SEERR_URL", "SEERR_API_KEY")},
           "IMDB_BASE_URL": MOCK + "/imdb", "MDBLIST_BASE_URL": MOCK + "/mdblist"}
    procs.append(subprocess.Popen([sys.executable, "-m", "gunicorn", "-w", "1", "--threads", "4", "-b", "127.0.0.1:8097", "app.main:app"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    base = "http://127.0.0.1:8097"; wait(base + "/healthz")
    s = requests.Session(); s.headers["X-Requested-With"] = "phlexerr"
    s.post(base + "/login", data={"password": PW})
    print("API with Plex only")
    s.post(base + "/api/refresh", json={})
    for _ in range(80):
        st = s.get(base + "/api/status").json()
        if st["state"] != "running": break
        time.sleep(0.3)
    check(st["state"] == "idle" and not st.get("error"), f"refresh works with Plex only ({st.get('error')})")
    items = s.get(base + "/api/items").json()["items"]
    check(len(items) > 0, "Prune list loads from Plex alone")
    for path in ("/api/rules", "/api/facets", "/api/rows", "/api/throwback", "/api/log", "/api/config"):
        r = s.get(base + path); check(r.status_code < 500, f"GET {path} -> {r.status_code}")
    a = s.get(base + "/api/activity"); check(a.status_code == 400 and "Tautulli" in a.json()["error"], "activity: clear message when Tautulli is missing")
    p = s.get(base + "/api/plays"); check(p.status_code == 400 and "Tautulli" in p.json()["error"], "plays: clear message when Tautulli is missing")
    d = s.post(base + "/api/delete", json={"ids": [items[0]["id"]], "options": {"arr": True, "seerr": True, "plex_cleanup": True, "plex_delete": True}})
    check(d.status_code < 500, f"dry-run delete with Radarr/Sonarr/Seerr options but none configured -> {d.status_code} {d.text[:160]}")

    print("UI with Plex only")
    errs = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: errs.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        pg.goto(base + "/login"); pg.fill("input[name=password]", PW); pg.click("button[type=submit]"); pg.wait_for_selector("#tabMain")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(SHOT, "po_main.png"))
        tabs = [t for t in ("#tabMain", "#tabProt", "#tabRows", "#tabHist", "#tabAct", "#tabHelp") if pg.is_visible(t)]
        print("   visible tabs:", tabs)
        for t in tabs:
            pg.click(t); pg.wait_for_timeout(700); pg.screenshot(path=os.path.join(SHOT, f"po_{t[4:]}.png"))
        pg.goto(base + "/#activity"); pg.wait_for_timeout(800); pg.screenshot(path=os.path.join(SHOT, "po_act_direct.png"))
        pg.goto(base + "/"); pg.wait_for_selector("#tabMain"); pg.wait_for_timeout(1200)
        check(pg.locator(".badge.warn", has_text="no Radarr").count() == 0 and pg.locator(".badge.warn", has_text="no Sonarr").count() == 0, "no misleading 'no Radarr/Sonarr' badges when they aren't connected")
        check(not pg.is_visible("[data-preset=auto]") and not pg.is_visible("#fUser"), "Seerr-only controls (Unrequested downloads, Requested by) are hidden without Seerr")
        pg.click("#tabAct"); pg.wait_for_timeout(500)
        check(pg.is_visible("#aSetup") and not pg.is_visible("#aNow") and "Tautulli" in pg.inner_text("#aSetup") and not pg.is_visible("#actBadge"), "Activity shows a friendly 'needs Tautulli' panel")
        pg.screenshot(path=os.path.join(SHOT, "po_activity.png"))
        pg.click("#tabMain"); pg.click("#tbl tbody tr:first-child .rowchk") if pg.locator("#tbl:visible").count() else None
        pg.evaluate("() => { const c = document.querySelector('.rowchk'); if (c && !c.checked) c.click(); }"); pg.wait_for_timeout(300)
        pg.evaluate("() => { const b = [...document.querySelectorAll('button')].find(x => /Delete|dry run/i.test(x.textContent) && x.offsetParent); if (b) b.click(); }"); pg.wait_for_timeout(600)
        if pg.is_visible("#dlgDelete"):
            check(pg.is_disabled("#oSeerr") and pg.is_disabled("#oArr") and "aren't connected" in pg.inner_text("#delBody"), "delete dialog disables Radarr/Sonarr/Seerr options with an explanation")
            pg.screenshot(path=os.path.join(SHOT, "po_delete.png"))
        else:
            check(False, "delete dialog opened")
        pg.keyboard.press("Escape")
        check(not errs, f"no JS errors / 5xx across tabs {errs[:4]}")
        b.close()
finally:
    for p in procs: p.terminate()
print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED"))
