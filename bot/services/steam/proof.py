"""Proving a Steam account is one's own with Steam's own sign-in, OpenID (#169).

An account somebody else holds moves only to whoever proves it is theirs: a
link or a nickname proves nothing, since anybody can type one. The person
signs in on Steam's site, which hands back a SteamID signed by Steam, and
nothing else: no token, no password, nothing to keep. Asked of Steam directly
(`check_authentication`), so a forged answer is refused by the one who would
have signed it.

Knows nothing about Telegram or the web server: the door a person comes in by
words the answer.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx

from bot.constants import Platform
from bot.db.repo import Repo
from bot.services import relink
from bot.services.steam.client import SteamApiError, get_profile
from bot.util import origin_of

if TYPE_CHECKING:
    from bot.services.steam.auth import SteamAuth

log = logging.getLogger(__name__)

OPENID_URL = "https://steamcommunity.com/openid/login"
RETURN_PATH = "/api/mini/auth/steam/return"
CLAIMED_ID = re.compile(r"https://steamcommunity\.com/openid/id/(7656\d{13})")
STATE_TTL_SECONDS = 600
REQUEST_TIMEOUT = 15.0
# What the answer's signature must cover, or a signed answer could carry
# another account's id beside it.
MUST_BE_SIGNED = frozenset({"claimed_id", "identity", "return_to", "response_nonce"})


class SteamProofError(Exception):
    """A refusal, as a code: `expired`, `refused`, `unavailable`,
    `already_linked`, `private`, `cooldown`, `not_configured`."""

    def __init__(self, code: str, *, person_id: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.person_id = person_id


@dataclass(slots=True)
class ProvedAccount:
    person_id: int
    steam_id: str
    name: str


async def verified_steam_id(params: dict[str, str], return_to: str) -> str | None:
    """The SteamID an OpenID answer proves, or None. Raises `unavailable` when
    Steam could not be asked: that says nothing of the answer."""
    if params.get("openid.mode") != "id_res" or params.get("openid.return_to") != return_to:
        return None
    if params.get("openid.op_endpoint") != OPENID_URL:
        return None
    claimed = params.get("openid.claimed_id", "")
    match = CLAIMED_ID.fullmatch(claimed)
    if match is None or params.get("openid.identity") != claimed:
        return None
    if not MUST_BE_SIGNED <= set(params.get("openid.signed", "").split(",")):
        return None

    check = {k: v for k, v in params.items() if k.startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(OPENID_URL, data=check)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("steam openid: could not ask Steam: %r", exc)
        raise SteamProofError("unavailable") from exc
    lines = (line.strip() for line in response.text.splitlines())
    return match.group(1) if "is_valid:true" in lines else None


class SteamProof:
    def __init__(self, repo: Repo, steam_auth: SteamAuth, app_url: str | None) -> None:
        self._repo = repo
        self._steam_auth = steam_auth
        self._origin = origin_of(app_url) if app_url else None
        self._pending: dict[str, tuple[int, float]] = {}

    @property
    def available(self) -> bool:
        return self._origin is not None

    def start(self, person_id: int) -> str:
        """Where to send this person. The state ties Steam's answer to them:
        the answer itself says only which account signed in."""
        if self._origin is None:
            raise SteamProofError("not_configured")
        self._forget_expired()
        state = secrets.token_urlsafe(24)
        self._pending[state] = (person_id, time.monotonic())
        params = {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "checkid_setup",
            "openid.return_to": self._return_to(state),
            "openid.realm": self._origin,
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        }
        return f"{OPENID_URL}?{urlencode(params)}"

    async def finish(self, state: str, params: dict[str, str]) -> ProvedAccount:
        """Links the account Steam proved, whoever holds it now: the state is
        spent by any answer, so a link is good once."""
        self._forget_expired()
        pending = self._pending.pop(state, None)
        if pending is None:
            raise SteamProofError("expired")
        person_id = pending[0]
        try:
            return await self._link(person_id, state, params)
        except SteamProofError as exc:
            exc.person_id = person_id
            raise

    async def _link(self, person_id: int, state: str, params: dict[str, str]) -> ProvedAccount:
        steam_id = await verified_steam_id(params, self._return_to(state))
        if steam_id is None:
            raise SteamProofError("refused")

        current = await self._repo.get_platform_link(person_id, Platform.STEAM)
        if current is not None and current.external_id != steam_id:
            raise SteamProofError("already_linked")

        api_key = await self._steam_auth.get_key()
        if api_key is None:
            raise SteamProofError("not_configured")
        try:
            profile = await get_profile(api_key, steam_id)
        except SteamApiError as exc:
            log.info("steam openid: no profile for a proved account: %s", exc)
            raise SteamProofError("unavailable") from exc
        if not profile.is_public:
            raise SteamProofError("private")

        tg_id = await self._repo.tg_id_of(person_id)
        cooldown = await self._repo.check_platform_cooldown(tg_id, Platform.STEAM, steam_id)
        if cooldown.is_blocked:
            raise SteamProofError("cooldown")

        await relink.link_person(
            self._repo, person_id, Platform.STEAM, steam_id, profile.persona_name
        )
        log.info("steam openid: person %s proved and linked %s", person_id, steam_id)
        return ProvedAccount(person_id, steam_id, profile.persona_name)

    def _return_to(self, state: str) -> str:
        return f"{self._origin}{RETURN_PATH}?{urlencode({'state': state})}"

    def _forget_expired(self) -> None:
        now = time.monotonic()
        for state, (_, created) in list(self._pending.items()):
            if now - created > STATE_TTL_SECONDS:
                del self._pending[state]
