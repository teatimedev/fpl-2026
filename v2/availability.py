"""Deadline-aware player availability forecasts.

The historical minutes model supplies a baseline.  Short-lived team news then
overrides that baseline for an explicit gameweek range, so a predicted lineup
or press-conference quote cannot silently live in the model for six weeks.

``p_cameo`` is conditional on not starting.  Keeping it separate from
``p_start`` matters: a substitute should receive substitute minutes, not a
starter's full per-90 exposure.
"""
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Iterable, Mapping


HERE = Path(__file__).resolve().parent
DEFAULT_OVERRIDES = HERE / "availability.json"
DEFAULT_GENERATED_OVERRIDES = HERE / "availability.generated.json"
DEFAULT_CAMEO_PROBABILITY = 0.20
DEFAULT_CAMEO_MINUTES = 25.0


@dataclass(frozen=True)
class AvailabilityForecast:
    p_start: float
    p_cameo: float
    start_minutes: float
    cameo_minutes: float
    p_play: float
    expected_minutes: float
    source: str
    confidence: str
    note: str = ""
    last_updated: str | None = None
    from_gw: int | None = None
    through_gw: int | None = None
    generation_rule: str | None = None


def _clamp_probability(value):
    return max(0.0, min(1.0, float(value)))


RETURN_DATE = re.compile(
    r"(?:Expected back|Suspended until)\s+(\d{1,2})\s+([A-Za-z]{3})",
    re.IGNORECASE,
)


def _dated_return(news, season_year=2026):
    match = RETURN_DATE.search(news or "")
    if not match:
        return None
    parsed = datetime.strptime(f"{match.group(1)} {match.group(2)} 2000", "%d %b %Y")
    year = season_year if parsed.month >= 7 else season_year + 1
    return parsed.replace(year=year).date()


def status_for_gameweek(status, gw, deadline_gw, *, news="", gw_deadline=None,
                        season_year=2026):
    """Expire next-round flags, while retaining explicitly dated absences."""
    if status == "u" or gw == deadline_gw:
        return status
    returned = _dated_return(news, season_year)
    if status in {"i", "s"} and returned and gw_deadline:
        deadline = datetime.fromisoformat(gw_deadline.replace("Z", "+00:00")).date()
        if deadline < returned:
            return status
    return "a"


def deadline_start_probability(base_start, status, chance=None, news=""):
    """Apply FPL's next-round flag without contaminating later gameweeks."""
    base_start = _clamp_probability(base_start)
    if status in {"u", "s"}:
        return 0.0
    if status in {"i", "d"} and chance is not None:
        return base_start * _clamp_probability(float(chance) / 100.0)
    if status == "i":
        return base_start * (0.40 if "expected back" in news.lower() else 0.06)
    return base_start


def _load_override_file(path, *, generated=False):
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    rows = data.get("overrides", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: 'overrides' must be a list")
    updated_at = data.get("updated_at")
    required = {
        "player_id", "from_gw", "through_gw", "p_start", "p_cameo",
        "start_minutes", "cameo_minutes", "source", "confidence",
    }
    generated_required = {"evidence_ids", "generation_rule", "generated_at"}
    validated = []
    for index, raw in enumerate(rows):
        if generated and raw.get("status") != "applied":
            continue
        row_required = required | (generated_required if generated else set())
        missing = sorted(row_required - raw.keys())
        if missing:
            raise ValueError(f"{path}: override {index} missing {', '.join(missing)}")
        if generated and not raw["evidence_ids"]:
            raise ValueError(f"{path}: override {index} needs evidence_ids")
        row = dict(raw, last_updated=raw.get("last_updated", updated_at))
        if not row["last_updated"]:
            raise ValueError(f"{path}: override {index} needs last_updated or updated_at")
        lo, hi = int(row["from_gw"]), int(row["through_gw"])
        if not (1 <= lo <= hi <= 38):
            raise ValueError(f"{path}: override {index} has invalid gameweek range")
        for field in ("p_start", "p_cameo"):
            value = float(row[field])
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{path}: override {index} has invalid {field}")
        # P8.1: a predicted line-up is an opinion, so its row may carry
        # blend_weight w and mean "p_start = w * row + (1 - w) * the model's
        # own rate" rather than replacing the model outright.
        if row.get("blend_weight") is not None:
            weight = float(row["blend_weight"])
            if not 0.0 <= weight <= 1.0:
                raise ValueError(f"{path}: override {index} has invalid blend_weight")
        for field, limit in (("start_minutes", 95), ("cameo_minutes", 59)):
            value = float(row[field])
            if not 0.0 <= value <= limit:
                raise ValueError(f"{path}: override {index} has invalid {field}")
        validated.append(row)
    return validated


def _overlaps(left, right):
    return (
        int(left["player_id"]) == int(right["player_id"])
        and int(left["from_gw"]) <= int(right["through_gw"])
        and int(right["from_gw"]) <= int(left["through_gw"])
    )


def load_overrides(path=DEFAULT_OVERRIDES,
                   generated_path=DEFAULT_GENERATED_OVERRIDES):
    """Load manual and applied generated inputs, with manual precedence."""
    manual = _load_override_file(path)
    generated = _load_override_file(generated_path, generated=True)
    for index, row in enumerate(generated):
        if any(_overlaps(row, previous) for previous in generated[:index]):
            raise ValueError(
                "conflicting generated overrides for "
                f"player {row['player_id']}, GW{row['from_gw']}-{row['through_gw']}"
            )
    unmasked = []
    for row in generated:
        for gw in range(int(row["from_gw"]), int(row["through_gw"]) + 1):
            point = dict(row, from_gw=gw, through_gw=gw)
            if not any(_overlaps(point, manual_row) for manual_row in manual):
                unmasked.append(point)
    return manual + unmasked


def _active_override(player_id, gw, overrides: Iterable[Mapping]):
    matches = []
    for row in overrides:
        if int(row.get("player_id", -1)) != int(player_id):
            continue
        lo = int(row.get("from_gw", gw))
        hi = int(row.get("through_gw", lo))
        if lo <= gw <= hi:
            matches.append(row)
    if len(matches) > 1:
        raise ValueError(f"multiple availability overrides for player {player_id}, GW{gw}")
    return matches[0] if matches else None


def availability_forecast(*, player_id, gw, base_start, base_start_minutes,
                          position=None, status="a", overrides=None):
    """Return the probability/minutes mixture for one player and gameweek."""
    base_start = _clamp_probability(base_start)
    start_minutes = max(0.0, min(95.0, float(base_start_minutes)))
    row = _active_override(player_id, gw, overrides or [])

    if status in {"u", "s"}:
        p_start = p_cameo = start_minutes = cameo_minutes = 0.0
        label = "suspended" if status == "s" else "unavailable"
        source, confidence, note = f"FPL {label} status", "high", ""
    else:
        default_cameo = DEFAULT_CAMEO_PROBABILITY
        if position == "GKP":
            default_cameo = 0.0
        elif status == "i":
            default_cameo = min(DEFAULT_CAMEO_PROBABILITY, base_start)
        p_start = _clamp_probability(row.get("p_start", base_start) if row else base_start)
        if row and row.get("blend_weight") is not None:
            weight = _clamp_probability(row["blend_weight"])
            p_start = _clamp_probability(weight * p_start + (1.0 - weight) * base_start)
        p_cameo = _clamp_probability(
            row.get("p_cameo", default_cameo) if row else default_cameo
        )
        start_minutes = max(0.0, min(
            95.0, float(row.get("start_minutes", start_minutes) if row else start_minutes)
        ))
        cameo_minutes = max(0.0, min(
            59.0, float(row.get("cameo_minutes", DEFAULT_CAMEO_MINUTES)
                        if row else DEFAULT_CAMEO_MINUTES)
        ))
        source = row.get("source", "model baseline") if row else "model baseline"
        confidence = row.get("confidence", "model") if row else "model"
        note = row.get("note", "") if row else ""

    p_play = p_start + (1.0 - p_start) * p_cameo
    expected_minutes = (
        p_start * start_minutes
        + (1.0 - p_start) * p_cameo * cameo_minutes
    )
    return AvailabilityForecast(
        p_start=round(p_start, 6),
        p_cameo=round(p_cameo, 6),
        start_minutes=round(start_minutes, 3),
        cameo_minutes=round(cameo_minutes, 3),
        p_play=round(p_play, 6),
        expected_minutes=round(expected_minutes, 3),
        source=source,
        confidence=confidence,
        note=note,
        last_updated=row.get("last_updated") if row else None,
        from_gw=int(row.get("from_gw")) if row and row.get("from_gw") is not None else None,
        through_gw=(int(row.get("through_gw"))
                    if row and row.get("through_gw") is not None else None),
        generation_rule=(row.get("generation_rule") if row else None),
    )
