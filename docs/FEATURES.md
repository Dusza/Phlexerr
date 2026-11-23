# Phlexerr feature guide

Details for every feature. For install and setup see the [README](../README.md).

## Released on this day (nostalgia row)
Turn it on in **Settings → Released on this day** to show movies whose release anniversary is today. You choose the **row name** people see in Plex, the **timeframe** (same day, within 3 days, same month, or a **custom window** of N days before and M days after today), how many **years ago** (at least, and optionally at most), the **minimum IMDb rating**, optional **genres** (several allowed; a movie can need any one or every one of them) and **tags** (Plex labels), the **maximum number of movies** (leave blank for all of them; **Preview today** has a **View all** list), and the **order** (highest rated, oldest, newest, A-Z, or shuffled daily). **Preview today** shows what would appear; **Save & publish now** creates the Plex collection and pins it to Home. It is set-and-forget: saving settings applies them automatically, and the row then updates itself every day and after each data refresh. Renaming moves the row; turning it off removes it from Plex. Release dates come from Plex (Radarr's cinema date as a fallback); unrated movies are left out. "Today" follows the container's clock, so set the `TZ` variable on the container (e.g. `America/New_York`) if the date rolls over at the wrong time. To change where the row sits among your other Home rows, drag it in Plex (Home → Customize); Plex remembers the position.

## Custom Plex Home Screen rows
The **Plex Home Screen** tab builds any other row from criteria (actor, director, studio, collection, genre, tag, title keyword, plus rating, recently added, never watched). Each row is a Plex collection kept in sync after every refresh, in the order you pick.

**Rows from IMDb lists.** On the Plex Home Screen tab, paste one or more IMDb list links, one per line (or upload CSV exports), instead of building criteria. The row holds only the titles from the lists (combined, duplicates removed) that are already in Plex; the rest are ignored. Sort by **Order in the list** to keep IMDb's order. The list is downloaded each time the row syncs (so it follows changes to the list), and the last copy is used if IMDb can't be reached. IMDb has no official API: this reads the public CSV export of a list, so it can stop working if IMDb changes it, and private lists need the CSV upload instead (on IMDb: the list's ••• menu → Export). Needs outbound internet access to imdb.com.

**MDBList links.** Paste an MDBList list link (e.g. `https://mdblist.com/lists/username/list-name`) in the same box as IMDb links, one per line; the app recognises which is which. IMDb blocks automated downloads, so MDBList (which can collect lists from IMDb, Trakt, Letterboxd and others) is the dependable way to keep a row following a list. Public lists need no key (the app reads the list's `/json` page). For a list that isn't public, set `MDBLIST_API_KEY` (from mdblist.com/preferences) in the container's environment and the app will fall back to MDBList's API.

**Requesting titles that aren't in Plex yet.** Rows built from a list have a **Missing…** button. It lists the titles on the list that aren't in Plex and checks each one in Seerr (not requested / already requested / already available / can't match). Tick the ones you want (or *select every title that can be requested*) and press **Request in Seerr**; Seerr passes them on to Radarr/Sonarr, and may need approval first. The screen shows each title's release date and rating and lets you filter (text, Seerr status, movie/show, minimum rating) and sort (release date, rating, title, list order; newest release first by default). The rating is the list's own IMDb rating when it gives one, otherwise Seerr's TMDB rating (labelled), and the date comes from the list or from Seerr. Safeguards: nothing is requested automatically, at most 50 titles per batch, titles Seerr already has are skipped, and a title is only requested when its name and year match exactly (otherwise it is shown as "can't match"). Requests are made as the Seerr API key's user. MDBList lists and uploaded IMDb CSVs carry titles and years; a list that only gave IMDb ids can't be matched.

**Your own order (e.g. a story timeline).** Pick **My own order** as a row's sort, then **Arrange titles…**. Drag titles, use ↑ ↓, or type a position number; "Start from release date" gives you a starting point. Titles you haven't placed (for example new additions) go at the end, oldest release first. Build the row from your existing collection by adding a *Collection* criterion with its name. Publishing writes the order into the Plex collection. Note that a row owns its collection's order, so reordering that collection by hand in Plex will be overwritten on the next sync.

**Publishing progress.** Press Publish and a progress bar shows the current step (downloading the lists, adding titles, putting them in order, arranging Home) with a percentage and elapsed time. It keeps running if you leave the page, and the bar comes back when you return.

**Sorting.** Sort a row by date added, IMDb rating, year, release date, times watched, last watched, title or random, in either direction. Items with no value for the chosen field (no rating, no release date…) always go last.

**Everyone gets the same rows.** Each row has **Also show on other users' Home screens** (on by default), which covers Plex Home members and people you share with. Turn it off to keep a row on your own Home only. Per-user rows aren't possible with a server token: each account's own Home layout belongs to that account, and people you share with can still hide or reorder rows in their Plex app.

**Order on Home.** Use the ↑ / ↓ buttons in the Order column. The app puts your rows at the top of Home in that order and re-applies it on every sync. This uses a Plex call that hasn't been verified against every server version, so if Plex refuses, the new order is still saved and the page tells you.


## Finding unrequested automated downloads
If lists (IMDb, Trakt…) auto-download content nobody asked for, use the **Unrequested downloads not watched** quick preset on the Prune tab. It combines three filters, all editable afterwards: **Requested by** = nobody / unknown (no Seerr request), **Added within last (days)** = 30, and **Not watched in (days)** = 14. Change the numbers to widen or narrow the window; items never watched always pass the "not watched" test. Protected items stay hidden.

## Look and feel
- **Light / dark:** the ☾/☀ button in the header switches theme. It follows your OS until you pick one, then remembers your choice in that browser.
- **Phones:** the table becomes cards, filters fold behind a "Filters" button, and there's a Sort dropdown.
- **Install as an app (PWA):** open the site in Chrome/Safari and choose "Add to Home Screen" / "Install". Icons and the manifest are in `app/static/icons` and `app/static/manifest.webmanifest`. Installing from a non-HTTPS address (plain `http://` on your LAN) may not be offered; it works through a Cloudflare tunnel (HTTPS).

## Leaving Soon (optional, off by default)

For people who delete by hand but want viewers to get a heads-up. Turn it on under **Settings → Leaving Soon**.

- In Prune, select titles and click **Mark leaving soon**. Each gets a date (your chosen number of days from now).
- While enabled, a **Leaving Soon** row appears on your Plex Home with those titles. **While disabled, the row does not exist** and nothing is shown to viewers.
- It only labels. **Nothing is deleted automatically**, ever. When the date passes the title stays, flagged as due, until you delete it yourself.
- Protected titles and titles marked Keep can't be marked.
- Deleting a title clears its mark. Turning the feature off removes the row but remembers your marks.
- Filter Prune by *Leaving soon* or *Leaving soon: due* to find them.

## Protecting media
The **Protect** tab lets you lock media so it can never be deleted: by **actor**, **director**, **studio**, **collection** or **title keyword**.
- Matching ignores case and accents. `*` matches any run of characters, `?` one character (e.g. `Tom *`, `Marvel*`, `*Trilogy`).
- Actor, director, studio and collection patterns match the whole name; a title keyword without wildcards matches anywhere in the title.
- Actor rules can be limited to the **top N billed** cast (e.g. top 3) so a cameo doesn't protect a film.
- A live preview shows what a rule would protect before you add it. Rules can be switched off or removed at any time.
- Protected rows show a lock badge and can't be selected. The server re-checks every rule on each delete, so nothing protected is ever removed, even by a stale page.
- Cast/crew is read from Plex during **Refresh data** and cached (`people.json`); the first refresh is slower, later ones only re-read changed items.
- Rules are saved in `/config/protection.json`. The existing **Mark Keep** button still works and counts as protection too.

## Re-requesting a deleted title
On the **History** tab, each real deletion has a **Re-request** button (click twice to confirm) that sends the title back to Seerr, which then hands it to Radarr/Sonarr as usual. TV shows are requested for all seasons. By default the request is made as the Seerr API key's user; tick **Re-request as the original requester** to file it under whoever originally asked. Only titles in the deletion log can be re-requested, and entries from before this feature have no TMDB id recorded, so the app finds the title in Seerr by exact name and year first (the button previews the match before you confirm).

## Titles that come back
If you delete something, re-request it and it lands in Plex again, its History row switches from "✓ removed" to "↩ back in Plex" and it stops counting toward "space freed" (the stats card notes how many were excluded). While History is open the app takes a quick live look at Plex (every couple of minutes, no full refresh needed); a title listed there that was deleted more than 10 minutes ago counts as back. It also uses the last report (Radarr/Sonarr state, date added). Use **Show → Back in Plex again** to list them.

## IMDb links on old deletions
New deletions record their exact IMDb id. For older entries the app looks each title up in Seerr (exact name + year) and reads the IMDb id from Seerr's TMDB data, in the background the first time you open History; results are cached in `log_ids.json`. Anything it can't match with confidence keeps the IMDb search link.

## Plex links
Titles and posters open the item in Plex. Set **Plex links** in Settings (default `https://app.plex.tv/desktop`) if you'd rather open your local server, e.g. `http://192.168.1.10:32400/web`. The (i) button shows the description, cast and details.

## Activity (who is watching what)
The Activity tab reads Tautulli: a live "Now playing" panel (updates by itself every 5 s) and recent plays with user/type/title filters. Endpoints: `/api/activity`, `/api/plays`. Needs the Tautulli URL and API key. Each stream has a Stop button (with an optional message to the viewer) that calls Tautulli `terminate_session`; the rest is read-only.

## Request tab (search and request any title)

Needs Seerr; the tab is hidden without it. Type two or more letters to search Seerr's catalogue for movies and shows. Each result shows Seerr's own status: **Available**, **Partially available**, **Requested** (approved and processing), **Pending** (waiting for approval) or **Blocklisted**. Titles that are already available, requested or pending have no button, so a request can't be duplicated. **Request** sends it to Seerr as the API key's user, which passes it on to Radarr or Sonarr; shows are requested with all seasons. Nothing is requested until you press the button.

## History and space freed

The **History** tab (its own screen, also reachable at `#history`) shows total space freed, items deleted, the last 30 days, a 12-month chart and the biggest deletions. Only real (non-dry-run) deletions that removed the files count. The table below lists every attempt with its steps, and exports to CSV.

## Updating

The container mounts the `app` folder (`/mnt/user/appdata/phlexerr/app` → `/srv/app`), so a code update needs no rebuild:

1. Replace the contents of `/mnt/user/appdata/phlexerr/app` with the new version (leave `data` alone).
2. Docker tab → click **phlexerr** → **Restart**.

You only need `docker build` again if `Dockerfile` or `requirements.txt` change.

## Safety model

- **`DRY_RUN=true` by default.** Nothing is deleted; you get an exact report of what would happen. Flip to `false` on the container only after you trust the data. It can't be changed from the web UI, so a compromised session can't turn deletion on.
- Real deletes require typing `DELETE` in a confirmation dialog that lists every item and the space freed.
- Items marked **Keep** are hidden by default and refused by the server.
- Every attempt (dry or live) is written to `/config/deletions.log` and shown under *Deletion log*.
- Batch limit (`MAX_DELETE_BATCH`, default 200).
- If removal from Radarr/Sonarr fails, Seerr is left alone for that item.

## What a delete does

Options in the dialog:

1. **Remove from Radarr / Sonarr** and delete the files (`deleteFiles=true`), optionally adding an import exclusion.
2. **Remove from Seerr**: deletes the media record and its requests so it can be requested again.
3. **Scan Plex and empty trash** afterwards (runs in the background once the scan finishes).
4. **Delete through Plex** for items Radarr/Sonarr don't manage. This needs *Settings → Library → Allow media deletion* in Plex.

## Where each column comes from

| Column | Source |
|---|---|
| Title, added | Plex |
| IMDb rating | Movies: Radarr, then Plex. Shows: Plex's IMDb rating, then Sonarr's rating (source may not be IMDb). Hover the rating to see which. |
| Last viewed, plays (1y), % viewed | Tautulli history across **all users**. Movies: highest % complete. Shows: distinct episodes watched ÷ episodes in Plex. Show "plays" count episode plays. |
| Requested by | Seerr (shows with several requesters list them all) |
| Size | Radarr/Sonarr size on disk, else Plex |

Without Tautulli the app falls back to Plex's own data (one account only), and "Plays (1y)" shows `?`.
Tautulli only knows plays since it was installed, so older items may show 0 plays.

## Putting it behind a Cloudflare tunnel

The app has its own password login, throttling and CSRF protection, but this tool can delete files, so add a second layer:

1. Set `COOKIE_SECURE=true` and `TRUST_PROXY=true` on the container.
2. Point the tunnel at `http://SERVER-IP:2112`.
3. **Put Cloudflare Access (Zero Trust) in front of the hostname**, limited to your email, so strangers never reach the login page.
4. Keep `DRY_RUN=true` unless you're actively cleaning up.

## Development / tests

```
pip install flask requests gunicorn playwright
python3 tests/e2e.py
```
The test starts a mock of all five services and exercises the report, dry-run, live deletes, auth and the UI.

## Animation

Animated titles get a 🎨 icon on the Prune tab and the Missing list. Both screens have an **Animation** filter (Include / Hide animation / Only animation). Prune uses the Plex genre "Animation". Missing titles use the genres from MDBList or Seerr, and titles that haven't been checked against Seerr yet may not be flagged.

Missing-list thumbnails: small posters come from Seerr's TMDB poster path and are fetched by the app (default `https://image.tmdb.org`, override with `TMDB_IMAGE_BASE_URL`). Titles Seerr hasn't been checked for yet (or couldn't match) show a blank placeholder.

## In-app help
The **Help** tab is a searchable guide to every feature plus an FAQ (also at `/#help`).

## Prune screen layout
The **Prune** tab has a **Cards / Table** switch. Cards is the default and the choice is remembered in the browser.

## Phones
Every tab and popup is checked at phone width (390px) by the test suite: no sideways scrolling, popup buttons always on screen, and the Protect, Plex Home Screen, History and Missing tables turn into stacked cards.

## Colours
The theme uses a charcoal and amber palette (amber `#e5a00d`), in both light and dark mode. All colours are CSS variables at the top of `app/static/style.css`, so changing the look is a few lines.

## Only Plex is required
Tautulli, Radarr, Sonarr and Seerr are optional. The app hides or disables whatever needs a missing service and says why (a "Heads up" box on Prune, a greyed-out option in the delete dialog, a "needs Tautulli" panel on Activity, a notice when requesting without Seerr). `python3 tests/plex_only.py` runs the whole app with only Plex configured.
