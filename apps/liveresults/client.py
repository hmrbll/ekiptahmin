"""Thin HTTP client for the football-data.org REST API (v4).

Single responsibility: fetch match data over HTTP and hand back parsed JSON.
No knowledge of our models — mapping/scoring live in mapping.py / sync.py so
this stays trivially mockable in tests and swappable if the provider changes.

Auth is the `X-Auth-Token` header. Config comes from settings:
- FOOTBALL_DATA_API_KEY     (required to make any call)
- FOOTBALL_DATA_BASE_URL    (default https://api.football-data.org/v4)
- FOOTBALL_DATA_COMPETITION (default "WC" — the World Cup competition code)
"""

from __future__ import annotations

import requests
from django.conf import settings

DEFAULT_BASE_URL = "https://api.football-data.org/v4"
DEFAULT_COMPETITION = "WC"
DEFAULT_TIMEOUT = 10  # seconds


class FootballDataError(RuntimeError):
    """Raised when the API call fails (missing key, HTTP error, bad payload).

    `status_code` is the HTTP status when one is available (None for transport
    errors / missing key), so callers can special-case 403 (competition not on
    plan) or 429 (rate limit) if they want.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FootballDataClient:
    """Minimal football-data.org v4 client.

    Construct with no args to read config from Django settings, or pass an
    explicit `api_key` / `base_url` (handy in tests).
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, "FOOTBALL_DATA_API_KEY", "")
        self.base_url = (base_url or getattr(settings, "FOOTBALL_DATA_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout or DEFAULT_TIMEOUT

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key:
            raise FootballDataError(
                "FOOTBALL_DATA_API_KEY is not set — add it to your .env (dev) "
                "or the Render environment (prod)."
            )
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.get(
                url,
                headers={"X-Auth-Token": self.api_key},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FootballDataError(f"Request to {url} failed: {exc}") from exc

        if resp.status_code != 200:
            # football-data error bodies look like {"message": "...", "errorCode": 403}
            message = resp.text
            try:
                message = resp.json().get("message", message)
            except ValueError:
                pass
            raise FootballDataError(
                f"football-data {resp.status_code} for {url}: {message}",
                status_code=resp.status_code,
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise FootballDataError(f"Non-JSON response from {url}") from exc

    def get_competition_matches(
        self,
        competition: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Return the raw `matches` list for a competition.

        `date_from`/`date_to` are YYYY-MM-DD strings (football-data caps the
        window at ~10 days). `status` filters server-side (e.g. "FINISHED",
        "IN_PLAY,PAUSED"). Returns [] when the API reports no matches.
        """
        competition = competition or getattr(settings, "FOOTBALL_DATA_COMPETITION", DEFAULT_COMPETITION)
        params = {}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if status:
            params["status"] = status
        data = self._get(f"/competitions/{competition}/matches", params=params)
        return data.get("matches", [])
