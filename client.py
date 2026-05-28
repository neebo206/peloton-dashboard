"""Peloton unofficial API client."""

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
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
        """True if the token is a 3-part JWT that doesn't expire within margin_s seconds."""
        if not token or token.count(".") != 2:
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
    # HTTP-based Auth0 PKCE login — no browser needed
    # ------------------------------------------------------------------

    @staticmethod
    def get_token_via_http(email: str, password: str) -> str:
        """
        Authenticate via Auth0 cross-origin auth + PKCE using plain HTTP.
        Bypasses PerimeterX entirely because there is no browser to fingerprint.
        Raises RuntimeError on failure.
        """
        _DOMAIN    = "auth.onepeloton.com"
        _CLIENT_ID = _AUTH0_CLIENT
        _REDIRECT  = "https://members.onepeloton.com/"

        # PKCE
        code_verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)

        sess = requests.Session()
        sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin":  "https://members.onepeloton.com",
            "Referer": "https://members.onepeloton.com/",
        })

        # Step 1 — cross-origin authenticate → login_ticket
        print("[peloton] HTTP auth: calling /co/authenticate", flush=True)
        co = sess.post(
            f"https://{_DOMAIN}/co/authenticate",
            json={
                "client_id":       _CLIENT_ID,
                "username":        email,
                "password":        password,
                "credential_type": "http://auth0.com/oauth/grant-type/password-realm",
                "realm":           "Username-Password-Authentication",
            },
        )
        print(f"[peloton] HTTP auth: /co/authenticate -> {co.status_code} {co.text[:200]}", flush=True)
        if co.status_code != 200:
            raise RuntimeError(
                f"Auth0 /co/authenticate failed ({co.status_code}): {co.text[:300]}"
            )
        co_data = co.json()
        ticket = co_data.get("login_ticket")
        if not ticket:
            raise RuntimeError(f"/co/authenticate returned no login_ticket: {co_data}")

        # Step 2 — exchange ticket for auth code via /authorize (follow redirect manually)
        auth = sess.get(
            f"https://{_DOMAIN}/authorize",
            params={
                "client_id":            _CLIENT_ID,
                "response_type":        "code",
                "redirect_uri":         _REDIRECT,
                "scope":                "openid profile email",
                "state":                state,
                "code_challenge":       code_challenge,
                "code_challenge_method":"S256",
                "login_ticket":         ticket,
                "referrer":             _REDIRECT,
            },
            allow_redirects=False,
        )
        location = auth.headers.get("Location", "")
        if "code=" not in location:
            raise RuntimeError(
                f"/authorize didn't redirect with code "
                f"({auth.status_code}): {location[:300]}"
            )
        code = urllib.parse.parse_qs(
            urllib.parse.urlparse(location).query
        ).get("code", [None])[0]
        if not code:
            raise RuntimeError(f"No code found in redirect: {location[:300]}")

        # Step 3 — exchange code for access_token
        tok = sess.post(
            f"https://{_DOMAIN}/oauth/token",
            json={
                "grant_type":    "authorization_code",
                "client_id":     _CLIENT_ID,
                "code":          code,
                "redirect_uri":  _REDIRECT,
                "code_verifier": code_verifier,
            },
        )
        if tok.status_code != 200:
            raise RuntimeError(
                f"Token exchange failed ({tok.status_code}): {tok.text[:300]}"
            )
        access_token = tok.json().get("access_token")
        if not access_token:
            raise RuntimeError(f"No access_token in token response: {tok.text[:200]}")
        return access_token

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
            # Only capture tokens from Peloton's actual data API to avoid
            # picking up internal auth/analytics JWTs from auth.onepeloton.com
            if "api.onepeloton.com" not in req.url:
                return
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer ") and not captured:
                tok = auth[7:]
                print(f"[peloton] token from {req.url[:60]}, len={len(tok)}", flush=True)
                captured.append(tok)

        chromium_path = PelotonClient._find_chromium()
        if not chromium_path:
            import subprocess, sys
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=False, capture_output=True,
            )

        # Start a virtual display so Chrome can run in headed mode,
        # bypassing PerimeterX headless-browser detection.
        import os as _os, shutil as _shutil, subprocess as _sp, time as _tm
        xvfb_proc = None
        if _shutil.which("Xvfb") and not _os.environ.get("DISPLAY"):
            xvfb_proc = _sp.Popen(
                ["Xvfb", ":99", "-screen", "0", "1280x800x24"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            _tm.sleep(0.5)
            _os.environ["DISPLAY"] = ":99"
            print("[peloton] Xvfb started, Chrome running headed", flush=True)
        else:
            print(f"[peloton] no Xvfb (which={_shutil.which('Xvfb')!r} "
                  f"DISPLAY={_os.environ.get('DISPLAY')!r}), headless", flush=True)

        headed = bool(_os.environ.get("DISPLAY"))

        try:
            with sync_playwright() as pw:
                launch_kwargs: dict = {
                    "headless": not headed,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                }
                if chromium_path:
                    launch_kwargs["executable_path"] = chromium_path
                browser = pw.chromium.launch(**launch_kwargs)
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                page = ctx.new_page()
                try:
                    from playwright_stealth import stealth_sync
                    stealth_sync(page)
                    print("[peloton] playwright-stealth applied", flush=True)
                except ImportError:
                    page.add_init_script(
                        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                    )
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
                    print(f"[peloton] filling form at {page.url[:80]}", flush=True)
                    email_input = page.locator('input[name="usernameOrEmail"]')
                    email_input.wait_for(state="visible", timeout=10_000)
                    email_input.click()
                    email_input.press_sequentially(email, delay=50)
                    pwd_input = page.locator('input[name="password"]')
                    pwd_input.click()
                    pwd_input.press_sequentially(password, delay=50)
                    pwd_input.press("Enter")
                    # Don't check for a specific URL — the Auth0 redirect
                    # chain may pass through domains that wouldn't match.
                    # Just wait for the network to settle.
                    try:
                        page.wait_for_load_state("networkidle", timeout=60_000)
                    except PWTimeout:
                        pass

                try:
                    page.wait_for_load_state("networkidle", timeout=30_000)
                except PWTimeout:
                    pass

                import re as _re, sys as _sys

                print(f"[peloton] post-login url={page.url[:80]}", flush=True)

                if not captured:
                    # Trigger a direct API fetch from inside the page so the SPA's
                    # auth interceptor adds the Bearer header — on_request will catch it.
                    try:
                        page.evaluate(
                            "async () => { try { await fetch("
                            "'https://api.onepeloton.com/api/me',"
                            " {credentials:'include'}); } catch(e) {} }"
                        )
                        page.wait_for_timeout(3_000)
                    except Exception:
                        pass

                if not captured:
                    # Use Playwright's storage_state() — more reliable than
                    # page.evaluate because it reads directly from the browser context.
                    try:
                        storage = ctx.storage_state()
                        for origin in storage.get("origins", []):
                            print(f"[peloton] storage origin={origin['origin']}, "
                                  f"ls_keys={[i['name'][:40] for i in origin.get('localStorage', [])]}", flush=True)
                            for item in origin.get("localStorage", []):
                                val = item.get("value", "")
                                m = _re.search(
                                    r'"access_token"\s*:\s*"(eyJ[A-Za-z0-9._-]{100,})"', val
                                )
                                if m:
                                    tok = m.group(1)
                                    print(f"[peloton] access_token found in storage_state "
                                          f"len={len(tok)}", flush=True)
                                    captured.append(tok)
                                    break
                            if captured:
                                break
                        if not captured:
                            print("[peloton] no access_token in storage_state", flush=True)
                    except Exception as exc:
                        print(f"[peloton] storage_state error: {exc}", flush=True)

                _sys.stdout.flush()
                browser.close()

        except Exception as exc:
            raise RuntimeError(f"Browser login failed: {exc}")
        finally:
            if xvfb_proc:
                xvfb_proc.terminate()
                _os.environ.pop("DISPLAY", None)

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
