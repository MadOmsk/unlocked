"""Short links (#244): a thing's stable key and its code, and what a key names
now. A version's or a game's id can change when the catalog is rebuilt, so a
target is what outlives that: a store product, a list, a store's own id."""

from __future__ import annotations

from typing import Any

from bot.services.short_links import new_code
from bot.util import utcnow_iso

_VERSION_KEY = "v.store || ':' || v.product_id || ':' || v.console"

# A thing named by a number in the app, as its stable target and its name.
_SOURCES = {
    "game": (
        f"SELECT {_VERSION_KEY} AS target, g.name FROM games g"
        " JOIN versions v ON v.version_id = g.anchor_version_id WHERE g.game_id = ?"
    ),
    "version": (
        f"SELECT {_VERSION_KEY} AS target, g.name FROM versions v"
        " LEFT JOIN games g ON g.game_id = v.game_id WHERE v.version_id = ?"
    ),
    "dlc": (
        f"SELECT {_VERSION_KEY} || '|' || CASE"
        "  WHEN d.store_id IS NOT NULL THEN 's:' || d.store_id"
        "  WHEN d.trophy_group_id IS NOT NULL THEN 't:' || d.trophy_group_id"
        "  END AS target, d.name FROM dlcs d"
        " JOIN versions v ON v.version_id = d.version_id WHERE d.dlc_id = ?"
    ),
    "edition": "SELECT store || ':' || store_id AS target, name FROM editions WHERE edition_id = ?",
    "series": "SELECT series_id::text AS target, name FROM series WHERE series_id = ?",
    "hltb": "SELECT hltb_id::text AS target, name FROM hltb_entries WHERE hltb_id = ?",
}


def _number(ref: str) -> int | None:
    return int(ref) if ref.isascii() and ref.isdigit() and len(ref) < 19 else None


class _ShortLinksRepo:
    async def short_link_code(self, kind: str, target: str) -> str:
        """One code for one thing, whoever shares it first."""
        for _ in range(5):
            await self._conn.execute(
                "INSERT INTO short_links (code, kind, target, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (new_code(), kind, target, utcnow_iso()),
            )
            await self._conn.commit()
            cursor = await self._conn.execute(
                "SELECT code FROM short_links WHERE kind = ? AND target = ?", (kind, target)
            )
            row = await cursor.fetchone()
            if row:
                return str(row["code"])
        raise RuntimeError("no free short link code")

    async def short_link_target(self, kind: str, code: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT target FROM short_links WHERE code = ? AND kind = ?", (code, kind)
        )
        row = await cursor.fetchone()
        return str(row["target"]) if row else None

    async def short_link_source(self, kind: str, ref: str) -> tuple[str, str | None] | None:
        """A thing as the app names it, as a stable target and the name for the
        address; None when there is no such thing, so nothing is stored for it."""
        if kind in ("list", "achievement", "news"):
            return await self._source_by_key(kind, ref)
        number = _number(ref)
        if number is None or kind not in _SOURCES:
            return None
        cursor = await self._conn.execute(_SOURCES[kind], (number,))
        row = await cursor.fetchone()
        return (str(row["target"]), row["name"]) if row and row["target"] else None

    async def _source_by_key(self, kind: str, ref: str) -> tuple[str, str | None] | None:
        """These are their own stable keys. A secret's name stays out of the address."""
        size, query = {
            "list": (
                2,
                "SELECT name FROM achievement_list_connectors WHERE platform = ? AND title_id = ?",
            ),
            "achievement": (
                3,
                "SELECT CASE WHEN is_secret = 0 THEN name_en END AS name FROM title_achievements"
                " WHERE platform = ? AND title_id = ? AND achievement_id = ?",
            ),
            "news": (
                2,
                "SELECT title AS name FROM game_patches WHERE steam_appid = ? AND gid = ?",
            ),
        }[kind]
        key: list[Any] = ref.split(":", size - 1)
        if len(key) != size or not all(key):
            return None
        if kind == "news":
            key[0] = _number(key[0])
            if key[0] is None:
                return None
        cursor = await self._conn.execute(query, tuple(key))
        row = await cursor.fetchone()
        return (ref, row["name"]) if row else None

    async def short_link_game(self, kind: str, target: str) -> int | None:
        """The game a target belongs to now: where a link opens when the thing
        has no screen of its own, and whose art its card shows."""
        if kind in ("list", "achievement"):
            platform, title_id = target.split(":", 2)[:2]
            return await self.game_of_list(platform, title_id)
        if kind == "news":
            return await self.game_of_steam_app(int(target.split(":", 1)[0]))
        if kind == "dlc":
            dlc = await self.short_link_dlc(target)
            game_id = dlc["game_id"] if dlc else None
        else:
            game_id = await self._game_by_target(kind, target)
        return await self.live_game_id(int(game_id)) if game_id is not None else None

    async def _game_by_target(self, kind: str, target: str) -> int | None:
        if kind in ("game", "version"):
            query = (
                "SELECT game_id FROM versions WHERE store = ? AND product_id = ? AND console = ?"
            )
            args: tuple[Any, ...] = tuple([*target.split(":", 2), "", ""][:3])
        elif kind == "edition":
            query = (
                "SELECT v.game_id FROM editions e"
                " JOIN edition_items i ON i.edition_id = e.edition_id"
                " JOIN versions v ON v.version_id = i.version_id"
                " WHERE e.store = ? AND e.store_id = ? AND v.game_id IS NOT NULL"
                " ORDER BY i.is_primary DESC, v.version_id LIMIT 1"
            )
            args = tuple(target.split(":", 1))
        elif kind == "series":
            query = (
                "SELECT gs.game_id FROM game_series gs"
                " JOIN series s ON s.series_id = gs.series_id"
                " WHERE gs.member = 1 AND gs.series_id = ?"
                " ORDER BY gs.game_id = s.anchor_game_id DESC NULLS LAST, gs.game_id LIMIT 1"
            )
            args = (await self._live_series(int(target)),)
        elif kind == "hltb":
            query = (
                "SELECT v.game_id FROM version_hltb l"
                " JOIN versions v ON v.version_id = l.version_id"
                " WHERE l.hltb_id = ? AND l.source <> 'rejected' AND v.game_id IS NOT NULL"
                " ORDER BY l.source = 'manual' DESC, v.version_id LIMIT 1"
            )
            args = (int(target),)
        else:
            return None
        cursor = await self._conn.execute(query, args)
        row = await cursor.fetchone()
        return row["game_id"] if row else None

    async def _live_series(self, series_id: int) -> int:
        """A merged series leads on to the one it became."""
        for _ in range(8):
            cursor = await self._conn.execute(
                "SELECT merged_into FROM series WHERE series_id = ?", (series_id,)
            )
            row = await cursor.fetchone()
            if not row or row["merged_into"] is None:
                break
            series_id = int(row["merged_into"])
        return series_id

    async def short_link_dlc(self, target: str) -> dict[str, Any] | None:
        version, _, own = target.partition("|")
        store, product_id, console = [*version.split(":", 2), "", ""][:3]
        how, _, value = own.partition(":")
        column = {"s": "store_id", "t": "trophy_group_id"}.get(how)
        if column is None:
            return None
        cursor = await self._conn.execute(
            "SELECT d.dlc_id, d.name, d.name_ru, d.image_url, d.description_en, v.game_id"
            " FROM dlcs d JOIN versions v ON v.version_id = d.version_id"
            f" WHERE v.store = ? AND v.product_id = ? AND v.console = ? AND d.{column} = ?",
            (store, product_id, console, value),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def short_link_achievement(self, target: str) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT name_ru, name_en, description_ru, description_en, icon_url, is_secret"
            " FROM title_achievements"
            " WHERE platform = ? AND title_id = ? AND achievement_id = ?",
            tuple(target.split(":", 2)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def short_link_named(self, kind: str, target: str) -> str | None:
        """The name of a thing with no screen of its own, for its card."""
        query, args = {
            "series": ("SELECT COALESCE(name_ru, name) AS name FROM series WHERE series_id = ?", 1),
            "hltb": ("SELECT name FROM hltb_entries WHERE hltb_id = ?", 1),
            "edition": ("SELECT name FROM editions WHERE store = ? AND store_id = ?", 2),
            "list": (
                "SELECT COALESCE(name_ru, name) AS name FROM achievement_list_connectors"
                " WHERE platform = ? AND title_id = ?",
                2,
            ),
        }.get(kind, (None, 0))
        if query is None:
            return None
        key = (int(target),) if args == 1 else tuple(target.split(":", 1))
        cursor = await self._conn.execute(query, key)
        row = await cursor.fetchone()
        return row["name"] if row else None

    async def person_by_handle(self, name: str, number: int) -> int | None:
        cursor = await self._conn.execute(
            "SELECT id FROM users WHERE handle_norm = ? AND handle_number = ?",
            (name.lower(), number),
        )
        row = await cursor.fetchone()
        return int(row["id"]) if row else None
