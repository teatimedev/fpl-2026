"""One idempotent command from public club pages to safe model inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from v2.news_contracts import aliases_from_bootstrap, load_aliases, load_sources
from v2.news_extract import extract_claims
from v2.news_fetch import fetch_all


ROOT = Path(__file__).resolve().parent.parent
FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES = "https://fantasy.premierleague.com/api/fixtures/"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text() == rendered:
        return False
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(rendered)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


# P8.1: predicted line-ups as a LOW-CONFIDENCE generated tier. A predicted
# start sets p_start to a 50/50 blend of the model's own rate and the source's
# implied rate (blend_weight, availability.py), never overrides an explicit
# absence, and expires at the deadline. It stays REVIEW-ONLY (status
# "review", which the loader skips) until two graded deadlines show a
# positive start_brier_lift for generation_rule predicted_lineup_v1 in
# scorecard.availability_groups.claim_type — the standard WORKFLOW-NOTES.md
# sets for nuanced claims. Flip PROMOTE_PREDICTED_LINEUPS to promote.
PROMOTE_PREDICTED_LINEUPS = False
PREDICTED_START_IMPLIED = 0.85
PREDICTED_BENCH_IMPLIED = 0.15
PREDICTED_BLEND_WEIGHT = 0.5


def build_predicted_lineup_overrides(claims: list[dict], *, gw: int, generated_at: str,
                                     absent_ids: set[int]) -> list[dict]:
    """Rows for predicted_start / predicted_bench claims about this gameweek."""
    by_player: dict[int, list[dict]] = {}
    for claim in claims:
        if claim.get("claim_type") not in ("predicted_start", "predicted_bench"):
            continue
        if claim.get("gw") not in (None, gw):
            continue
        by_player.setdefault(int(claim["player_id"]), []).append(claim)
    rows = []
    for player_id, evidence in sorted(by_player.items()):
        if player_id in absent_ids:
            continue                     # an explicit absence always wins
        kinds = {claim["claim_type"] for claim in evidence}
        if len(kinds) != 1:
            continue                     # start AND bench predicted: no opinion
        kind = kinds.pop()
        evidence = sorted(evidence, key=lambda row: row["id"])
        start = kind == "predicted_start"
        rows.append({
            "player_id": player_id, "name": evidence[0]["player"],
            "from_gw": gw, "through_gw": gw,
            "p_start": PREDICTED_START_IMPLIED if start else PREDICTED_BENCH_IMPLIED,
            "p_cameo": 0.2 if start else 0.5,
            "start_minutes": 80.0, "cameo_minutes": 25.0,
            "blend_weight": PREDICTED_BLEND_WEIGHT, "confidence": "low",
            "source": " ; ".join(dict.fromkeys(row["url"] for row in evidence)),
            "note": evidence[0]["excerpt"],
            "status": "applied" if PROMOTE_PREDICTED_LINEUPS else "review",
            "evidence_ids": [row["id"] for row in evidence],
            "generation_rule": "predicted_lineup_v1", "generated_at": generated_at,
            "last_updated": generated_at,
        })
    return rows


def build_generated_overrides(claims: list[dict], *, gw: int, deadlines: dict[int, str],
                              generated_at: str) -> dict:
    rows = []
    by_player: dict[int, list[dict]] = {}
    conflicts: set[int] = set()
    for claim in claims:
        if claim.get("decision") == "applied":
            by_player.setdefault(int(claim["player_id"]), []).append(claim)
        if claim.get("claim_type") == "available":
            conflicts.add(int(claim["player_id"]))
    for player_id, evidence in sorted(by_player.items()):
        if player_id in conflicts:
            continue
        # The first safe release only auto-applies zero-minute absences. A
        # claim with a later dated return can extend this once date parsing is
        # explicitly represented in the evidence contract.
        evidence = sorted(evidence, key=lambda row: row["id"])
        if evidence and all(row.get("return_date") for row in evidence) and gw in deadlines:
            current_deadline = datetime.fromisoformat(deadlines[gw].replace("Z", "+00:00")).date()
            if max(date.fromisoformat(row["return_date"]) for row in evidence) <= current_deadline:
                continue
        through_gw = gw
        for claim in evidence:
            if claim.get("return_date"):
                returned = date.fromisoformat(claim["return_date"])
                before_return = [event_gw for event_gw, stamp in deadlines.items()
                                 if event_gw >= gw and
                                 datetime.fromisoformat(stamp.replace("Z", "+00:00")).date() < returned]
                if before_return:
                    through_gw = max(through_gw, max(before_return))
        rows.append({
            "player_id": player_id, "name": evidence[0]["player"],
            "from_gw": gw, "through_gw": through_gw, "p_start": 0.0, "p_cameo": 0.0,
            "start_minutes": 0.0, "cameo_minutes": 0.0, "confidence": "high",
            "source": " ; ".join(dict.fromkeys(row["url"] for row in evidence)),
            "note": evidence[0]["excerpt"], "status": "applied",
            "evidence_ids": [row["id"] for row in evidence],
            "generation_rule": "explicit_absence_v1", "generated_at": generated_at,
            "last_updated": generated_at,
        })
    absent = {int(row["player_id"]) for row in rows}
    rows.extend(build_predicted_lineup_overrides(claims, gw=gw, generated_at=generated_at,
                                                 absent_ids=absent))
    return {"version": 1, "updated_at": generated_at, "overrides": rows}


def resolve_claim_conflicts(claims: list[dict]) -> list[dict]:
    available = {int(claim["player_id"]) for claim in claims
                 if claim.get("claim_type") == "available"}
    dated: dict[int, set[str]] = {}
    for claim in claims:
        if claim.get("decision") == "applied" and claim.get("return_date"):
            dated.setdefault(int(claim["player_id"]), set()).add(claim["return_date"])
    conflicts = available | {player_id for player_id, dates in dated.items() if len(dates) > 1}
    resolved = []
    for raw in claims:
        claim = dict(raw)
        if int(claim["player_id"]) in conflicts and claim.get("decision") == "applied":
            claim["decision"] = "candidate"
            claim["confidence"] = "review"
            claim["reason"] = "conflicting_first_party_claims"
        resolved.append(claim)
    return resolved


def _semantic_generated(value: dict) -> list[dict]:
    """The rows that reach the model: applied ones. Review-only rows (P8.1's
    predicted line-ups until promoted) are archived but never a reason to
    rebuild or notify."""
    ignored = {"generated_at", "last_updated"}
    return [{k: v for k, v in row.items() if k not in ignored}
            for row in value.get("overrides", [])
            if row.get("status", "applied") == "applied"]


def materiality(old: dict, new: dict, *, owned_ids: set[int], captain: int | None,
                vice: int | None, hours_to_deadline: float) -> dict:
    changed = _semantic_generated(old) != _semantic_generated(new)
    old_owned = [r for r in _semantic_generated(old) if int(r["player_id"]) in owned_ids]
    new_owned = [r for r in _semantic_generated(new) if int(r["player_id"]) in owned_ids]
    affected = sorted({int(r["player_id"]) for r in old_owned + new_owned}) if old_owned != new_owned else []
    urgent = hours_to_deadline <= 3 and bool({captain, vice} & set(affected))
    return {"state_changed": changed, "rebuild_required": changed,
            "notify_required": bool(affected), "urgent": urgent, "affected_owned": affected}


def degraded_materiality(current_failed: set[str], previous_failed: set[str],
                         captain_clubs: set[str], hours_to_deadline: float) -> dict:
    newly_relevant = (current_failed ^ previous_failed) & captain_clubs
    urgent = hours_to_deadline <= 3 and bool(newly_relevant)
    return {"notify_required": urgent, "urgent": urgent,
            "affected_clubs": sorted(newly_relevant)}


def persist_scan(root: Path, payload: dict) -> dict:
    paths = {
        "data/news/evidence.json": payload["evidence"],
        "data/news/source_health.json": payload["health"],
        "data/news/latest_run.json": payload["run"],
        "v2/availability.generated.json": payload["generated"],
        f"data/news/history/gw{payload['evidence']['gw']}.json": payload["evidence"],
    }
    written = [relative for relative, value in paths.items()
               if _atomic_json(root / relative, value)]
    return {"state_changed": bool(written), "written": written}


def _official_json(url: str, path: Path):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read()), True
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        if path.exists():
            try:
                return json.loads(path.read_text()), False
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass
        return None, False


def _valid_official_payloads(bootstrap, fixtures, now: datetime) -> bool:
    try:
        if (not isinstance(bootstrap, dict) or not isinstance(fixtures, list)
                or any(not isinstance(bootstrap.get(key), list) or not bootstrap[key]
                       for key in ("events", "teams", "elements"))):
            return False
        if any(not {"id", "deadline_time"} <= set(event) for event in bootstrap["events"]):
            return False
        if any(not {"id", "short_name", "name"} <= set(team) for team in bootstrap["teams"]):
            return False
        if any("id" not in element for element in bootstrap["elements"]):
            return False
        gw, _, _ = _context(bootstrap, now)
        team_ids = {team["id"] for team in bootstrap["teams"]}
        upcoming = [row for row in fixtures if isinstance(row, dict) and row.get("event") == gw]
        if not upcoming:
            return False
        required = {"event", "team_h", "team_a", "kickoff_time"}
        return all(required <= set(row) and row["team_h"] in team_ids and row["team_a"] in team_ids
                   and datetime.fromisoformat(row["kickoff_time"].replace("Z", "+00:00"))
                   for row in upcoming)
    except (KeyError, StopIteration, TypeError, ValueError):
        return False


def _fixture_context(bootstrap: dict, fixtures: list[dict], gw: int) -> dict[str, dict[str, set[str]]]:
    teams = {team["id"]: team for team in bootstrap["teams"]}
    contexts = {team["short_name"]: {"opponents": set(), "match_markers": {"premier league"}}
                for team in bootstrap["teams"]}
    for fixture in fixtures:
        if fixture.get("event") != gw:
            continue
        home, away = teams[fixture["team_h"]], teams[fixture["team_a"]]
        for club, opponent in ((home, away), (away, home)):
            names = {opponent["short_name"], opponent["name"]}
            context = contexts[club["short_name"]]
            context["opponents"].update(name.casefold() for name in names)
            kickoff = datetime.fromisoformat(fixture["kickoff_time"].replace("Z", "+00:00"))
            day = kickoff.day
            suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            context["match_markers"].update({
                kickoff.strftime("%A").casefold(), kickoff.strftime("%d %B").lstrip("0").casefold(),
                kickoff.strftime("%B %d").replace(" 0", " ").casefold(),
                f"{day}{suffix} {kickoff.strftime('%B')}".casefold(), kickoff.date().isoformat(),
            })
    return contexts


def _context(bootstrap: dict, now: datetime) -> tuple[int, dict[int, str], float]:
    events = bootstrap["events"]
    upcoming = next((event for event in events if event.get("is_next")), None)
    if not upcoming:
        upcoming = next(event for event in events
                        if datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00")) > now)
    deadlines = {int(event["id"]): event["deadline_time"] for event in events}
    deadline = datetime.fromisoformat(upcoming["deadline_time"].replace("Z", "+00:00"))
    return int(upcoming["id"]), deadlines, (deadline - now).total_seconds() / 3600


def _owned(root: Path) -> tuple[set[int], int | None, int | None]:
    weekly = _read(root / "data/weekly.json", {})
    squad = weekly.get("squad") or {}
    model = weekly.get("model") or {}
    return set(map(int, squad.get("ids") or [])), model.get("captain"), model.get("vice")


def _without_timestamps(value):
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items()
                if key not in {"checked_at", "observed_at", "updated_at", "generated_at", "last_updated",
                               "content_hash", "etag", "last_modified", "documents", "article_errors"}}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _stabilize(root: Path, relative: str, value: dict) -> dict:
    prior = _read(root / relative, None)
    if prior is not None and _without_timestamps(prior) == _without_timestamps(value):
        return prior
    return value


def _official_outage(root: Path, now: datetime, stamp: str) -> dict:
    prior_run = _read(root / "data/news/latest_run.json", {})
    evidence = _read(root / "data/news/evidence.json", {})
    prior_health = _read(root / "data/news/source_health.json", {})
    generated = _read(root / "v2/availability.generated.json", {"version": 1, "overrides": []})
    gw = int(prior_run.get("gw") or evidence.get("gw") or 0)
    deadline = prior_run.get("deadline") or evidence.get("deadline")
    hours = ((datetime.fromisoformat(deadline.replace("Z", "+00:00")) - now).total_seconds() / 3600
             if deadline else 0.0)
    transitioned = prior_run.get("official_fpl_ok", True) is not False
    notify = hours <= 3 and transitioned
    key = hashlib.sha256(f"official-fpl-outage|{gw}|{deadline}".encode()).hexdigest()[:20]
    health = {**prior_health, "version": 1, "gw": gw, "status": "red",
              "official_fpl_ok": False, "checked_at": stamp}
    health.setdefault("coverage", 0.0); health.setdefault("healthy", 0)
    health.setdefault("enabled", 20); health.setdefault("sources", [])
    run = {**prior_run, "version": 1, "gw": gw, "deadline": deadline,
           "checked_at": stamp, "status": "red", "official_fpl_ok": False,
           "rebuild_required": False, "notify_required": notify, "urgent": notify,
           "affected_owned": [], "affected_clubs": [], "notification_key": key}
    run.setdefault("coverage", health["coverage"]); run.setdefault("claims", len(evidence.get("claims", [])))
    payload = {"evidence": evidence or {"version": 1, "gw": gw, "deadline": deadline, "claims": [],
                                        "policy": "explicit absences auto-apply; nuanced claims are review candidates"},
               "health": health, "generated": generated, "run": run}
    payload["health"] = _stabilize(root, "data/news/source_health.json", payload["health"])
    payload["run"] = _stabilize(root, "data/news/latest_run.json", payload["run"])
    persisted = persist_scan(root, payload)
    return {**run, "state_changed": persisted["state_changed"],
            "files_changed": persisted["written"]}


def run(root: Path = ROOT, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    bootstrap, bootstrap_fresh = _official_json(
        FPL_BOOTSTRAP, root / "v2/cache/bootstrap.json")
    fixtures, fixtures_fresh = _official_json(
        FPL_FIXTURES, root / "v2/cache/fixtures.json")
    if not _valid_official_payloads(bootstrap, fixtures, now):
        return _official_outage(root, now, stamp)
    official_fpl_ok = bootstrap_fresh and fixtures_fresh
    gw, deadlines, hours = _context(bootstrap, now)
    aliases_payload = aliases_from_bootstrap(bootstrap)
    _atomic_json(root / "v2/player_aliases.json", aliases_payload)
    aliases = load_aliases(root / "v2/player_aliases.json")
    sources = load_sources(root / "v2/news_sources.json", require_all_clubs=True)
    previous_health = _read(root / "data/news/source_health.json", {})
    prior_health = {row["id"]: row for row in previous_health.get("sources", [])}
    documents, source_rows, unchanged_sources, unchanged_urls = fetch_all(
        sources, prior_health=prior_health)
    fixture_context = _fixture_context(bootstrap, fixtures, gw)
    claims = []
    for document in documents:
        context = fixture_context.get(document["club"], {})
        claims.extend(extract_claims(document, aliases, gw=gw, now=now,
                                     fixture_terms=context.get("opponents", set()),
                                     fixture_markers=context.get("match_markers", set())))
    previous_evidence = _read(root / "data/news/evidence.json", {})
    claims.extend(claim for claim in previous_evidence.get("claims", [])
                  if (claim.get("source_id") in unchanged_sources or claim.get("url") in unchanged_urls)
                  and claim.get("gw") == gw)
    claims = [dict(claim, observed_at=stamp) for claim in
              {claim["id"]: claim for claim in claims}.values()]
    claims = resolve_claim_conflicts(sorted(claims, key=lambda row: row["id"]))
    enabled = sum(1 for source in sources if source["enabled"])
    healthy = sum(1 for row in source_rows if row["status"] == "ok")
    coverage = healthy / enabled if enabled else 0.0
    status = ("red" if not official_fpl_ok else
              "green" if coverage == 1.0 else "red" if coverage < 0.5 else "amber")
    old_generated = _read(root / "v2/availability.generated.json", {"overrides": []})
    generated = (build_generated_overrides(claims, gw=gw, deadlines=deadlines, generated_at=stamp)
                 if official_fpl_ok else old_generated)
    owned, captain, vice = _owned(root)
    impact = materiality(old_generated, generated, owned_ids=owned, captain=captain,
                         vice=vice, hours_to_deadline=hours)
    player_club = {int(player["player_id"]): player["club"] for player in aliases}
    captain_clubs = {player_club[player_id] for player_id in (captain, vice)
                     if player_id in player_club}
    current_failed = {row["club"] for row in source_rows if row["status"] != "ok"}
    previous_failed = {row["club"] for row in previous_health.get("sources", [])
                       if row.get("status") != "ok"}
    degraded = degraded_materiality(current_failed, previous_failed, captain_clubs, hours)
    if degraded["notify_required"]:
        impact["notify_required"] = True
        impact["urgent"] = True
    official = {str(element["id"]): {
        "status": element.get("status"), "chance": element.get("chance_of_playing_next_round"),
        "news": element.get("news", "")
    } for element in bootstrap["elements"]}
    previous_run = _read(root / "data/news/latest_run.json", {})
    previous_official = previous_run.get("official_fpl") or {}
    official_changed = sorted(int(pid) for pid in set(official) | set(previous_official)
                              if official.get(pid) != previous_official.get(pid))
    if previous_official and official_fpl_ok:
        if official_changed:
            impact["rebuild_required"] = True
        official_affected = sorted(set(official_changed) & owned)
        if official_affected:
            impact["notify_required"] = True
            impact["affected_owned"] = sorted(set(impact["affected_owned"]) | set(official_affected))
            impact["urgent"] = impact["urgent"] or (
                hours <= 3 and bool({captain, vice} & set(official_affected))
            )
    if not official_fpl_ok:
        impact["rebuild_required"] = False
        transitioned = previous_run.get("official_fpl_ok", True) is not False
        if hours <= 3 and transitioned:
            impact["notify_required"] = True
            impact["urgent"] = True
    notification_basis = {
        "gw": gw, "affected_owned": impact["affected_owned"],
        "affected_clubs": degraded["affected_clubs"],
        "official_fpl_ok": official_fpl_ok,
        "generated": _semantic_generated(generated),
    }
    notification_key = hashlib.sha256(
        json.dumps(notification_basis, sort_keys=True).encode()).hexdigest()[:20]
    payload = {
        "evidence": {"version": 1, "gw": gw, "deadline": deadlines[gw],
                     "claims": claims, "policy": "explicit absences auto-apply; nuanced claims are review candidates"},
        "health": {"version": 1, "gw": gw, "status": status, "coverage": round(coverage, 3),
                   "official_fpl_ok": official_fpl_ok,
                   "healthy": healthy, "enabled": enabled, "checked_at": stamp, "sources": source_rows},
        "generated": generated,
        "run": {"version": 1, "gw": gw, "deadline": deadlines[gw], "checked_at": stamp,
                "status": status, "coverage": round(coverage, 3), "claims": len(claims),
                "official_fpl": official, "official_fpl_ok": official_fpl_ok,
                "affected_clubs": degraded["affected_clubs"],
                "notification_key": notification_key, **impact},
    }
    payload["evidence"] = _stabilize(root, "data/news/evidence.json", payload["evidence"])
    payload["health"] = _stabilize(root, "data/news/source_health.json", payload["health"])
    payload["generated"] = _stabilize(root, "v2/availability.generated.json", payload["generated"])
    payload["run"] = _stabilize(root, "data/news/latest_run.json", payload["run"])
    persisted = persist_scan(root, payload)
    result = {**payload["run"], **impact, "state_changed": persisted["state_changed"],
              "files_changed": persisted["written"]}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    result = run()
    if args.summary:
        _atomic_json(args.summary, result)
    if result.get("notify_required") and not result.get("rebuild_required"):
        if not result.get("official_fpl_ok", True):
            message = ("Official FPL data is unavailable, so the model rebuild was stopped. "
                       "Check the stale-data warning before the deadline.\n"
                       "https://fpl-2026.vercel.app\n")
        else:
            clubs = ", ".join(result.get("affected_clubs") or [])
            message = (f"Team-news source coverage changed for {clubs or 'your captain/vice clubs'} "
                       f"inside three hours of the deadline. Check the source-health panel now.\n"
                       "https://fpl-2026.vercel.app\n")
        (ROOT / "v2/news_push.txt").write_text(message)
    for key in ("state_changed", "rebuild_required", "notify_required", "urgent"):
        print(f"{key}={str(bool(result[key])).lower()}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            for key in ("state_changed", "rebuild_required", "notify_required", "urgent"):
                handle.write(f"{key}={str(bool(result[key])).lower()}\n")
            handle.write(f"status={result['status']}\n")
            handle.write(f"coverage={result['coverage']}\n")
            handle.write(f"official_fpl_ok={str(bool(result['official_fpl_ok'])).lower()}\n")
            handle.write(f"notification_key={result['notification_key']}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
