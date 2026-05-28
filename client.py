"""Peloton unofficial API client."""

import base64
import json
import time
from pathlib import Path

import requests

BASE_URL = "https://api.onepeloton.com"

_AUTH0_URL    = "https://auth.onepeloton.com/oauth/token"
_AUTH0_CLIENT = "WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM"

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://members.onepeloton.com",
    "Referer": "https://members.onepeloton.com/",
    "Peloton-Platform": "web",
}


class PelotonClient:
    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config_path: str | Path = "config.json"):
        """Create a client backed by config.json (legacy / CLI path)."""
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self.user_id: str | None = None
        self._config_path = Path(config_path)
        self._load_config()

    @classmethod
    def from_token(cls, access_token: str) -> "PelotonClient":
        """Create a client directly from a Bearer token — no config.json needed."""
        instance = object.__new__(cls)
        instance.session = requests.Session()
        instance.session.headers.update(_HEADERS)
        instance.user_id = None
        instance._apply_token(access_token)
        return instance

    # ------------------------------------------------------------------
    # Config (used by the config-file path only)
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        path = self._config_path
        if not path.exists():
            example = path.parent / "config.example.json"
            raise FileNotFoundError(
                f"No config file found at {path}.\n"
                f"Copy {example} → {path} and fill in your credentials.\n"
                f"To switch profiles, change 'active_profile' in that file."
            )
        with open(path) as f:
            config = json.load(f)

        self._config = config
        active = config["active_profile"]
        profile = config["profiles"][active]
        self._email         = profile.get("email", "")
        self._password      = profile.get("password", "")
        self._access_token  = profile.get("access_token")
        self._refresh_token = profile.get("refresh_token")

    def _save_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        active = self._config["active_profile"]
        self._config["profiles"][active]["access_token"] = access_token
        if refresh_token:
            self._config["profiles"][active]["refresh_token"] = refresh_token
        with open(self._config_path, "w") as f:
            json.dump(self._config, f, indent=2)
        self._access_token = access_token
        if refresh_token:
            self._refresh_token = refresh_token

    # ------------------------------------------------------------------
    # JWT / token helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _jwt_claims(token: str) -> dict:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    @staticmethod
    def token_valid(token: str | None, margin_s: int = 300) -> bool:
        """True if the token exists and doesn't expire within margin_s seconds."""
        if not token:
            return False
        try:
            exp = PelotonClient._jwt_claims(token).get("exp", 0)
            return time.time() < exp - margin_s
        except Exception:
            return False

    def _apply_token(self, token: str) -> None:
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.user_id = self._jwt_claims(token).get("http://onepeloton.com/user_id", "")

    # ------------------------------------------------------------------
    # Playwright login — standalone, callable without a client instance
    # ------------------------------------------------------------------

    @staticmethod
    def _find_chromium() -> str | None:
        import shutil
        for name in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
            path = shutil.which(name)
            if path:
                return path
        return None

    @staticmethod
    def get_token_via_playwright(email: str, password: str) -> str:
        """
        Log in via a headless browser and return the Bearer token.
        Raises RuntimeError on failure.
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        captured: list[str] = []

        def on_request(req):
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer ") and not captured:
                captured.append(auth[7:])

        try:
            with sync_playwright() as pw:
                chromium_path = PelotonClient._find_chromium()
                launch_kwargs: dict = {"headless": True}
                if chromium_path:
                    launch_kwargs["executable_path"] = chromium_path
                browser = pw.chromium.launch(**launch_kwargs)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.on("request", on_request)

                page.goto("https://members.onepeloton.com/login",
                          wait_until="domcontentloaded", timeout=30_000)

                # SSO may auto-login without showing the form
                try:
                    page.wait_for_url(
                        lambda url: "members.onepeloton.com" in url and "/login" not in url,
                        timeout=6_000,
                    )
                except PWTimeout:
                    # Fill the login form manually
                    page.locator('input[name="usernameOrEmail"]').fill(email, timeout=10_000)
                    page.locator('input[name="password"]').fill(password)
                    page.locator('button[type="submit"]').first.click()
                    page.wait_for_url(
                        lambda url: "members.onepeloton.com" in url and "/login" not in url,
                        timeout=30_000,
                    )

                page.wait_for_load_state("networkidle", timeout=30_000)

                if not captured:
                    page.goto("https://members.onepeloton.com/profile", timeout=30_000)
                    page.wait_for_load_state("networkidle", timeout=30_000)

                browser.close()

        except PWTimeout as exc:
            raise RuntimeError(f"Browser login timed out: {exc}")
        except Exception as exc:
            raise RuntimeError(f"Browser login failed: {exc}")

        if not captured:
            raise RuntimeError("Login succeeded but could not capture Bearer token.")

        return captured[0]

    # ------------------------------------------------------------------
    # Config-file login (CLI / legacy path)
    # ------------------------------------------------------------------

    def login(self) -> "PelotonClient":
        if self.token_valid(self._access_token):
            self._apply_token(self._access_token)
            return self

        # Try refresh token
        if self._refresh_token:
            resp = requests.post(_AUTH0_URL, json={
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id":     _AUTH0_CLIENT,
            })
            if resp.status_code == 200:
                data = resp.json()
                self._save_tokens(data["access_token"], data.get("refresh_token"))
                self._apply_token(data["access_token"])
                return self

        # Playwright
        if self._email and self._password:
            token = self.get_token_via_playwright(self._email, self._password)
            self._save_tokens(token)
            self._apply_token(token)
            return self

        raise RuntimeError(
            "Authentication failed. Ensure 'email' and 'password' are set in config.json."
        )

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def get_workouts(self, limit: int = 20, page: int = 0) -> list[dict]:
        resp = self.session.get(
            f"{BASE_URL}/api/user/{self.user_id}/workouts",
            params={
                "limit": limit,
                "page": page,
                "sort_by": "-created",
                "joins": "ride,ride.instructor",
            },
        )
        resp.raise_for_status()
        workouts = resp.json().get("data", [])
        return [w for w in workouts if w.get("fitness_discipline") == "cycling"]

    def get_performance_graph(self, workout_id: str) -> dict:
        resp = self.session.get(
            f"{BASE_URL}/api/workout/{workout_id}/performance_graph",
            params={"every_n": 1},
        )
        resp.raise_for_status()
        return resp.json()

    def get_target_metrics(self, ride_id: str) -> dict | None:
        if not ride_id or set(ride_id) == {"0"}:
            return None
        resp = self.session.get(f"{BASE_URL}/api/ride/{ride_id}/details", timeout=10)
        resp.raise_for_status()
        tmd = resp.json().get("target_metrics_data")
        if tmd and tmd.get("target_metrics"):
            return tmd
        return None
