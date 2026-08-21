import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from v2.news_pipeline import (
    build_generated_overrides, degraded_materiality, materiality, persist_scan,
    resolve_claim_conflicts, _official_json, _official_outage, _valid_official_payloads,
)


class NewsPipelineTests(unittest.TestCase):
    def claim(self, **updates):
        row = {
            "id": "ev-1", "player_id": 12, "player": "Bukayo Saka", "club": "ARS",
            "claim_type": "explicit_out", "decision": "applied", "gw": 2,
            "source_id": "ars-team-news", "publisher": "Arsenal",
            "url": "https://www.arsenal.com/news/team-news", "excerpt": "Saka will miss the match.",
            "published_at": "2026-08-28T12:00:00Z", "confidence": "high",
        }
        row.update(updates)
        return row

    def test_explicit_out_builds_expiring_override(self):
        out = build_generated_overrides([self.claim()], gw=2, deadlines={2: "2026-08-29T11:00:00Z"}, generated_at="2026-08-28T13:00:00Z")
        self.assertEqual(out["overrides"][0]["through_gw"], 2)
        self.assertEqual(out["overrides"][0]["p_start"], 0.0)
        self.assertEqual(out["overrides"][0]["evidence_ids"], ["ev-1"])

    def test_candidate_never_builds_an_override(self):
        out = build_generated_overrides([self.claim(decision="candidate")], gw=2, deadlines={}, generated_at="x")
        self.assertEqual(out["overrides"], [])

    def test_return_date_extends_only_through_deadlines_before_return(self):
        claim = self.claim(claim_type="return_date", return_date="2026-09-06")
        deadlines = {2: "2026-08-29T11:00:00Z", 3: "2026-09-04T17:30:00Z", 4: "2026-09-12T12:30:00Z"}
        out = build_generated_overrides([claim], gw=2, deadlines=deadlines, generated_at="x")
        self.assertEqual(out["overrides"][0]["through_gw"], 3)

    def test_owned_player_override_is_material(self):
        old = {"overrides": []}
        new = build_generated_overrides([self.claim()], gw=2, deadlines={}, generated_at="x")
        result = materiality(old, new, owned_ids={12}, captain=12, vice=13, hours_to_deadline=2.5)
        self.assertTrue(result["rebuild_required"])
        self.assertTrue(result["urgent"])

    def test_unowned_change_is_visible_without_forcing_model_rebuild(self):
        new = build_generated_overrides([self.claim()], gw=2, deadlines={}, generated_at="x")
        result = materiality({"overrides": []}, new, owned_ids={99}, captain=99, vice=98, hours_to_deadline=10)
        self.assertTrue(result["rebuild_required"])
        self.assertTrue(result["state_changed"])

    def test_available_claim_conflicts_fail_safe(self):
        claims = [self.claim(), self.claim(id="ev-2", claim_type="available",
                                          decision="candidate", excerpt="Saka is available.")]
        out = build_generated_overrides(claims, gw=2, deadlines={2: "2026-08-29T11:00:00Z"}, generated_at="x")
        self.assertEqual(out["overrides"], [])
        resolved = resolve_claim_conflicts(claims)
        self.assertEqual({claim["decision"] for claim in resolved}, {"candidate"})
        self.assertIn("conflicting_first_party_claims", {claim.get("reason") for claim in resolved})

    def test_conflicting_dated_claims_fail_safe(self):
        claims = [self.claim(id="ev-1", claim_type="return_date", return_date="2026-09-01"),
                  self.claim(id="ev-2", claim_type="suspended_until", return_date="2026-09-08")]
        resolved = resolve_claim_conflicts(claims)
        self.assertEqual({claim["decision"] for claim in resolved}, {"candidate"})
        out = build_generated_overrides(resolved, gw=2, deadlines={}, generated_at="x")
        self.assertEqual(out["overrides"], [])

    def test_unchanged_scan_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "evidence": {"version": 1, "gw": 2, "claims": [self.claim()]},
                "health": {"version": 1, "gw": 2, "status": "green", "coverage": 1.0, "sources": []},
                "generated": build_generated_overrides([self.claim()], gw=2, deadlines={}, generated_at="fixed"),
                "run": {"version": 1, "gw": 2, "status": "green", "checked_at": "fixed"},
            }
            first = persist_scan(root, payload)
            mtimes = {p: (root / p).stat().st_mtime_ns for p in first["written"]}
            second = persist_scan(root, payload)
            self.assertFalse(second["state_changed"])
            self.assertEqual(second["written"], [])
            self.assertEqual(mtimes, {p: (root / p).stat().st_mtime_ns for p in mtimes})

    def test_new_captain_club_source_failure_is_urgent_inside_t3(self):
        result = degraded_materiality({"MCI"}, set(), {"MCI", "MUN"}, 2.5)
        self.assertTrue(result["notify_required"])
        self.assertTrue(result["urgent"])

    def test_repeated_source_failure_does_not_spam(self):
        result = degraded_materiality({"MCI"}, {"MCI"}, {"MCI"}, 2.0)
        self.assertFalse(result["notify_required"])

    def test_official_fpl_cache_fallback_is_marked_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "bootstrap.json"
            cache.write_text('{"events": []}')
            with patch("v2.news_pipeline.urllib.request.urlopen", side_effect=OSError("down")):
                payload, fresh = _official_json("https://example.test/api", cache)
        self.assertEqual(payload, {"events": []})
        self.assertFalse(fresh)

    def test_official_fpl_outage_without_cache_is_controlled(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch("v2.news_pipeline.urllib.request.urlopen", side_effect=OSError("down")):
                payload, fresh = _official_json("https://example.test/api", missing)
        self.assertIsNone(payload)
        self.assertFalse(fresh)

    def test_malformed_official_json_is_controlled(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{truncated'
        response.__enter__.return_value.__exit__ = unittest.mock.MagicMock(return_value=False)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("v2.news_pipeline.urllib.request.urlopen", return_value=response):
                payload, fresh = _official_json("https://example.test/api", Path(tmp) / "missing")
        self.assertIsNone(payload)
        self.assertFalse(fresh)
        self.assertFalse(_valid_official_payloads({"events": []}, []))

    def test_outage_near_deadline_builds_urgent_alert_without_fpl_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data/news").mkdir(parents=True)
            (root / "data/news/latest_run.json").write_text(json.dumps({
                "gw": 2, "deadline": "2026-08-29T11:00:00Z", "official_fpl_ok": True,
                "coverage": 1.0, "official_fpl": {}
            }))
            result = _official_outage(root, datetime(2026, 8, 29, 9, tzinfo=timezone.utc),
                                      "2026-08-29T09:00:00Z")
        self.assertTrue(result["notify_required"])
        self.assertTrue(result["urgent"])
        self.assertFalse(result["rebuild_required"])


if __name__ == "__main__":
    unittest.main()
