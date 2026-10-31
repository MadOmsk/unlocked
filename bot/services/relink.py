"""Linking an account that somebody — possibly somebody else — already has
(#52, step 2).

Since achievements belong to the account rather than to the person holding
it, linking is no longer a harmless overwrite. Three things can happen at
once, and a person deserves to know which before it does:

- they swap their own account for a different one, and everything the first
  account earned stops counting for them;
- they link an account the bot already knows, and its whole history becomes
  theirs without a single request to the platform;
- they take an account somebody else currently holds, and that person loses
  it. **Only with proof that it is theirs** (#169): Microsoft's sign-in, Steam's
  (`steam/proof.py`), a PSN account's own token. A link or a nickname proves
  nothing, so those doors refuse an account `held_by_other` names.

This module answers "what would happen" and then performs it, and tells
whoever lost the account through `notifier` (they may have no Telegram). It
knows nothing about Telegram: handlers do the asking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.constants import AccountPlatform, Platform
from bot.db.repo import PlatformLink, Repo

if TYPE_CHECKING:
    from bot.services.notifier import Notifier

log = logging.getLogger(__name__)

# Set by main.py once the notifications exist.
notifier: Notifier | None = None


@dataclass(slots=True)
class LinkPreview:
    """What linking `external_id` would do to this person right now."""

    current: PlatformLink | None
    """The account they hold on this platform today, if any."""

    current_achievements: int
    """How many rows that account has — what they are about to stop seeing."""

    incoming_achievements: int
    """How many the new account already has stored — what they gain for free,
    and the signal that a full backfill is unnecessary."""

    taken_from: int | None
    """Who currently holds the incoming account, when it is not this person."""

    incoming_id: str
    """The account being linked."""

    held_by_other: int | None = None
    """The person holding it, when it is somebody else: `taken_from` is their
    Telegram id, which a person who signed in by email does not have."""

    @property
    def is_switch(self) -> bool:
        """True only for a genuine identity change of *this person's* own
        account. Relinking the same account — after a failure, or just
        running /connect_steam twice — is not a switch."""
        return self.current is not None and self.current.external_id != self.incoming_id

    @property
    def needs_confirmation(self) -> bool:
        """Ask before anything irreversible-looking happens: swapping one's
        own account, or taking one that somebody else holds (2026-09-12,
        user request — a takeover used to go through silently, with only
        the loser finding out afterwards).

        A plain first link, and a relink of the same account, both stay
        silent. Those are the common cases and a prompt there is noise.
        """
        return self.is_switch or self.taken_from is not None or self.held_by_other is not None


async def preview(repo: Repo, tg_id: int, platform: str, external_id: str) -> LinkPreview:
    if platform == AccountPlatform.PSN:
        # PSN adds an account beside the others (#10) instead of swapping:
        # nothing is lost, so the only "current" worth naming is the incoming
        # account itself, when this person already holds it.
        current = next(
            (
                link
                for link in await repo.platform_links_for(await repo.person_id(tg_id), platform)
                if link.external_id == external_id
            ),
            None,
        )
    else:
        current = await repo.get_platform_link(await repo.person_id(tg_id), platform)
    owner = await repo.account_owner(platform, external_id)
    return LinkPreview(
        current=current,
        current_achievements=(
            await repo.account_achievement_count(platform, current.external_id) if current else 0
        ),
        incoming_achievements=await repo.account_achievement_count(platform, external_id),
        taken_from=owner if owner is not None and owner != tg_id else None,
        incoming_id=external_id,
        held_by_other=await held_by_other(repo, await repo.person_id(tg_id), platform, external_id),
    )


async def held_by_other(
    repo: Repo, person_id: int | None, platform: str, external_id: str
) -> int | None:
    """The person holding this account, when it is not `person_id`."""
    holder = await repo.account_holder(platform, external_id)
    return holder if holder is not None and holder != person_id else None


async def perform(
    repo: Repo, tg_id: int, platform: str, external_id: str, display_name: str | None
) -> int | None:
    """Link it, and return the tg_id it was taken from, if anyone.

    A taken-over Xbox account also loses the previous owner's refresh token:
    it authorises reading *that account*, which is no longer theirs, and
    leaving it would keep the bot polling an account on behalf of someone who
    does not hold it.
    """
    return await link_person(repo, await repo.person_id(tg_id), platform, external_id, display_name)


async def link_person(
    repo: Repo, person_id: int, platform: str, external_id: str, display_name: str | None
) -> int | None:
    """`perform` by person: link it and tell whoever lost it, by whichever way
    they hear (a notice never stops the linking). Returns their Telegram id."""
    holder = await held_by_other(repo, person_id, platform, external_id)
    taken_from = await repo.link_platform_account(person_id, platform, external_id, display_name)
    if holder is None:
        return taken_from
    if platform == AccountPlatform.XBOX:
        await repo.delete_token(holder)
    if notifier is not None:
        try:
            await notifier.notify(
                holder,
                "account_taken",
                platform=_PLATFORM_NAMES.get(platform, platform),
                name=display_name or external_id,
            )
        except Exception:
            log.exception("could not tell person %s their account moved", holder)
    return taken_from


# In a notice, whatever the language: the platforms' own names.
_PLATFORM_NAMES = {Platform.STEAM: "Steam", Platform.PSN: "PlayStation", "xbox": "Xbox"}
