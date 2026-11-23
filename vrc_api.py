"""Thin client for the (unofficial, community-documented) VRChat API.

Docs: https://vrchat.community/ (OpenAPI spec at https://github.com/vrchatapi/specification)

Notes
-----
* VRChat does not offer a public, officially supported API. Be gentle:
  identify yourself with a User-Agent, cache the session, avoid hammering it.
* Most endpoints, including world lookup, need a logged-in session.
* Your password is NEVER stored. Only the auth cookies are cached (file
  permissions 0600 on Linux) so you don't have to log in + 2FA every launch.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

API_BASE_URL = "https://api.vrchat.cloud/api/1"
USER_AGENT = "VRCWorldChecker/0.1 (World safety checker)" #This is to tell the API "what is this thing using our API??"

REQUEST_TIMEOUT_SECONDS = 15
MAX_REQUEST_ATTEMPTS = 3
HTTP_TOO_MANY_REQUESTS = 429

# Cookies worth keeping between runs. "twoFactorAuth" lets later logins skip 2FA.
PERSISTED_COOKIE_NAMES = ("auth", "twoFactorAuth")

WORLD_ID_PATTERN = re.compile(
    r"wrld_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


# ---- errors ------------------------------------------------------------------
class VRCError(Exception):
    """Anything that went wrong talking to VRChat (message is user-presentable)."""


class AuthRequired(VRCError):
    """Not logged in, or the saved session expired."""


class TwoFactorRequired(VRCError):
    """Login needs a 2FA code. `methods` is e.g. ['totp', 'otp'] or ['emailOtp']."""

    def __init__(self, methods: list[str]):
        super().__init__("Two-factor authentication code required")
        self.methods = methods


# ---- small helpers -----------------------------------------------------------
def config_dir() -> Path:
    """Per-user config folder that works on both Windows and Linux."""
    if os.name == "nt":
        default_base = Path.home() / "AppData" / "Roaming"
        base_directory = Path(os.environ.get("APPDATA") or default_base)
    else:
        default_base = Path.home() / ".config"
        base_directory = Path(os.environ.get("XDG_CONFIG_HOME") or default_base)

    app_directory = base_directory / "vrc-world-checker"
    app_directory.mkdir(parents=True, exist_ok=True)
    return app_directory


def extract_world_id(text: str) -> Optional[str]:
    """Accepts a bare ID or any text/URL containing one (e.g. a vrchat.com/home/world/... link)."""
    match = WORLD_ID_PATTERN.search(text or "")
    if match is None:
        return None
    return match.group(0).lower()


def _build_basic_auth_header(username: str, password: str) -> str:
    """VRChat wants URL-encoded credentials, joined with ':' and base64-encoded."""
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded_credentials}"


def _pick_two_factor_endpoint(methods: list[str], code: str) -> str:
    """Choose the verification endpoint that matches how the user receives codes."""
    if "emailOtp" in methods:
        return "/auth/twofactorauth/emailotp/verify"

    looks_like_authenticator_code = code.isdigit() and len(code) == 6
    if "totp" in methods and looks_like_authenticator_code:
        return "/auth/twofactorauth/totp/verify"

    return "/auth/twofactorauth/otp/verify"  # recovery code


def _parse_json_body(response: requests.Response) -> dict:
    """Return the JSON body as a dict. A top-level list is wrapped as {'_list': [...]}."""
    try:
        body = response.json()
    except ValueError:
        return {}
    if isinstance(body, dict):
        return body
    return {"_list": body}


# ---- client ------------------------------------------------------------------
class VRChatClient:
    def __init__(self) -> None:
        self.http_session = requests.Session()
        self.http_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.current_user: Optional[dict] = None
        self._session_file = config_dir() / "session.json"

    # -- low-level requests --
    def _send_request(self, method: str, path: str, **request_kwargs) -> requests.Response:
        """Send a request, backing off and retrying when VRChat rate-limits us."""
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                response = self.http_session.request(
                    method,
                    API_BASE_URL + path,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    **request_kwargs,
                )
            except requests.RequestException as network_error:
                raise VRCError(f"Network error: {network_error}") from network_error

            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                time.sleep(2 * attempt)
                continue
            return response

        raise VRCError("VRChat is rate-limiting requests (HTTP 429). Wait a minute and retry.")

    # -- session persistence --
    def _save_session(self) -> None:
        cookies_to_keep = {
            cookie.name: cookie.value
            for cookie in self.http_session.cookies
            if cookie.name in PERSISTED_COOKIE_NAMES
        }
        try:
            self._session_file.write_text(json.dumps(cookies_to_keep))
            os.chmod(self._session_file, 0o600)  # effectively a no-op on Windows
        except OSError:
            pass  # not being able to cache the session is not fatal

    def _load_saved_cookies(self) -> bool:
        """Put cached cookies into the HTTP session. Returns False if there are none."""
        try:
            saved_cookies = json.loads(self._session_file.read_text())
        except (OSError, ValueError):
            return False

        for cookie_name, cookie_value in saved_cookies.items():
            self.http_session.cookies.set(cookie_name, cookie_value, domain="api.vrchat.cloud", path="/")
        return True

    def logout(self) -> None:
        self.http_session.cookies.clear()
        self.current_user = None
        try:
            self._session_file.unlink()
        except OSError:
            pass

    def resume(self) -> Optional[dict]:
        """Try to reuse the cached cookies. Returns the user dict, or None if that didn't work."""
        if not self._load_saved_cookies():
            return None

        try:
            response = self._send_request("GET", "/auth/user")
        except VRCError:
            return None

        user = _parse_json_body(response)
        if response.status_code == 200 and user.get("id"):
            self.current_user = user
            return user
        return None

    # -- authentication --
    def login(self, username: str, password: str) -> dict:
        auth_header = _build_basic_auth_header(username, password)
        response = self._send_request("GET", "/auth/user", headers={"Authorization": auth_header})

        if response.status_code == 401:
            raise VRCError("Login failed: wrong username or password.")
        if response.status_code != 200:
            raise VRCError(f"Login failed (HTTP {response.status_code}).")

        user = _parse_json_body(response)
        required_methods = user.get("requiresTwoFactorAuth")
        if required_methods:
            raise TwoFactorRequired(list(required_methods))

        self.current_user = user
        self._save_session()
        return user

    def verify_2fa(self, methods: list[str], code: str) -> dict:
        code = code.strip()
        endpoint = _pick_two_factor_endpoint(methods, code)

        response = self._send_request("POST", endpoint, json={"code": code})
        was_verified = response.status_code == 200 and _parse_json_body(response).get("verified")
        if not was_verified:
            raise VRCError("That 2FA code was not accepted.")

        # Confirm the session now works before we report success.
        response = self._send_request("GET", "/auth/user")
        user = _parse_json_body(response)
        if response.status_code != 200 or not user.get("id"):
            raise VRCError("2FA accepted but the session could not be confirmed.")

        self.current_user = user
        self._save_session()
        return user

    # -- data --
    def get_world(self, world_id: str) -> dict:
        response = self._send_request("GET", f"/worlds/{world_id}")

        if response.status_code == 401:
            raise AuthRequired("Not logged in (or the session expired). Please log in again.")
        if response.status_code == 404:
            raise VRCError("World not found. It may be private, deleted, or the ID is wrong.")
        if response.status_code != 200:
            raise VRCError(f"VRChat returned HTTP {response.status_code} for this world.")
        return _parse_json_body(response)

    def get_author_worlds(self, author_id: str, limit: int = 100) -> list[dict]:
        """Other public worlds by the same author. Best effort: returns [] on any failure."""
        search_params = {
            "userId": author_id,
            "n": limit,
            "sort": "updated",
            "releaseStatus": "public",
        }
        try:
            response = self._send_request("GET", "/worlds", params=search_params)
        except VRCError:
            return []

        if response.status_code != 200:
            return []

        worlds = _parse_json_body(response).get("_list", [])
        return [world for world in worlds if isinstance(world, dict)]
