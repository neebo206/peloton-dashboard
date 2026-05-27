"""Peloton unofficial API client."""

import json
from pathlib import Path
from datetime import datetime

import requests

BASE_URL = "https://api.onepeloton.com"

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
        self._load_config(Path(config_path))

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self, path: Path) -> None:
        if not path.exists():
            example = path.parent / "config.example.json"
            raise FileNotFoundError(
                f"No config file found at {path}.\n"
                f"Copy {example} → {path} and fill in your credentials.\n"
                f"To switch profiles, change 'active_profile' in that file."
            )
        with open(path) as f:
            config = json.load(f)

        active = config["active_profile"]
        profile = config["profiles"][active]
        self._email = profile.get("email", "")
        self._password = profile.get("password", "")
        self._session_cookie = profile.get("session_cookie")
        self._access_token = profile.get("access_token")
        self._user_id_override = profile.get("user_id")
        print(f"Profile : {active}  ({self._email or 'token auth'})")

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> "PelotonClient":
        if self._access_token:
            self.session.headers["Authorization"] = f"Bearer {self._access_token}"
            self.user_id = self._user_id_override or self._user_id_from_jwt(self._access_token)
            print(f"Logged in via Bearer token — user_id: {self.user_id}")
            return self

        if self._session_cookie:
            self.session.cookies.set("peloton_session_id", self._session_cookie, domain=".onepeloton.com")
            self.user_id = self._user_id_override
            print(f"Logged in via session cookie — user_id: {self.user_id}")
            return self

        resp = self.session.post(
            f"{BASE_URL}/auth/login",
            json={"username_or_email": self._email, "password": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        self.user_id = data["user_id"]
        print(f"Logged in — user_id: {self.user_id}")
        return self

    @staticmethod
    def _user_id_from_jwt(token: str) -> str:
        import base64, json as _json
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # re-pad
        claims = _json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("http://onepeloton.com/user_id", "")

    # ------------------------------------------------------------------
    # Workouts
    # ------------------------------------------------------------------

    def get_workouts(self, limit: int = 20, page: int = 0) -> list[dict]:
        """Return up to *limit* cycling workouts, most recent first."""
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
        # Keep only cycling sessions (filter client-side so the join works)
        return [w for w in workouts if w.get("fitness_discipline") == "cycling"]

    # ------------------------------------------------------------------
    # Per-workout data
    # ------------------------------------------------------------------

    def get_performance_graph(self, workout_id: str) -> dict:
        """Second-by-second metrics for a completed workout."""
        resp = self.session.get(
            f"{BASE_URL}/api/workout/{workout_id}/performance_graph",
            params={"every_n": 1},
        )
        resp.raise_for_status()
        return resp.json()

    def get_target_metrics(self, ride_id: str) -> dict | None:
        """
        Instructor cadence/resistance targets for a ride class.
        Returns the target_metrics_data dict from ride details, or None.
        """
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
