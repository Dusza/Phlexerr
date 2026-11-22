"""Deletion logic. Every step is recorded per item so the UI (and the log) can show exactly what happened."""
import threading
import time

from .clients import Plex, Radarr, Sonarr, Seerr, ApiError


def _plex_cleanup(cfg, section_ids):
    """Trigger a scan, wait for it to finish, then empty trash. Runs in the background."""
    try:
        plex = Plex(cfg)
        for sid in section_ids:
            plex.refresh(sid)
        time.sleep(5)
        deadline = time.time() + 300
        while time.time() < deadline and plex.scanning():
            time.sleep(5)
        for sid in section_ids:
            plex.empty_trash(sid)
        print(f"[delete] Plex scan + empty trash complete for sections {sorted(section_ids)}", flush=True)
    except ApiError as e:
        print(f"[delete] Plex cleanup failed: {e}", flush=True)


def run(cfg, items, opts, dry_run, blocked):
    """Delete `items` according to `opts`. `blocked` maps item id -> reason it is protected. Returns (results, summary)."""
    svc = cfg.services()
    radarr = Radarr(cfg) if svc["radarr"] else None
    sonarr = Sonarr(cfg) if svc["sonarr"] else None
    seerr = Seerr(cfg) if svc["seerr"] else None
    plex = Plex(cfg)

    results, sections_touched = [], set()
    for it in items:
        steps = []
        res = {"id": it["id"], "title": it["title"], "year": it.get("year"), "size": it["size"], "ok": False, "steps": steps,
               "type": it["type"], "library": it.get("library")}
        results.append(res)

        if it["id"] in blocked:
            steps.append({"step": "check", "ok": False, "detail": f"protected ({blocked[it['id']]}) - skipped"})
            continue

        # 1) primary removal: Radarr/Sonarr (deletes files) or Plex fallback
        primary_ok = False
        client = radarr if it["type"] == "movie" else sonarr
        label = "Radarr" if it["type"] == "movie" else "Sonarr"
        try:
            if opts.get("arr") and it.get("arr_id") and client:
                if dry_run:
                    steps.append({"step": label, "ok": True, "detail": f"[dry run] would delete id {it['arr_id']} + files"
                                  + (" + import exclusion" if opts.get("exclusion") else "")})
                else:
                    client.delete(it["arr_id"], opts.get("exclusion"))
                    steps.append({"step": label, "ok": True, "detail": "removed with files"
                                  + (" + import exclusion" if opts.get("exclusion") else "")})
                primary_ok = True
            elif opts.get("plex_delete"):
                if dry_run:
                    steps.append({"step": "Plex", "ok": True, "detail": "[dry run] would delete via Plex API"})
                else:
                    plex.delete_item(it["plex_key"])
                    steps.append({"step": "Plex", "ok": True, "detail": "deleted via Plex API"})
                primary_ok = True
            else:
                why = "not managed by " + label if not it.get("arr_id") else f"{label} removal not selected"
                steps.append({"step": "delete", "ok": False,
                              "detail": f"skipped: {why} and 'delete through Plex' is off"})
        except ApiError as e:
            steps.append({"step": label if opts.get("arr") and it.get("arr_id") else "Plex", "ok": False, "detail": str(e)})

        if not primary_ok:
            continue  # never touch Seerr if the files weren't removed

        # 2) Seerr
        if opts.get("seerr") and it.get("seerr_media_id") and seerr:
            try:
                if dry_run:
                    steps.append({"step": "Seerr", "ok": True, "detail": f"[dry run] would remove media {it['seerr_media_id']}"})
                else:
                    seerr.delete_media(it["seerr_media_id"])
                    steps.append({"step": "Seerr", "ok": True, "detail": "media record + requests cleared"})
            except ApiError as e:
                steps.append({"step": "Seerr", "ok": False, "detail": str(e)})

        sections_touched.add(it["section_id"])
        res["ok"] = True  # files are gone; Seerr trouble is reported but doesn't undo that

    summary = {"dry_run": dry_run, "ok": sum(1 for r in results if r["ok"]),
               "failed": sum(1 for r in results if not r["ok"]),
               "bytes": sum(r["size"] for r in results if r["ok"]), "plex_cleanup": None}

    if opts.get("plex_cleanup") and sections_touched:
        if dry_run:
            summary["plex_cleanup"] = f"[dry run] would scan + empty trash on {len(sections_touched)} librar{'y' if len(sections_touched) == 1 else 'ies'}"
        else:
            threading.Thread(target=_plex_cleanup, args=(cfg, sorted(sections_touched)), daemon=True).start()
            summary["plex_cleanup"] = "Plex library scan started; trash will be emptied when it finishes"
    return results, summary
