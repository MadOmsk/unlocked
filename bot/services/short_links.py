"""What a short link looks like (#244): `/g/hades-ii-k3F9xQ`, a slug of the name
for reading and a code that alone finds the target, so a rename breaks nothing.
No database, no network."""

from __future__ import annotations

import re
import secrets
import string
import unicodedata

# The kinds of link, each its own first path segment.
KINDS = {
    "game": "g",
    "achievement": "a",
    "dlc": "d",
    "news": "n",
    "series": "s",
    "version": "v",
    "edition": "e",
    "hltb": "h",
    "list": "l",
}
KIND_OF_PREFIX = {prefix: kind for kind, prefix in KINDS.items()}

CODE_LENGTH = 6
_ALPHABET = string.ascii_letters + string.digits
_CODE = re.compile(rf"[A-Za-z0-9]{{{CODE_LENGTH}}}")
_SLUG_CHARS = 40


def new_code() -> str:
    """Random, so links cannot be walked in order."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def slug(name: str | None) -> str:
    """The name in plain Latin, cut at a word. A name in another alphabet has
    none: an address of percent signs reads worse than the code alone."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    if len(words) <= _SLUG_CHARS:
        return words
    cut = words[: _SLUG_CHARS + 1]
    return cut[: cut.rfind("-")] if "-" in cut else cut[:_SLUG_CHARS]


def path_of(kind: str, name: str | None, code: str) -> str:
    words = slug(name)
    return f"/{KINDS[kind]}/{words}-{code}" if words else f"/{KINDS[kind]}/{code}"


def code_of(segment: str) -> str | None:
    """The code at the end of a link's last segment; whatever stands before it
    is only for reading."""
    code = segment.rsplit("-", 1)[-1]
    return code if _CODE.fullmatch(code) else None


def profile_path(handle: str) -> str:
    """`RideTheSun#4821` as `/u/RideTheSun-4821`: `#` would start a fragment."""
    return "/u/" + handle.replace("#", "-")


def profile_handle(segment: str) -> tuple[str, int] | None:
    """A profile link's nickname and digits (0 for none)."""
    name, _, number = segment.partition("-")
    if not name or (number and not number.isdigit()):
        return None
    return name, int(number or 0)
