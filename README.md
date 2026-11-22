# Phlexerr

*Pronounced "flex-er" (Plex + "ph" + "err").*

**Clean up your Plex library on your terms.** Phlexerr shows you what is worth deleting, you decide, and nothing is ever removed on a timer or behind your back.

Born from a simple problem: **your users request everything.** Every "just one more season" and "I'll definitely watch that someday" lands on your server, and the drives fill up. Phlexerr is how you take the library back, kindly and on your own schedule.

Most cleanup tools run rules on a schedule and delete for you. Phlexerr is built for people who would rather look first and click the button themselves, with a safety net around every click.

## Why Phlexerr

- **You are always the one who deletes.** No schedules, no background deletions, no "the rule matched at 3 a.m." surprises. Every delete is a button you pressed.
- **Tame the request button.** See who requested each title and whether anyone ever pressed play, so "requested it, never watched it" stops being a mystery. Prune it, and if they really do want it back later, they can request it again.
- **See the space you win back.** Every selection shows how much it will free before you confirm, and the History tab keeps a running total of what you've reclaimed, so you can see the drive-space payoff.
- **Review before you act.** A sortable list or card view with ratings, file sizes, last-watched dates and who requested each title, so you can make the call with the facts in front of you.
- **Dry-run by default.** You get an exact report of what *would* happen until you choose to turn deleting on, and that switch can't be flipped from the web page.
- **Protection that actually protects.** Lock titles by actor (even top-billed only), director, studio, collection or keyword. Protected titles can't be selected, and the server refuses them even from an old page.
- **Warn people first, only if you want to.** An optional, off-by-default **Leaving Soon** row puts titles you picked on your Plex Home with a date. When the date passes, nothing happens until you delete them.
- **Everything in one place.** Prune, Protect, custom Plex Home rows, live "who's watching" and a history of space freed, in one app.
- **Starts with only Plex.** Tautulli, Radarr, Sonarr and Seerr are optional extras, and the app works around any you don't have.

### How it compares

These tools overlap a little but solve different problems. Pick by what you want done.

| | **Phlexerr** | Maintainerr | Janitorr | Cleanuparr | Checkrr |
|---|---|---|---|---|---|
| Main job | Review and delete unwatched or unwanted media by hand | Rule-based media cleanup | Automatic media cleanup | Clean stalled, failed or unwanted downloads from the download client queue | Find corrupt or broken media files |
| Who decides what is deleted | **You, with a review screen** | Rules, on a schedule | Rules, on a schedule | Rules (queue items, not your library) | Reports problems |
| Manual, title-by-title control | **Yes, it is the whole design** | Limited | Limited | No | No |
| Dry-run before anything happens | **Yes, on by default** | Varies by setup | Yes (setting) | Yes (setting) | Report-only |
| Warn viewers before removal | Optional Leaving Soon row, **you** delete later | Yes, automated | Yes, automated | No | No |
| Custom Plex Home rows | **Yes** | Collections | No | No | No |
| Live "who is watching" and Stop stream | **Yes** (with Tautulli) | No | No | No | No |
| Space-freed history | **Yes** | No | No | No | No |
| Needs Radarr / Sonarr | **No, optional** | Optional | Yes | Yes | Optional |
| Plex required | Yes | Yes (or Jellyfin) | Plex or Jellyfin | No | No |

*Based on each project's public description at the time of writing; those projects change, so check their pages before deciding.* They are good at what they do, and they can run alongside Phlexerr. If you want the library cleaned without touching it, use an automatic tool. If you want to see it, decide, and press the button yourself, use Phlexerr.

## What it does

- **Prune:** find movies and shows worth deleting (never watched, stale, low rated, large, unrequested downloads) and delete them, optionally cleaning up Radarr, Sonarr and Seerr at the same time.
- **Protect:** lock titles so they can never be deleted.
- **Plex Home Screen:** build your own Home rows from criteria or IMDb/MDBList lists, and request what's missing through Seerr.
- **Activity:** live "now playing" cards, a full watch history, and a Stop button for a stream (needs Tautulli).
- **History:** everything deleted, and the space freed.
- **Leaving Soon (optional):** warn viewers before you delete, without automating anything.

> **Phlexerr can permanently delete files.** It starts in **dry-run mode**, where nothing is deleted and you get a report of what would happen. Read [Safety](#safety) before you turn dry-run off.

*Phlexerr is an independent project and is not affiliated with or endorsed by Plex, Tautulli, Radarr, Sonarr, Seerr or IMDb. Those names belong to their owners.*

## Required vs optional

**Required (you can't start without these):**

| What | Why | Where it goes |
|---|---|---|
| **Docker** | Phlexerr runs as a container | Unraid already has it |
| **An app password** you choose | Protects the web page | Container setting `APP_PASSWORD` |
| **A folder for its data** | Keeps your settings and history between updates | Mapped to `/config` (the template does this for you) |
| **Plex URL and Plex token** | The library it works on | Settings page (or container settings) |

**Optional (add any, any time, from the Settings page):**

| Service | What you gain | What happens without it |
|---|---|---|
| Tautulli | Play counts and last-watched across *all* users, plus the **Activity** tab | Play data comes from your own Plex account only, and Activity asks you to connect it |
| Radarr / Sonarr | Real file sizes and ratings, and deleting through them | Titles are deleted through Plex instead |
| Seerr (Overseerr / Jellyseerr) | "Requested by", re-requesting deleted titles, requesting missing titles | Those parts are hidden and the app says why |
| MDBList key | Following private MDBList lists | Public lists work without it |

You don't need to fill in anything optional up front. **DRY_RUN** is also a container setting; it defaults to `true` (safe) and you only change it when you are ready to delete for real.

## Install

Pick one. Replace `YOUR-GITHUB-USERNAME` with the account that publishes the image (the maintainer's GitHub name).

### Unraid (ready-made image)

1. Download `Phlexerr.xml` from this repository and copy it to `/boot/config/plugins/dockerMan/templates-user/` on your server.
2. Docker tab, **Add Container**, pick **phlexerr** from the template dropdown.
3. Set an **App password** (you choose it) and leave **DRY_RUN** as `true`. That is all the container needs. Plex and the optional services are entered on the web page next. Click **Apply**.
4. Open `http://YOUR-SERVER-IP:8080`, sign in, and Phlexerr will ask for your Plex details.

### Docker Compose

```
git clone https://github.com/YOUR-GITHUB-USERNAME/phlexerr.git && cd phlexerr
cp .env.example .env        # set APP_PASSWORD at minimum
docker compose up -d --build
```
Open `http://localhost:8080`. Your settings are stored in `./data`.

### Docker run

```
docker run -d --name phlexerr --restart unless-stopped -p 8080:8080 \
  -e APP_PASSWORD='choose-a-password' -e DRY_RUN=true \
  -v /path/to/phlexerr-data:/config ghcr.io/YOUR-GITHUB-USERNAME/phlexerr:latest
```
The container runs as user `99:100`. If the data folder isn't writable, run `chown 99:100 /path/to/phlexerr-data`.

### Build it yourself instead

```
docker build -t phlexerr:latest .
```
Then use `phlexerr:latest` as the image name in the commands above (on Unraid, edit the template's Repository field).

## First-run setup

1. Open the web page and sign in with your app password.
2. Click **Settings** and enter the **Plex URL and token**. Everything else is optional (Tautulli, Radarr, Sonarr, Seerr). Each one has a **Test connection** button. **Leaving Soon** is also optional and off until you enable it.
3. Click **Save & refresh data**. The first refresh reads your whole library and can take a few minutes.
4. Browse **Prune**. Nothing is deleted while the **DRY RUN** badge is showing.

Use your server's LAN IP in the URLs (for example `http://192.168.1.10:32400`), not `localhost`.

| Setting | Where to find it |
|---|---|
| Plex token | Plex web, any item, the **⋯** menu, **Get Info**, **View XML**, then `X-Plex-Token` in the address bar |
| Tautulli key | Tautulli, Settings, Web Interface, API key |
| Radarr / Sonarr key | Settings, General, API key |
| Seerr key | Settings, General, API key |

### Where your keys are stored

Keys entered on the Settings page are saved in `settings.json` in your data folder (`/config` in the container), readable only by the container's user. They are never sent back to your browser once saved. Keys given as container environment variables stay in the container definition. Anything saved on the Settings page overrides the environment. **Never share your data folder or your Unraid template file.**

## Safety

- **Dry-run is on by default.** It can only be switched off with `DRY_RUN=false` on the container, never from the web page, so a stolen login can't turn on deleting.
- **Nothing is ever deleted automatically.** There are no schedules. Even the optional Leaving Soon row only *labels* titles; when a date passes, the title stays until you delete it.
- Real deletes need you to type `DELETE` in a dialog that lists every item and the space it will free.
- Protected and **Keep** items can't be selected, and the server refuses them even from an old page.
- Every attempt, real or dry, is written to the deletion log and shown on the History tab.
- Batches are limited (200 by default).
- If removing a title from Radarr/Sonarr fails, Seerr is left alone for that title.

## Updating

- **Ready-made image:** pull the new image and recreate the container. On Unraid, use the Docker tab's **Check for updates** and **Apply update**. Your `data` folder is kept.
- **Built from source:** pull the new code, run `docker build` again (or `docker compose up -d --build`), and recreate the container.

Hard-refresh the browser afterwards (Ctrl+F5).

## Remote access

Phlexerr has a password login, login throttling and CSRF protection, but it can delete files, so don't expose it to the internet on its own. If you want access from outside:

1. Set `COOKIE_SECURE=true` and `TRUST_PROXY=true`.
2. Use a tunnel or reverse proxy with HTTPS.
3. Put an access-control layer in front (for example Cloudflare Access limited to your email, or a VPN such as Tailscale).

## Configuration reference

Everything below can be set as an environment variable. Service URLs and keys can instead be entered on the Settings page.

| Variable | Default | Meaning |
|---|---|---|
| `APP_PASSWORD` | none, **required** | Password for the web page |
| `DRY_RUN` | `true` | Nothing is deleted while true |
| `PLEX_URL`, `PLEX_TOKEN` | | Plex (required, but can be set in Settings) |
| `TAUTULLI_URL`, `TAUTULLI_API_KEY` | | Optional |
| `RADARR_URL`, `RADARR_API_KEY` | | Optional |
| `SONARR_URL`, `SONARR_API_KEY` | | Optional |
| `SEERR_URL`, `SEERR_API_KEY` | | Optional |
| `MDBLIST_API_KEY` | | Only for private MDBList lists |
| `PLEX_LIBRARIES` | all | Limit to some libraries, comma separated |
| `WATCHED_PERCENT` | `85` | An episode counts as watched at this % |
| `MAX_DELETE_BATCH` | `200` | Most items per delete |
| `REFRESH_HOURS` | `0` | Auto-refresh interval (0 = manual) |
| `VERIFY_TLS` | `true` | Set false for self-signed certificates |
| `COOKIE_SECURE`, `TRUST_PROXY` | `false` | For use behind HTTPS and a proxy |
| `TZ` | | Your time zone, for the "Released on this day" row |

## Troubleshooting

- **Can't sign in / "bad request":** hard-refresh the browser (Ctrl+F5) after an update.
- **"Heads up" box on Prune:** that's the list of optional services you haven't connected. It's information, not an error.
- **Settings won't save:** the data folder isn't writable. Run `chown 99:100` on it and restart.
- **Plays show `?`:** Tautulli isn't connected, so play data comes from Plex for your own account only.
- **IMDb list rows stop updating:** IMDb blocks automated downloads. Use an MDBList link or upload the list's CSV instead.
- **Posters on the Missing list are blank:** the title hasn't been matched in Seerr yet, or the server can't reach `image.tmdb.org`.

## More documentation

- [Feature guide](docs/FEATURES.md): every tab and option in detail
- [Moving from "Media Pruner"](docs/MIGRATING.md)
- The app also has a searchable **Help** tab.

## Development

```
pip install -r requirements.txt playwright
python3 tests/e2e.py          # full test against mock services
python3 tests/plex_only.py    # the app with only Plex connected
```
The tests start mock versions of all five services, so they never touch your real server.

## License

[MIT](LICENSE).
