"""A shared post's link carries its card for a messenger (owner, 2026-10-30)."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.db.repo import Repo
from bot.db.repo._steam import StoredPatch
from bot.services import short_links
from bot.services.stores import StoreVersion
from bot.web.mini_api import setup_mini_api


@pytest.mark.asyncio
async def test_a_shared_post_has_its_card_and_leads_to_the_app(repo: Repo, settings) -> None:
    await repo.save_game_patches(
        10,
        [
            StoredPatch(
                gid="5", title="Live Update 2.3.0", published_at="2026-10-30T00:00:00Z",
                text_en="Fixed a crash.", title_ru=None, text_ru=None,
                image_url="https://clan.steamstatic.com/p.jpg",
            )
        ],
    )  # fmt: skip
    await repo.set_patch_translation(
        10, "5", of=("Live Update 2.3.0", "Fixed a crash."),
        title_ru="Обновление 2.3.0", text_ru="Исправлен вылет.",
    )  # fmt: skip
    app = web.Application()
    setup_mini_api(app, settings, repo)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        page = await (await client.get("/api/mini/share/news/10/5?b=/app/")).text()
        assert '<meta property="og:title" content="Обновление 2.3.0">' in page
        assert '<meta property="og:description" content="Исправлен вылет.">' in page
        assert '<meta property="og:image" content="https://clan.steamstatic.com/p.jpg">' in page
        assert 'url=/app/?n=10%3A5"' in page
        # Never another site's address as the way on.
        page = await (await client.get("/api/mini/share/news/10/5?b=//evil.example/")).text()
        assert 'url=/?n=10%3A5"' in page
        assert (await client.get("/api/mini/share/news/10/404")).status == 404
    finally:
        await client.close()


def test_a_slug_is_plain_and_the_code_alone_finds() -> None:
    assert short_links.slug("Hades II: Élysée!") == "hades-ii-elysee"
    assert short_links.slug("Ведьмак") == ""
    assert len(short_links.slug("word " * 40)) <= 40
    assert short_links.path_of("game", "Hades II", "k3F9xQ") == "/g/hades-ii-k3F9xQ"
    assert short_links.path_of("game", "Ведьмак", "k3F9xQ") == "/g/k3F9xQ"
    assert short_links.code_of("another-name-k3F9xQ") == "k3F9xQ"
    assert short_links.code_of("k3F9xQ") == "k3F9xQ"
    assert short_links.code_of("too-short-k3") is None
    assert short_links.profile_handle("Name-4821") == ("Name", 4821)
    assert short_links.profile_handle("Name") == ("Name", 0)
    assert short_links.profile_handle("Name-x") is None


async def _save_achievement(repo: Repo, achievement_id: str, *, secret: int) -> None:
    await repo._conn.execute(
        "INSERT INTO title_achievements (platform, title_id, achievement_id, name_en,"
        " description_en, is_secret, updated_at) VALUES ('steam', '10', ?, ?, ?, ?, 'now')",
        (achievement_id, f"Name {achievement_id}", f"Text {achievement_id}", secret),
    )
    await repo._conn.commit()


@pytest.mark.asyncio
async def test_one_thing_has_one_code_and_nothing_is_made_for_what_does_not_exist(
    repo: Repo,
) -> None:
    await _save_achievement(repo, "A1", secret=0)
    target, name = await repo.short_link_source("achievement", "steam:10:A1")
    assert (target, name) == ("steam:10:A1", "Name A1")
    first = await repo.short_link_code("achievement", target)
    assert await repo.short_link_code("achievement", target) == first
    assert await repo.short_link_target("achievement", first) == target
    # A code is of its kind.
    assert await repo.short_link_target("game", first) is None
    assert await repo.short_link_source("achievement", "steam:10:nope") is None
    assert await repo.short_link_source("game", "999") is None
    assert await repo.short_link_source("game", "not a number") is None


@pytest.mark.asyncio
async def test_a_secret_achievement_stays_one_on_its_public_card(repo: Repo, settings) -> None:
    await _save_achievement(repo, "OPEN", secret=0)
    await _save_achievement(repo, "HID", secret=1)
    # A secret's name stays out of the address too.
    assert (await repo.short_link_source("achievement", "steam:10:HID"))[1] is None
    open_code = await repo.short_link_code("achievement", "steam:10:OPEN")
    secret_code = await repo.short_link_code("achievement", "steam:10:HID")
    app = web.Application()
    setup_mini_api(app, settings, repo)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        page = await (await client.get(f"/a/any-words-{open_code}")).text()
        assert '<meta property="og:title" content="Name OPEN">' in page
        assert "Text OPEN" in page
        assert "a=OPEN" in page
        page = await (await client.get(f"/a/{secret_code}")).text()
        assert "Name HID" not in page
        assert "Text HID" not in page
        assert (await client.get("/a/nosuch")).status == 404
        assert (await client.get(f"/g/{open_code}")).status == 404
        # Making a link takes a sign-in.
        response = await client.post("/api/mini/link", json={"kind": "achievement", "ref": "x"})
        assert response.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_profile_card_shows_a_nickname_and_nothing_else(repo: Repo, settings) -> None:
    await repo.ensure_user(77, "RideTheSun", "Real", "Name")
    person = await repo.person_id(77)
    await repo._conn.execute(
        "UPDATE users SET handle = 'RideTheSun', handle_norm = 'ridethesun', handle_number = 0"
        " WHERE id = ?",
        (person,),
    )
    await repo._conn.commit()
    app = web.Application()
    setup_mini_api(app, settings, repo)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        page = await (await client.get("/u/RideTheSun")).text()
        assert '<meta property="og:title" content="RideTheSun">' in page
        assert f"p={person}" in page
        assert "/u/RideTheSun/face" in page
        assert "Real" not in page
        assert "og:description" not in page
        assert (await client.get("/u/NoSuchPerson")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_every_kind_of_the_catalog_opens_its_game_and_outlives_a_rebuild(
    repo: Repo, settings
) -> None:
    await repo.save_version(
        StoreVersion(store="steam", product_id="200", console="steam"),
        platform="steam",
        title_id="200",
    )
    conn = repo._conn
    made = "'2026-10-31'"
    await conn.execute(
        "INSERT INTO achievement_list_connectors (platform, title_id, name, updated_at)"
        " VALUES ('steam', '200', 'Hades II list', '2026-10-31') ON CONFLICT DO NOTHING"
    )

    async def number(sql: str) -> int:
        return int((await (await conn.execute(sql)).fetchone())[0])

    version = await number("SELECT version_id FROM versions")
    game = await number(
        f"INSERT INTO games (name, anchor_version_id, created_at, updated_at)"
        f" VALUES ('Hades II', {version}, {made}, {made}) RETURNING game_id"
    )
    await conn.execute("UPDATE versions SET game_id = ?", (game,))
    dlc = await number(
        f"INSERT INTO dlcs (version_id, store_id, name, updated_at)"
        f" VALUES ({version}, 'D1', 'Add-on', {made}) RETURNING dlc_id"
    )
    edition = await number(
        f"INSERT INTO editions (store, store_id, name, first_seen_at, last_seen_at)"
        f" VALUES ('steam', 'E1', 'Deluxe', {made}, {made}) RETURNING edition_id"
    )
    await conn.execute(
        "INSERT INTO edition_items (edition_id, store_id, item_kind, version_id)"
        " VALUES (?, '200', 'game', ?)",
        (edition, version),
    )
    series = await number(
        f"INSERT INTO series (name, created_at, updated_at)"
        f" VALUES ('Hades', {made}, {made}) RETURNING series_id"
    )
    await conn.execute(
        "INSERT INTO game_series (game_id, series_id, source, created_at, updated_at)"
        " VALUES (?, ?, 'manual', '2026-10-31', '2026-10-31')",
        (game, series),
    )
    await conn.execute(
        f"INSERT INTO hltb_entries (hltb_id, name, checked_at) VALUES (7, 'Hades 2', {made})"
    )
    await conn.execute(
        f"INSERT INTO version_hltb (version_id, hltb_id, source) VALUES ({version}, 7, 'manual')"
    )
    await conn.commit()

    refs = {
        "game": str(game),
        "version": str(version),
        "edition": str(edition),
        "series": str(series),
        "hltb": "7",
        "list": "steam:200",
    }
    paths = {}
    for kind, ref in refs.items():
        target, name = await repo.short_link_source(kind, ref)
        paths[kind] = short_links.path_of(kind, name, await repo.short_link_code(kind, target))
    assert paths["game"].startswith("/g/hades-ii-")
    target, _ = await repo.short_link_source("dlc", str(dlc))
    assert target == "steam:200:steam|s:D1"
    dlc_path = short_links.path_of("dlc", "Add-on", await repo.short_link_code("dlc", target))

    app = web.Application()
    setup_mini_api(app, settings, repo)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        for kind, path in paths.items():
            page = await (await client.get(path)).text()
            assert f"game={game}" in page, kind
        page = await (await client.get(dlc_path)).text()
        assert f"game={game}&amp;dlc={dlc}" in page

        # The catalog rebuilt: the game has a new id, and the link still finds it.
        rebuilt = await number(
            f"INSERT INTO games (name, created_at, updated_at)"
            f" VALUES ('Hades II', {made}, {made}) RETURNING game_id"
        )
        await conn.execute("UPDATE versions SET game_id = ?", (rebuilt,))
        await conn.commit()
        page = await (await client.get(paths["game"])).text()
        assert f"game={rebuilt}" in page
    finally:
        await client.close()
