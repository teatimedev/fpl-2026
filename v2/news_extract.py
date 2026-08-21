"""Conservative deterministic extraction of availability claims.

Only an unambiguous explicit absence is auto-applied. Everything else is kept
as a review candidate until the probability model has two scored deadlines.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


SENTENCE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
CLAIM_PATTERNS = (
    ("explicit_out", re.compile(
        r"\b(?:will miss|is ruled out|has been ruled out|will not be available|"
        r"won't be available|is unavailable|not in contention|not fit to face|"
        r"out of (?:the )?(?:match|game|squad))\b",
        re.I)),
    ("suspended_until", re.compile(r"\b(?:suspended|banned)\s+until\b", re.I)),
    ("return_date", re.compile(r"\b(?:expected|hoping|due)\s+(?:to be )?back\b", re.I)),
    ("late_test", re.compile(r"\b(?:late fitness test|will be assessed|needs? to be assessed|a doubt)\b", re.I)),
    ("available", re.compile(r"\b(?:is available|are available|back in training|fully fit|fit and available)\b", re.I)),
    ("predicted_bench", re.compile(r"\b(?:expected|predicted|likely)\s+(?:to be )?on the bench\b", re.I)),
    ("predicted_start", re.compile(r"\b(?:expected|predicted|likely)\s+to start\b", re.I)),
)
NEGATED_OUT = re.compile(r"\b(?:not|isn't|is not|hasn't|has not)\s+(?:been\s+)?ruled out\b", re.I)
RETURN_DATE = re.compile(
    r"\b(?:expected|due)\s+(?:to be )?back(?:\s+on)?\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", re.I)
SUSPENSION_DATE = re.compile(
    r"\b(?:suspended|banned)\s+until\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b", re.I)


def _excerpt(text: str, limit: int = 280) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"


def _mentions(sentence: str, players: list[dict], club: str) -> list[dict]:
    matches = []
    folded = sentence.casefold()
    for player in players:
        if player["club"] != club:
            continue
        aliases = sorted(player["aliases"], key=len, reverse=True)
        if any(re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", folded)
               for alias in aliases):
            matches.append(player)
    return matches


def _recent(value: str | None, now: datetime) -> bool:
    if not value:
        return False
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = (now - published.astimezone(timezone.utc)).total_seconds()
        return -86400 <= age <= 7 * 86400
    except ValueError:
        return False


def _return_date(sentence: str, now: datetime) -> str | None:
    match = RETURN_DATE.search(sentence) or SUSPENSION_DATE.search(sentence)
    if not match:
        return None
    try:
        parsed = datetime.strptime(f"{match.group(1)} {match.group(2)[:3]} 2000", "%d %b %Y")
    except ValueError:
        return None
    candidate = parsed.replace(year=now.year).date()
    # A season crosses New Year. Only wrap a month/day into next year when it
    # is clearly on the other side of that boundary, not merely yesterday.
    if (now.date() - candidate).days > 180:
        candidate = candidate.replace(year=now.year + 1)
    return candidate.isoformat()


def extract_claims(document: dict, players: list[dict], *, gw: int,
                   now: datetime, fixture_terms: set[str] | None = None,
                   fixture_markers: set[str] | None = None) -> list[dict]:
    fixture_terms = {term.casefold() for term in (fixture_terms or set()) if len(term) >= 3}
    markers = ({term.casefold() for term in fixture_markers if len(term) >= 3}
               if fixture_markers is not None else None)
    claims = []
    for sentence in SENTENCE.split(document.get("text") or ""):
        mentioned = _mentions(sentence, players, document["club"])
        if not mentioned:
            continue
        matched = None
        for claim_type, pattern in CLAIM_PATTERNS:
            if pattern.search(sentence):
                matched = claim_type
                break
        if not matched:
            continue
        negated = matched == "explicit_out" and NEGATED_OUT.search(sentence)
        ambiguous = len(mentioned) != 1
        recent = _recent(document.get("published_at"), now)
        return_date = _return_date(sentence, now)
        fixture_text = f"{document.get('title', '')} {sentence}".casefold()
        fixture_matched = any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", fixture_text)
                              for term in fixture_terms)
        fixture_specific = (markers is None or any(
            re.search(rf"(?<!\w){re.escape(term)}(?!\w)", fixture_text) for term in markers
        ))
        safe = (matched in {"explicit_out", "suspended_until", "return_date"} and not negated
                and not ambiguous and recent)
        if matched == "explicit_out" and not (fixture_matched and fixture_specific):
            safe = False
        if matched in {"suspended_until", "return_date"} and not return_date:
            safe = False
        reason = "explicit_absence" if safe else (
            "multiple_players_in_sentence" if ambiguous else
            "negated_absence" if negated else
            "missing_or_stale_publication_time" if not recent else
            "fixture_not_matched" if matched == "explicit_out" and not (fixture_matched and fixture_specific) else
            "observation_only"
        )
        for player in mentioned:
            excerpt = _excerpt(sentence)
            stable = "|".join((document["source_id"], str(player["player_id"]), matched,
                               excerpt.casefold(), str(gw)))
            claim = {
                "id": "ev-" + hashlib.sha256(stable.encode()).hexdigest()[:16],
                "player_id": player["player_id"], "player": player["canonical"],
                "club": player["club"], "claim_type": matched,
                "decision": "applied" if safe else "candidate", "reason": reason,
                "gw": gw, "confidence": "high" if safe else "review",
                "source_id": document["source_id"], "publisher": document["publisher"],
                "url": document["url"], "title": document.get("title", ""),
                "excerpt": excerpt, "published_at": document.get("published_at"),
            }
            if return_date:
                claim["return_date"] = return_date
            claims.append(claim)
    unique = {claim["id"]: claim for claim in claims}
    return [unique[key] for key in sorted(unique)]
