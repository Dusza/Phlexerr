"""Configuration, read from environment variables."""
import os


def _s(name, default=""):
    return os.environ.get(name, default).strip()


def _b(name, default):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _i(name, default):
    try:
        return int(_s(name, str(default)))
    except ValueError:
        return default


DEFAULT_PASSWORD = "obiwan"   # used only when APP_PASSWORD is not set, so a first install works straight away


class Config:
    def __init__(self):
        # Services (Plex is required; the rest are optional but recommended)
        self.plex_url = _s("PLEX_URL").rstrip("/")
        self.plex_token = _s("PLEX_TOKEN")
        # Where "open in Plex" links go. Blank = Plex's hosted web app (works from anywhere you're signed in).
        # For LAN-only use: http://SERVER-IP:32400/web
        self.plex_link_base = _s("PLEX_LINK_BASE").rstrip("/")
        self.plex_libraries_raw = _s("PLEX_LIBRARIES")
        self.plex_libraries = [x.strip().lower() for x in self.plex_libraries_raw.split(",") if x.strip()]
        self.tautulli_url = _s("TAUTULLI_URL").rstrip("/")
        self.tautulli_key = _s("TAUTULLI_API_KEY")
        self.radarr_url = _s("RADARR_URL").rstrip("/")
        self.radarr_key = _s("RADARR_API_KEY")
        self.sonarr_url = _s("SONARR_URL").rstrip("/")
        self.sonarr_key = _s("SONARR_API_KEY")
        # Seerr = Overseerr / Jellyseerr / Seerr (same API)
        self.seerr_url = _s("SEERR_URL").rstrip("/")
        self.seerr_key = _s("SEERR_API_KEY")
        self.verify_tls = _b("VERIFY_TLS", True)

        # Behaviour
        self.dry_run = _b("DRY_RUN", True)  # SAFE BY DEFAULT: nothing is deleted until you set DRY_RUN=false
        self.watched_percent = _i("WATCHED_PERCENT", 85)  # an episode counts as "watched" at this % complete
        self.max_batch = _i("MAX_DELETE_BATCH", 200)
        self.history_days = _i("HISTORY_YEAR_DAYS", 365)
        self.http_timeout = _i("HTTP_TIMEOUT", 60)
        self.imdb_base = _s("IMDB_BASE_URL", "https://www.imdb.com").rstrip("/")
        self.mdblist_base = _s("MDBLIST_BASE_URL", "https://mdblist.com").rstrip("/")
        self.mdblist_api = _s("MDBLIST_API_URL", "https://api.mdblist.com").rstrip("/")
        self.tmdb_image_base = _s("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org").rstrip("/")  # posters for the Missing list
        self.mdblist_key = _s("MDBLIST_API_KEY")        # optional: only needed if a list isn't reachable publicly

        # Web app / auth
        self.app_password = _s("APP_PASSWORD") or ("" if _b("AUTH_DISABLED", False) else DEFAULT_PASSWORD)
        self.default_password = self.app_password == DEFAULT_PASSWORD   # quick-start default; the UI nags until it is changed
        self.auth_disabled = _b("AUTH_DISABLED", False)
        self.secret_key = _s("SECRET_KEY")
        self.cookie_secure = _b("COOKIE_SECURE", False)  # set true behind HTTPS (Cloudflare tunnel)
        self.refresh_hours = max(0, min(168, _i("REFRESH_HOURS", 0)))  # 0 = only when you click Refresh data
        self.leaving_raw = {}  # "Leaving Soon" row settings (saved from the Settings page)
        self.tb_raw = {}  # "released on this day" row settings (saved from the Settings page)
        self.trust_proxy = _b("TRUST_PROXY", False)  # honour X-Forwarded-* / CF-Connecting-IP
        self.config_dir = _s("CONFIG_DIR", "/config")

    # Settings that can be edited in the web UI (they override environment variables).
    URL_FIELDS = ("plex_url", "tautulli_url", "radarr_url", "sonarr_url", "seerr_url", "plex_link_base")
    SECRET_FIELDS = ("plex_token", "tautulli_key", "radarr_key", "sonarr_key", "seerr_key")

    def apply(self, d):
        """Apply a dict of UI-editable settings (from settings.json or a form) onto this config."""
        for k in self.URL_FIELDS:
            if isinstance(d.get(k), str):
                setattr(self, k, d[k].strip().rstrip("/"))
        for k in self.SECRET_FIELDS:
            if isinstance(d.get(k), str):
                setattr(self, k, d[k].strip())
        if isinstance(d.get("refresh_hours"), str) and d["refresh_hours"].strip().isdigit():
            self.refresh_hours = max(0, min(168, int(d["refresh_hours"].strip())))
        for k in ("throwback_enabled", "throwback_name", "throwback_scope", "throwback_min_years", "throwback_max_years",
                  "throwback_min_rating", "throwback_limit", "throwback_order", "throwback_pin", "throwback_before", "throwback_after",
                  "throwback_genres", "throwback_genre_mode", "throwback_tags"):
            if isinstance(d.get(k), str):
                self.tb_raw[k] = d[k].strip()
        for k in ("leaving_enabled", "leaving_name", "leaving_days", "leaving_pin"):
            if isinstance(d.get(k), str):
                self.leaving_raw[k] = d[k].strip()
        if isinstance(d.get("plex_libraries"), str):
            self.plex_libraries_raw = d["plex_libraries"].strip()
            self.plex_libraries = [x.strip().lower() for x in self.plex_libraries_raw.split(",") if x.strip()]

    def problems(self):
        out = []
        if not self.app_password and not self.auth_disabled:
            out.append("APP_PASSWORD is required (or set AUTH_DISABLED=true if something else protects the app)")
        return out

    def services(self):
        return {
            "plex": bool(self.plex_url and self.plex_token),
            "tautulli": bool(self.tautulli_url and self.tautulli_key),
            "radarr": bool(self.radarr_url and self.radarr_key),
            "sonarr": bool(self.sonarr_url and self.sonarr_key),
            "seerr": bool(self.seerr_url and self.seerr_key),
        }
