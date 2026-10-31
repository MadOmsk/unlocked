"""An account somebody holds moves only with proof (#169): the Mini App's and
the bot's plain doors refuse it, Steam's sign-in and a PSN token take it, and
whoever lost it is told, Telegram or no Telegram."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.constants import Platform
from bot.db.repo import Repo
from bot.i18n import static_i18n
from bot.services import relink
from bot.services.crypto import TokenCipher
from bot.services.psn.auth import PsnAuth
from bot.services.psn.client import PsnProfile
from bot.services.steam import proof as proof_module
from bot.services.steam.auth import SteamAuth
from bot.services.steam.client import SteamProfile
from bot.services.steam.proof import OPENID_URL, SteamProof, SteamProofError, verified_steam_id
from bot.views.psn_token import render_mismatch
from bot.web import mini_steam
from bot.web.mini_auth import MiniAppUser

KEY = "jDzMk1H4D8_uG2bZqv0O1c0JxQb5ZQ0w6m3pX1Yf4kE="
STEAM_ID = "76561198000000001"
OTHER_ID = "76561198000000002"
RETURN_TO = "https://app.test/api/mini/auth/steam/return?state=abc"


def answer(steam_id: str = STEAM_ID, return_to: str = RETURN_TO) -> dict[str, str]:
    claimed = f"https://steamcommunity.com/openid/id/{steam_id}"
    return {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "id_res",
        "openid.op_endpoint": OPENID_URL,
        "openid.claimed_id": claimed,
        "openid.identity": claimed,
        "openid.return_to": return_to,
        "openid.response_nonce": "2026-10-31T00:00:00Zabc",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce",
        "openid.sig": "c2ln",
    }


@pytest.fixture
def steam_says(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What Steam answers `check_authentication`; the requests it got."""
    reply = ["ns:http://specs.openid.net/auth/2.0\nis_valid:true\n"]
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert "openid.mode=check_authentication" in request.content.decode()
        return httpx.Response(200, text=reply[0])

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(proof_module.httpx, "AsyncClient", client)
    return reply


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str, dict]]:
    heard: list[tuple[int, str, dict]] = []

    class Notifier:
        async def notify(self, recipient: int, kind: str, **data: object) -> None:
            heard.append((recipient, kind, data))

    monkeypatch.setattr(relink, "notifier", Notifier())
    return heard


@pytest.fixture
def public_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_profile(_key: str, steam_id: str) -> SteamProfile:
        return SteamProfile(steam_id, "Nick", True)

    monkeypatch.setattr(proof_module, "get_profile", get_profile)


def _proof(repo: Repo) -> SteamProof:
    auth = SteamAuth(repo, TokenCipher(KEY), env_key="key")
    return SteamProof(repo, auth, "https://app.test/app/")


def _return_to(url: str) -> tuple[str, str]:
    return_to = parse_qs(urlsplit(url).query)["openid.return_to"][0]
    return return_to, parse_qs(urlsplit(return_to).query)["state"][0]


async def test_a_signed_answer_gives_its_steam_id(steam_says) -> None:
    assert await verified_steam_id(answer(), RETURN_TO) == STEAM_ID


async def test_steam_refusing_its_own_signature_proves_nothing(steam_says) -> None:
    steam_says[0] = "is_valid:false\n"
    assert await verified_steam_id(answer(), RETURN_TO) is None


@pytest.mark.parametrize(
    "change",
    [
        {"openid.mode": "cancel"},
        {"openid.return_to": "https://evil.test/return"},
        {"openid.op_endpoint": "https://evil.test/openid/login"},
        {"openid.claimed_id": "https://evil.test/openid/id/76561198000000001"},
        {"openid.identity": "https://steamcommunity.com/openid/id/76561198000000002"},
        {"openid.signed": "signed,op_endpoint,return_to"},
    ],
)
async def test_a_forged_answer_is_refused_before_asking(change: dict[str, str]) -> None:
    # No `steam_says`: asking Steam here would fail the test (conftest).
    assert await verified_steam_id({**answer(), **change}, RETURN_TO) is None


async def test_steam_not_answering_is_not_a_refusal(monkeypatch) -> None:
    real = httpx.AsyncClient

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(
        proof_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(SteamProofError) as caught:
        await verified_steam_id(answer(), RETURN_TO)
    assert caught.value.code == "unavailable"


async def test_proof_moves_the_account_and_tells_the_holder(
    repo: Repo, steam_says, told, public_profile
) -> None:
    holder = await repo.ensure_user(1, None)
    await repo.link_platform_account(holder, Platform.STEAM, STEAM_ID, "Nick")
    taker = await repo.ensure_user(2, None)
    proof = _proof(repo)

    return_to, state = _return_to(proof.start(taker))
    account = await proof.finish(state, answer(return_to=return_to))

    assert account.person_id == taker
    assert await repo.account_holder(Platform.STEAM, STEAM_ID) == taker
    assert told == [(holder, "account_taken", {"platform": "Steam", "name": "Nick"})]


async def test_a_state_is_good_once(repo: Repo, steam_says, told, public_profile) -> None:
    person = await repo.ensure_user(1, None)
    proof = _proof(repo)
    return_to, state = _return_to(proof.start(person))
    await proof.finish(state, answer(return_to=return_to))
    with pytest.raises(SteamProofError) as caught:
        await proof.finish(state, answer(return_to=return_to))
    assert caught.value.code == "expired"


async def test_an_unknown_state_links_nobody(repo: Repo) -> None:
    with pytest.raises(SteamProofError) as caught:
        await _proof(repo).finish("nope", answer())
    assert caught.value.code == "expired"


async def test_proof_of_a_second_account_does_not_swap_the_first(
    repo: Repo, steam_says, told, public_profile
) -> None:
    person = await repo.ensure_user(1, None)
    await repo.link_platform_account(person, Platform.STEAM, OTHER_ID, "Mine")
    proof = _proof(repo)
    return_to, state = _return_to(proof.start(person))
    with pytest.raises(SteamProofError) as caught:
        await proof.finish(state, answer(return_to=return_to))
    assert caught.value.code == "already_linked"
    assert await repo.account_holder(Platform.STEAM, STEAM_ID) is None


async def _client(repo: Repo, person: int, monkeypatch, routes) -> TestClient:
    from bot.web import mini_api

    async def require_user(request: web.Request) -> MiniAppUser:
        return MiniAppUser(None, None, None, None, None, False, person_id=person)

    async def no_backfill(*_: object) -> None:
        return None

    monkeypatch.setattr(mini_api, "_require_user", require_user)
    monkeypatch.setattr(mini_api, "_steam_backfill", no_backfill)
    monkeypatch.setattr(mini_api, "_psn_backfill", no_backfill)
    steam_auth = SteamAuth(repo, TokenCipher(KEY), env_key="key")
    steam_auth.proof = SteamProof(repo, steam_auth, "https://app.test/app/")
    app = web.Application()
    app["mini_repo"] = repo
    app["mini_steam_auth"] = steam_auth
    app["mini_steam_fetcher"] = object()
    app["mini_psn_auth"] = PsnAuth(repo, TokenCipher(KEY))
    app["mini_psn_fetcher"] = object()
    mini_steam.setup(app)
    for path, handler in routes(mini_api).items():
        app.router.add_post(path, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_the_mini_app_refuses_somebody_elses_steam_by_its_link(
    repo: Repo, monkeypatch
) -> None:
    from bot.web import mini_api

    holder = await repo.ensure_user(1, None)
    await repo.link_platform_account(holder, Platform.STEAM, STEAM_ID, "Nick")
    # A holder who signed in by email has no Telegram id to compare.
    await repo._conn.execute("UPDATE users SET tg_id = NULL WHERE id = ?", (holder,))
    await repo._conn.commit()
    taker = await repo.ensure_user(2, None)

    async def resolve(_key: str, _raw: str) -> str:
        return STEAM_ID

    async def profile(_key: str, steam_id: str) -> SteamProfile:
        return SteamProfile(steam_id, "Nick", True)

    monkeypatch.setattr(mini_api, "resolve_steam_id", resolve)
    monkeypatch.setattr(mini_api, "get_profile", profile)
    client = await _client(
        repo, taker, monkeypatch, lambda m: {"/api/mini/connect/steam": m.handle_connect_steam}
    )
    try:
        res = await client.post("/api/mini/connect/steam", json={"steam_id": STEAM_ID})
        assert res.status == 409
        assert await res.json() == {"ok": False, "error": "taken", "proof": True}
        asked = await client.post("/api/mini/connect/steam/proof")
        assert (await asked.json())["url"].startswith(OPENID_URL)
    finally:
        await client.close()
    assert await repo.account_holder(Platform.STEAM, STEAM_ID) == holder


async def test_the_mini_app_refuses_somebody_elses_psn_by_its_nickname(
    repo: Repo, monkeypatch
) -> None:
    from bot.web import mini_api

    holder = await repo.ensure_user(1, None)
    await repo.link_platform_account(holder, Platform.PSN, "111", "Me")
    taker = await repo.ensure_user(2, None)

    async def status(_self: object) -> str:
        return "active"

    async def get_client(_self: object) -> object:
        return object()

    async def resolve(_client: object, _raw: str) -> PsnProfile:
        return PsnProfile("111", "Me")

    async def visible(_client: object, _account: str) -> bool:
        return True

    monkeypatch.setattr(PsnAuth, "status", status)
    monkeypatch.setattr(PsnAuth, "get_client", get_client)
    monkeypatch.setattr(mini_api, "resolve_profile", resolve)
    monkeypatch.setattr(mini_api, "is_trophy_visible", visible)
    client = await _client(
        repo, taker, monkeypatch, lambda m: {"/api/mini/connect/psn": m.handle_connect_psn}
    )
    try:
        res = await client.post("/api/mini/connect/psn", json={"online_id": "Me"})
        assert res.status == 409
        assert (await res.json())["error"] == "taken"
    finally:
        await client.close()
    assert await repo.account_holder(Platform.PSN, "111") == holder


async def test_a_free_account_still_links_by_its_link(repo: Repo, monkeypatch) -> None:
    from bot.web import mini_api

    person = await repo.ensure_user(2, None)

    async def resolve(_key: str, _raw: str) -> str:
        return STEAM_ID

    async def profile(_key: str, steam_id: str) -> SteamProfile:
        return SteamProfile(steam_id, "Nick", True)

    monkeypatch.setattr(mini_api, "resolve_steam_id", resolve)
    monkeypatch.setattr(mini_api, "get_profile", profile)
    client = await _client(
        repo, person, monkeypatch, lambda m: {"/api/mini/connect/steam": m.handle_connect_steam}
    )
    try:
        res = await client.post("/api/mini/connect/steam", json={"steam_id": STEAM_ID})
        assert res.status == 200
    finally:
        await client.close()
    assert await repo.account_holder(Platform.STEAM, STEAM_ID) == person


async def test_the_return_page_links_and_words_a_refusal(
    repo: Repo, steam_says, told, public_profile, monkeypatch
) -> None:
    person = await repo.ensure_user(2, None)
    client = await _client(repo, person, monkeypatch, lambda m: {})
    try:
        bad = await client.get("/api/mini/auth/steam/return", params={"state": "nope"})
        assert bad.status == 400
        asked = await client.post("/api/mini/connect/steam/proof")
        return_to, state = _return_to((await asked.json())["url"])
        good = await client.get(
            "/api/mini/auth/steam/return",
            params={"state": state, **answer(return_to=return_to)},
        )
        assert good.status == 200
        assert "Nick" in await good.text()
    finally:
        await client.close()
    assert await repo.account_holder(Platform.STEAM, STEAM_ID) == person


@pytest.mark.parametrize("held", [False, True])
def test_a_token_of_another_account_warns_and_offers_it(held: bool) -> None:
    screen = render_mismatch("Mine", "Theirs", held=held, i18n=static_i18n("psn", "en"))
    assert "Theirs" in screen.text
    assert ("will lose it" in screen.text) is held
    buttons = [b.callback_data for row in screen.keyboard.inline_keyboard for b in row]
    assert buttons == ["psn:switch:yes", "psn:switch:no"]
