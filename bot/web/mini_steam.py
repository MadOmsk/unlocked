"""Proving a Steam account in the browser (#169): the Mini App asks for Steam's
sign-in page, and Steam sends the person back to a page of ours. The rules are
`services/steam/proof.py`'s.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from bot.db.repo import Repo
from bot.i18n import DEFAULT_LOCALE, translator
from bot.services.steam.proof import RETURN_PATH, SteamProofError

log = logging.getLogger(__name__)


def setup(app: web.Application) -> None:
    app.router.add_post("/api/mini/connect/steam/proof", handle_start)
    app.router.add_get(RETURN_PATH, handle_return)


async def handle_start(request: web.Request) -> web.Response:
    from bot.web.mini_api import _require_user

    user = await _require_user(request)
    steam_auth = request.app["mini_steam_auth"]
    proof = steam_auth.proof if steam_auth is not None else None
    if proof is None or not proof.available:
        return web.json_response({"ok": False, "error": "not_configured"}, status=400)
    return web.json_response({"ok": True, "url": proof.start(user.person_id)})


async def handle_return(request: web.Request) -> web.Response:
    """No sign-in here: the browser Steam sent back may not be the one that
    asked (Telegram opens it outside). The one-time state says who it is."""
    from bot.web.mini_api import _steam_backfill
    from bot.web.oauth import _page

    repo: Repo = request.app["mini_repo"]
    steam_auth = request.app["mini_steam_auth"]
    if steam_auth is None or steam_auth.proof is None:
        raise web.HTTPServiceUnavailable(text="steam unavailable")
    params = dict(request.query)
    state = params.pop("state", "")
    try:
        account = await steam_auth.proof.finish(state, params)
    except SteamProofError as exc:
        locale = await repo.user_locale(exc.person_id) if exc.person_id else DEFAULT_LOCALE
        _ = translator("oauth", locale)
        return _page(_("oauth-failed-title"), _(f"oauth-steam-{exc.code.replace('_', '-')}"), 400)

    fetcher = request.app["mini_steam_fetcher"]
    if fetcher is not None:
        asyncio.create_task(  # noqa: RUF006
            _steam_backfill(fetcher, account.person_id, account.steam_id)
        )
    _ = translator("oauth", await repo.user_locale(account.person_id))
    return _page(_("oauth-steam-done-title", name=account.name), _("oauth-steam-done-text"))
