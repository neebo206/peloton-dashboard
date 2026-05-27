"""Peloton unofficial API client."""

import base64
import json
import time
from pathlib import Path

import requests

BASE_URL = "https://api.onepeloton.com"

# Auth0 config extracted from the Peloton web app JWT
_AUTH0_URL      = "https://auth.onepeloton.com/oauth/token"
_AUTH0_CLIENT   = "WVoJxVDdPoFx4RNewvvg6ch2mZ7bwnsM"
_AUTH0_AUDIENCE = "https://api.onepeloton.com/"
_AUTH0_SCOPE    = "openid profile email peloton-api.members:default offline_access"

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
    def __init__(self, config_path: str | Path = "config.json"):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self.user_id: str | None = None
        self._config_path = Path(config_path)
        self._load_config()

    # ------------------------------------------------------------------
    # Config
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
        print(f"Profile : {active}  ({self._email or 'token auth'})")

    def _save_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        """Persist updated tokens back to config.json."""
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
    # Auth helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _jwt_claims(token: str) -> dict:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    @staticmethod
    def _token_valid(token: str | None, margin_s: int = 300) -> bool:
        """True if token exists and doesn't expire within margin_s seconds."""
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

    def _try_refresh(self) -> bool:
        """Exchange refresh token for a new access token. Returns True on success."""
        if not self._refresh_token:
            return False
        resp = requests.post(_AUTH0_URL, json={
            "grant_type":    "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id":     _AUTH0_CLIENT,
        })
        if resp.status_code != 200:
            print(f"  Token refresh failed ({resp.status_code}) — will try password login.")
            return False
        data = resp.json()
        self._save_tokens(data["access_token"], data.get("refresh_token"))
        self._apply_token(data["access_token"])
        print(f"Logged in via token refresh — user_id: {self.user_id}")
        return True

    def _try_ropc(self) -> bool:
        """Auth0 Resource Owner Password Credentials flow. Returns True on success."""
        if not (self._email and self._password):
            return False
        resp = requests.post(_AUTH0_URL, json={
            "grant_type": "password",
            "username":   self._email,
            "password":   self._password,
            "client_id":  _AUTH0_CLIENT,
            "audience":   _AUTH0_AUDIENCE,
            "scope":      _AUTH0_SCOPE,
        })
        if resp.status_code != 200:
            return False
        data = resp.json()
        self._save_tokens(data["access_token"], data.get("refresh_token"))
        self._apply_token(data["access_token"])
        print(f"Logged in via Auth0 ROPC — user_id: {self.user_id}")
        return True

    def _playwright_login(self) -> bool:
        """Automate a real browser login and capture the Bearer token from network traffic."""
        if not (self._email and self._password):
            return False
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            print("  Playwright not installed. Run: pip install playwright && playwright install chromium")
            return False

        captured: list[str] = []

        def on_request(req):
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer ") and not captured:
                captured.append(auth[7:])

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.on("request", on_request)

                print("  Browser: navigating to Peloton login...")
                page.goto("https://members.onepeloton.com/login",
                          wait_until="domcontentloaded", timeout=30_000)

                # Peloton SSO often auto-logs in without showing the form.
                # Wait up to 6 s for that redirect before trying to fill credentials.
                try:
                    page.wait_for_url(
                        lambda url: "members.onepeloton.com" in url and "/login" not in url,
                        timeout=6_000,
                    )
                    print("  Auto-login via SSO detected.")
                except PWTimeout:
                    # Form is present — fill it manually
                    try:
                        page.locator('input[name="usernameOrEmail"]').fill(
                            self._email, timeout=10_000)
                        page.locator('input[name="password"]').fill(self._password)
                        page.locator('button[type="submit"]').first.click()
                        page.wait_for_url(
                            lambda url: "members.onepeloton.com" in url and "/login" not in url,
                            timeout=30_000,
                        )
                        print("  Logged in via credentials form.")
                    except PWTimeout as exc:
                        print(f"  Login form submission failed. URL: {page.url}\n  {exc}")
                        browser.close()
                        return False

                page.wait_for_load_state("networkidle", timeout=30_000)

                # Navigate to profile to trigger an authenticated API call
                if not captured:
                    page.goto("https://members.onepeloton.com/profile", timeout=30_000)
                    page.wait_for_load_state("networkidle", timeout=30_000)

                # Try to pull refresh token from Auth0 SPA SDK localStorage cache
                refresh_token = None
                try:
                    keys = page.evaluate("() => Object.keys(localStorage)")
                    key = next((k for k in keys if "auth0spajs" in k), None)
                    if key:
                        val = page.evaluate(f"() => JSON.parse(localStorage.getItem('{key}'))")
                        body = val.get("body", {})
                        if not captured and body.get("access_token"):
                            captured.append(body["access_token"])
                        refresh_token = body.get("refresh_token")
                except Exception:
                    pass

                browser.close()

        except PWTimeout as exc:
            print(f"  Playwright timed out: {exc}")
            return False
        except Exception as exc:
            print(f"  Playwright error: {exc}")
            return False

        if not captured:
            print("  Playwright: logged in but could not capture Bearer token.")
            return False

        self._save_tokens(captured[0], refresh_token)
        self._apply_token(captured[0])
        rt_note = " + refresh token" if refresh_token else ""
        print(f"Logged in via browser automation{rt_note} — user_id: {self.user_id}")
        return True

    # ------------------------------------------------------------------
    # Login (tries strategies in order)
    # ------------------------------------------------------------------

    def login(self) -> "PelotonClient":
        # 1. Stored token still valid — use it directly
        if self._token_valid(self._access_token):
            self._apply_token(self._access_token)
            print(f"Logged in via stored token — user_id: {self.user_id}")
            return self

        # 2. Refresh token — silent renewal, no credentials needed
        if self._try_refresh():
            return self

        # 3. Auth0 ROPC (often disabled — falls through silently)
        if self._try_ropc():
            return self

        # 4. Playwright browser automation
        if self._playwright_login():
            return self

        # 5. Expired stored token (last resort)
        if self._access_token:
            self._apply_token(self._access_token)
            print(f"Warning: using expired token — user_id: {self.user_id}")
            return self

        raise RuntimeError(
            "Authentication failed. Ensure 'email' and 'password' are set in config.json, "
            "or paste a fresh 'access_token' from the browser."
        )

    # ------------------------------------------------------------------
    # Workouts
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

    # ------------------------------------------------------------------
    # Per-workout data
    # ------------------------------------------------------------------

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

    def get_ride_details(self, ride_id: str) -> dict:
        resp = self.session.get(f"{BASE_URL}/api/ride/{ride_id}/details")
        resp.raise_for_status()
        return resp.json()
