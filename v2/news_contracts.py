"""Validated file contracts for the public team-news collector."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


EXPECTED_CLUBS = {
    "ARS", "AVL", "BOU", "BRE", "BHA", "CHE", "COV", "CRY", "EVE", "FUL",
    "HUL", "IPS", "LEE", "LIV", "MCI", "MUN", "NEW", "NFO", "SUN", "TOT",
}
SOURCE_TYPES = {"club_news", "club_press_conference", "premier_league"}
PARSERS = {"html_article_index", "html_page"}


def _object(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def load_sources(path: Path, *, require_all_clubs: bool = False) -> list[dict]:
    data = _object(path)
    rows = data.get("sources")
    if data.get("version") != 1 or not isinstance(rows, list):
        raise ValueError(f"{path}: expected version 1 and a sources list")
    ids: set[str] = set()
    clubs: set[str] = set()
    required = {"id", "publisher", "club", "url", "source_type", "parser", "enabled"}
    out = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: source {index} must be an object")
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"{path}: source {index} missing {', '.join(sorted(missing))}")
        source = dict(raw)
        if source["id"] in ids:
            raise ValueError(f"{path}: duplicate source id {source['id']}")
        if source["club"] not in EXPECTED_CLUBS:
            raise ValueError(f"{path}: unknown club {source['club']}")
        parsed = urlparse(str(source["url"]))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{path}: source {source['id']} needs a public HTTPS URL")
        if source["source_type"] not in SOURCE_TYPES or source["parser"] not in PARSERS:
            raise ValueError(f"{path}: unsupported source contract for {source['id']}")
        if not isinstance(source["enabled"], bool):
            raise ValueError(f"{path}: enabled must be boolean for {source['id']}")
        if not source["enabled"] and not source.get("unsupported_reason"):
            raise ValueError(f"{path}: disabled source {source['id']} needs unsupported_reason")
        ids.add(source["id"])
        clubs.add(source["club"])
        out.append(source)
    if require_all_clubs and clubs != EXPECTED_CLUBS:
        missing = sorted(EXPECTED_CLUBS - clubs)
        extra = sorted(clubs - EXPECTED_CLUBS)
        raise ValueError(f"{path}: club coverage mismatch; missing={missing}, extra={extra}")
    return out


def load_aliases(path: Path) -> list[dict]:
    data = _object(path)
    rows = data.get("players")
    if data.get("version") != 1 or not isinstance(rows, list):
        raise ValueError(f"{path}: expected version 1 and a players list")
    ids: set[int] = set()
    aliases: dict[tuple[str, str], int] = {}
    out = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: player {index} must be an object")
        missing = {"player_id", "club", "canonical", "aliases"} - raw.keys()
        if missing:
            raise ValueError(f"{path}: player {index} missing {', '.join(sorted(missing))}")
        row = dict(raw)
        pid = int(row["player_id"])
        if pid in ids:
            raise ValueError(f"{path}: duplicate player id {pid}")
        if row["club"] not in EXPECTED_CLUBS or not isinstance(row["aliases"], list):
            raise ValueError(f"{path}: invalid player alias row {pid}")
        names = [str(row["canonical"]).strip(), *[str(a).strip() for a in row["aliases"]]]
        names = list(dict.fromkeys(name for name in names if len(name) >= 3))
        for name in names:
            key = (row["club"], name.casefold())
            previous = aliases.get(key)
            if previous is not None and previous != pid:
                raise ValueError(f"{path}: ambiguous alias {name!r} for {row['club']}")
            aliases[key] = pid
        row["player_id"] = pid
        row["aliases"] = names
        ids.add(pid)
        out.append(row)
    return out


def aliases_from_bootstrap(bootstrap: dict) -> dict:
    """Build a deterministic alias registry from the official FPL bootstrap."""
    clubs = {team["id"]: team["short_name"] for team in bootstrap["teams"]}
    players = []
    for element in sorted(bootstrap["elements"], key=lambda row: row["id"]):
        canonical = " ".join(part for part in (
            element.get("first_name", "").strip(), element.get("second_name", "").strip()
        ) if part)
        variants = [element.get("web_name", ""), element.get("known_name", ""),
                    element.get("second_name", ""), canonical]
        players.append({
            "player_id": element["id"], "club": clubs[element["team"]],
            "canonical": canonical or element["web_name"],
            "aliases": list(dict.fromkeys(v.strip() for v in variants if len(v.strip()) >= 3)),
        })
    counts: dict[tuple[str, str], int] = {}
    for player in players:
        for alias in set(player["aliases"]):
            key = (player["club"], alias.casefold())
            counts[key] = counts.get(key, 0) + 1
    for player in players:
        # Shared surnames are not safe identifiers. Full canonical names remain
        # available through load_aliases even when a short alias is removed.
        player["aliases"] = [
            alias for alias in player["aliases"]
            if alias == player["canonical"] or counts[(player["club"], alias.casefold())] == 1
        ]
    return {"version": 1, "generated_from": "official FPL bootstrap", "players": players}
