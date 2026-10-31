"""A link to share that Telegram (and every other messenger) can draw as a
card (owner, 2026-10-30): the app is one page with no picture or title of a
post's own, so a shared link pointed at it showed nothing. The link points
here instead — a tiny public page carrying the post's Open Graph tags (the
game and the title, the start of the text, the post's picture or its game's
art), which sends a person on to the post in the app at once.

Public, as the post is on Steam: a messenger's crawler carries no sign-in.
The pictures are the stores' own addresses, which any crawler can fetch.

Short links (#244) carry the same card for everything of the catalog and for a
person's profile. A secret achievement shows only its game, a profile only a
nickname and a face.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from aiohttp import web

from bot.services import game_page, images, short_links

if TYPE_CHECKING:
    from bot.db.repo import Repo

# Where the app lives on this host: a path of our own, never another site.
_APP_PATH = re.compile(r"^/[A-Za-z0-9/_-]*$")
_DESCRIPTION_CHARS = 200


def setup_share_routes(app: web.Application) -> None:
    app.router.add_get("/api/mini/share/news/{appid}/{gid}", handle_news_share)
    app.router.add_post("/api/mini/link", handle_make_link)
    prefixes = "|".join(short_links.KIND_OF_PREFIX)
    app.router.add_get(rf"/{{prefix:{prefixes}}}/{{segment:[^/]+}}", handle_short_link)
    app.router.add_get("/u/{segment:[^/]+}", handle_profile_link)
    app.router.add_get("/u/{segment:[^/]+}/face", handle_profile_face)


def _app_address(request: web.Request) -> tuple[str, str]:
    """The app's origin and its path on this host, from what the server is
    configured with, never from the request's query."""
    url = urlsplit(request.app["mini_settings"].mini_app_url or "")
    origin = f"{url.scheme}://{url.netloc}" if url.netloc else f"{request.scheme}://{request.host}"
    return origin, url.path.rstrip("/") + "/"


async def handle_make_link(request: web.Request) -> web.Response:
    """The link to share of a thing the app shows. Signed in, since it writes:
    only a thing that exists gets a code."""
    from bot.web.mini_api import _require_user

    await _require_user(request)
    repo: Repo = request.app["mini_repo"]
    body = await request.json()
    kind, ref = str(body.get("kind") or ""), str(body.get("ref") or "")
    origin, _ = _app_address(request)
    if kind == "person":
        person = await repo.get_user(int(ref)) if ref.isdigit() else None
        if person is None or not person.handle:
            raise web.HTTPNotFound(text="no such person")
        return web.json_response({"url": origin + short_links.profile_path(person.handle)})
    if kind not in short_links.KINDS:
        raise web.HTTPBadRequest(text="bad kind")
    source = await repo.short_link_source(kind, ref)
    if source is None:
        raise web.HTTPNotFound(text="no such thing")
    target, name = source
    code = await repo.short_link_code(kind, target)
    return web.json_response({"url": origin + short_links.path_of(kind, name, code)})


async def handle_short_link(request: web.Request) -> web.Response:
    repo: Repo = request.app["mini_repo"]
    kind = short_links.KIND_OF_PREFIX[request.match_info["prefix"]]
    code = short_links.code_of(request.match_info["segment"])
    target = await repo.short_link_target(kind, code) if code else None
    card = await _card(repo, kind, target) if target else None
    if card is None:
        raise web.HTTPNotFound(text="no such link")
    _, base = _app_address(request)
    return _page(
        heading=card["title"] or "Unlocked",
        description=card["text"],
        image=card["image"],
        target=f"{base}?{urlencode(card['query'])}",
    )


async def _card(repo: Repo, kind: str, target: str) -> dict[str, Any] | None:
    """A thing's public card: its title, text and picture, and where it opens."""
    game_id = await repo.short_link_game(kind, target)
    game = (await repo.game_cards([game_id])).get(game_id) if game_id is not None else None
    game_name = (game.get("name_ru") or game.get("name")) if game else None
    art = game_page.store_art(game["media"], game["icons"]) if game else {}
    picture = art.get("hero") or art.get("poster")
    if kind == "achievement":
        return await _achievement_card(repo, target, game_name, picture)
    if kind == "news":
        return await _news_card(repo, target, game_name, picture)
    if game_id is None:
        return None
    if kind == "dlc":
        dlc = await repo.short_link_dlc(target)
        if dlc is None:
            return None
        return {
            "title": dlc["name_ru"] or dlc["name"],
            "text": dlc["description_en"],
            "image": dlc["image_url"] or picture,
            "query": {"game": game_id, "dlc": dlc["dlc_id"]},
        }
    # A thing with no screen of its own opens its game.
    named = await repo.short_link_named(kind, target)
    return {"title": named or game_name, "text": None, "image": picture, "query": {"game": game_id}}


async def _achievement_card(
    repo: Repo, target: str, game_name: str | None, picture: str | None
) -> dict[str, Any] | None:
    ach = await repo.short_link_achievement(target)
    if ach is None:
        return None
    platform, title_id, achievement_id = target.split(":", 2)
    query = {"g": f"{platform}:{title_id}", "a": achievement_id}
    # A secret stays one for whoever gets the link: its game alone.
    if ach["is_secret"]:
        return {"title": game_name, "text": None, "image": picture, "query": query}
    icon = ach["icon_url"]
    return {
        "title": ach["name_ru"] or ach["name_en"],
        "text": ach["description_ru"] or ach["description_en"],
        "image": images.current_url(icon) if icon else picture,
        "query": query,
    }


async def _news_card(
    repo: Repo, target: str, game_name: str | None, picture: str | None
) -> dict[str, Any] | None:
    from bot.web.mini_chat import _news_excerpt

    appid, _, gid = target.partition(":")
    post = await repo.game_patch(int(appid), gid)
    if post is None:
        return None
    title = post.title_ru or post.title
    text = post.text_ru if post.title_ru else post.text_en
    named = game_name and title.casefold().startswith(game_name.casefold())
    return {
        "title": f"{game_name} — {title}" if game_name and not named else title,
        "text": _news_excerpt(text) if text else None,
        "image": post.image_url or picture,
        "query": {"n": target},
    }


async def _profile_person(request: web.Request) -> int:
    repo: Repo = request.app["mini_repo"]
    handle = short_links.profile_handle(request.match_info["segment"])
    person = await repo.person_by_handle(*handle) if handle else None
    if person is None:
        raise web.HTTPNotFound(text="no such person")
    return person


async def handle_profile_link(request: web.Request) -> web.Response:
    """A nickname and a face, which are no activity (#157): nothing else about
    the person goes on a public page."""
    person = await _profile_person(request)
    user = await request.app["mini_repo"].get_user(person)
    origin, base = _app_address(request)
    return _page(
        heading=user.handle,
        description=None,
        image=f"{origin}/u/{request.match_info['segment']}/face",
        target=f"{base}?{urlencode({'p': person})}",
    )


async def handle_profile_face(request: web.Request) -> web.Response:
    """The face for a messenger's crawler, which carries no sign-in. Asked for by
    nickname, so it cannot be walked by person id."""
    from bot.web.mini_api import _avatar_of

    person = await _profile_person(request)
    user = await request.app["mini_repo"].get_user(person)
    return await _avatar_of(request, user, user.tg_id)


async def handle_news_share(request: web.Request) -> web.Response:
    """`?g=platform:title_id` the game the post was opened over, `?b=` the
    app's path on this host (`/` on the dev server, `/app/` in production)."""
    from bot.web.mini_chat import _news_excerpt

    repo: Repo = request.app["mini_repo"]
    try:
        appid = int(request.match_info["appid"])
    except ValueError as exc:
        raise web.HTTPNotFound(text="no such post") from exc
    gid = request.match_info["gid"]
    post = await repo.game_patch(appid, gid)
    if post is None:
        raise web.HTTPNotFound(text="no such post")

    game_name: str | None = None
    art: dict[str, str | None] = {}
    platform, _, title_id = (request.query.get("g") or "").partition(":")
    # The game the post was opened over, else the game its Steam app is.
    game_id = await repo.game_of_list(platform, title_id) if platform and title_id else None
    if game_id is None:
        game_id = await repo.game_of_steam_app(appid)
    card = (await repo.game_cards([game_id])).get(game_id) if game_id is not None else None
    if card:
        game_name = card.get("name_ru") or card.get("name")
        art = game_page.store_art(card["media"], card["icons"])
    title = post.title_ru or post.title
    text = post.text_ru if post.title_ru else post.text_en
    image = post.image_url or art.get("hero") or art.get("poster")

    base = request.query.get("b") or "/"
    if not _APP_PATH.match(base) or base.startswith("//"):
        base = "/"
    query = {"n": f"{appid}:{gid}"}
    if platform and title_id:
        query["g"] = f"{platform}:{title_id}"
    target = f"{base}?{urlencode(query)}"

    # The game first, unless the post's title already starts with it.
    named = game_name and title.casefold().startswith(game_name.casefold())
    heading = f"{game_name} — {title}" if game_name and not named else title
    return _page(
        heading=heading,
        description=_news_excerpt(text) if text else None,
        image=image,
        target=target,
    )


def _page(*, heading: str, description: str | None, image: str | None, target: str) -> web.Response:
    """A tiny page: the tags for a messenger, and a person sent on at once."""

    def tag(prop: str, value: str | None) -> str:
        if not value:
            return ""
        return f'<meta property="{prop}" content="{html.escape(value, quote=True)}">'

    page = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(heading)}</title>"
        f"{tag('og:type', 'article')}"
        f"{tag('og:site_name', 'Unlocked')}"
        f"{tag('og:title', heading)}"
        f"{tag('og:description', description[:_DESCRIPTION_CHARS] if description else None)}"
        f"{tag('og:image', image)}"
        '<meta name="twitter:card" content="summary_large_image">'
        f'<meta http-equiv="refresh" content="0;url={html.escape(target, quote=True)}">'
        f"<script>location.replace({_js_string(target)})</script>"
        "</head><body></body></html>"
    )
    return web.Response(text=page, content_type="text/html")


def _js_string(value: str) -> str:
    """A string for the page's one script, safe inside `<script>`."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\u003c")
    return f'"{escaped}"'
