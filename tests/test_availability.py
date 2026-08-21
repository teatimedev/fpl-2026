import tempfile
import unittest
from pathlib import Path

from v2.availability import (
    availability_forecast,
    deadline_start_probability,
    load_overrides,
    status_for_gameweek,
)


class AvailabilityForecastTests(unittest.TestCase):
    def test_manual_override_wins_over_generated_for_the_same_gameweek(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "availability.json"
            generated = Path(tmp) / "availability.generated.json"
            manual.write_text(
                """{
                  "updated_at": "2026-08-21T14:30:00Z",
                  "overrides": [{
                    "player_id": 12,
                    "from_gw": 2,
                    "through_gw": 2,
                    "p_start": 0.60,
                    "p_cameo": 0.80,
                    "start_minutes": 70,
                    "cameo_minutes": 30,
                    "confidence": "medium",
                    "source": "manual review"
                  }]
                }"""
            )
            generated.write_text(
                """{
                  "updated_at": "2026-08-21T14:25:00Z",
                  "overrides": [{
                    "player_id": 12,
                    "from_gw": 2,
                    "through_gw": 2,
                    "p_start": 0.0,
                    "p_cameo": 0.0,
                    "start_minutes": 0,
                    "cameo_minutes": 0,
                    "confidence": "high",
                    "source": "official club news",
                    "status": "applied",
                    "evidence_ids": ["club-12-out"],
                    "generation_rule": "explicit_out_v1",
                    "generated_at": "2026-08-21T14:25:00Z"
                  }]
                }"""
            )

            overrides = load_overrides(manual, generated)

        forecast = availability_forecast(
            player_id=12,
            gw=2,
            base_start=0.75,
            base_start_minutes=86,
            status="a",
            overrides=overrides,
        )
        self.assertEqual(forecast.p_start, 0.60)
        self.assertEqual(forecast.source, "manual review")

    def test_applied_generated_override_requires_audit_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "availability.json"
            generated = Path(tmp) / "availability.generated.json"
            manual.write_text('{"overrides": []}')
            generated.write_text(
                """{
                  "updated_at": "2026-08-21T14:25:00Z",
                  "overrides": [{
                    "player_id": 12,
                    "from_gw": 2,
                    "through_gw": 2,
                    "p_start": 0.0,
                    "p_cameo": 0.0,
                    "start_minutes": 0,
                    "cameo_minutes": 0,
                    "confidence": "high",
                    "source": "official club news",
                    "status": "applied"
                  }]
                }"""
            )

            with self.assertRaisesRegex(ValueError, "evidence_ids"):
                load_overrides(manual, generated)

    def test_conflicting_applied_generated_overrides_fail_at_load_time(self):
        row = """{
          "player_id": 12,
          "from_gw": 2,
          "through_gw": 2,
          "p_start": 0.0,
          "p_cameo": 0.0,
          "start_minutes": 0,
          "cameo_minutes": 0,
          "confidence": "high",
          "source": "official club news",
          "status": "applied",
          "evidence_ids": ["%s"],
          "generation_rule": "explicit_out_v1",
          "generated_at": "2026-08-21T14:25:00Z"
        }"""
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "availability.json"
            generated = Path(tmp) / "availability.generated.json"
            manual.write_text('{"overrides": []}')
            generated.write_text(
                '{"updated_at": "2026-08-21T14:25:00Z", "overrides": ['
                + (row % "club-report") + "," + (row % "league-report")
                + "]}"
            )

            with self.assertRaisesRegex(ValueError, "conflicting generated"):
                load_overrides(manual, generated)

    def test_manual_override_masks_only_its_gameweek_from_generated_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = Path(tmp) / "availability.json"
            generated = Path(tmp) / "availability.generated.json"
            manual.write_text(
                """{"updated_at": "2026-08-21T14:30:00Z", "overrides": [{
                  "player_id": 12, "from_gw": 2, "through_gw": 2,
                  "p_start": 0.6, "p_cameo": 0.8,
                  "start_minutes": 70, "cameo_minutes": 30,
                  "confidence": "medium", "source": "manual review"
                }]}"""
            )
            generated.write_text(
                """{"updated_at": "2026-08-21T14:25:00Z", "overrides": [{
                  "player_id": 12, "from_gw": 2, "through_gw": 3,
                  "p_start": 0.0, "p_cameo": 0.0,
                  "start_minutes": 0, "cameo_minutes": 0,
                  "confidence": "high", "source": "official club news",
                  "status": "applied", "evidence_ids": ["club-12-out"],
                  "generation_rule": "explicit_out_v1",
                  "generated_at": "2026-08-21T14:25:00Z"
                }]}"""
            )
            overrides = load_overrides(manual, generated)

        current = availability_forecast(
            player_id=12, gw=2, base_start=0.75, base_start_minutes=86,
            status="a", overrides=overrides,
        )
        following = availability_forecast(
            player_id=12, gw=3, base_start=0.75, base_start_minutes=86,
            status="a", overrides=overrides,
        )
        self.assertEqual(current.p_start, 0.6)
        self.assertEqual(following.p_start, 0.0)
        self.assertEqual(following.from_gw, 3)

    def test_override_requires_an_explicit_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "availability.json"
            path.write_text(
                """{
                  "updated_at": "2026-08-21T14:30:00Z",
                  "overrides": [{
                    "player_id": 12,
                    "from_gw": 1,
                    "p_start": 0.55,
                    "p_cameo": 0.80,
                    "start_minutes": 70,
                    "cameo_minutes": 30,
                    "confidence": "medium",
                    "source": "https://example.test/team-news"
                  }]
                }"""
            )

            with self.assertRaisesRegex(ValueError, "through_gw"):
                load_overrides(path)

    def test_deadline_override_applies_only_inside_its_gameweek_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "availability.json"
            path.write_text(
                """{
                  "updated_at": "2026-08-21T14:30:00Z",
                  "overrides": [{
                    "player_id": 12,
                    "from_gw": 1,
                    "through_gw": 1,
                    "p_start": 0.55,
                    "p_cameo": 0.80,
                    "start_minutes": 70,
                    "cameo_minutes": 30,
                    "confidence": "medium",
                    "source": "official team news"
                  }]
                }"""
            )
            overrides = load_overrides(path)

        current = availability_forecast(
            player_id=12,
            gw=1,
            base_start=0.75,
            base_start_minutes=86,
            status="a",
            overrides=overrides,
        )
        expired = availability_forecast(
            player_id=12,
            gw=2,
            base_start=0.75,
            base_start_minutes=86,
            status="a",
            overrides=overrides,
        )

        self.assertAlmostEqual(current.p_start, 0.55)
        self.assertAlmostEqual(current.p_play, 0.91)
        self.assertAlmostEqual(current.expected_minutes, 49.3)
        self.assertEqual(current.confidence, "medium")
        self.assertEqual(current.last_updated, "2026-08-21T14:30:00Z")
        self.assertEqual(current.through_gw, 1)
        self.assertEqual(expired.p_start, 0.75)
        self.assertAlmostEqual(expired.p_play, 0.80)
        self.assertEqual(expired.source, "model baseline")

    def test_unavailable_player_has_no_cameo_probability(self):
        forecast = availability_forecast(
            player_id=298,
            gw=1,
            base_start=0.40,
            base_start_minutes=80,
            status="u",
            overrides=[],
        )

        self.assertEqual(forecast.p_start, 0.0)
        self.assertEqual(forecast.p_cameo, 0.0)
        self.assertEqual(forecast.p_play, 0.0)
        self.assertEqual(forecast.expected_minutes, 0.0)

    def test_suspended_player_has_no_route_to_minutes(self):
        forecast = availability_forecast(
            player_id=300,
            gw=1,
            base_start=0.80,
            base_start_minutes=85,
            status="s",
            overrides=[],
        )

        self.assertEqual(forecast.p_start, 0.0)
        self.assertEqual(forecast.p_cameo, 0.0)
        self.assertEqual(forecast.p_play, 0.0)
        self.assertEqual(forecast.source, "FPL suspended status")

    def test_temporary_status_only_changes_the_next_deadline(self):
        base = 0.80
        current_status = status_for_gameweek("s", 4, 4)
        future_status = status_for_gameweek("s", 5, 4)
        current = availability_forecast(
            player_id=300, gw=4,
            base_start=deadline_start_probability(base, "s"),
            base_start_minutes=85, status=current_status, overrides=[],
        )
        future = availability_forecast(
            player_id=300, gw=5, base_start=base,
            base_start_minutes=85, status=future_status, overrides=[],
        )

        self.assertEqual(current.p_play, 0.0)
        self.assertEqual(future.p_start, 0.80)

    def test_dated_suspension_persists_until_its_return(self):
        news = "Suspended until 6 Sep"

        self.assertEqual(
            status_for_gameweek(
                "s", 3, 1, news=news,
                gw_deadline="2026-09-04T17:30:00Z",
            ),
            "s",
        )
        self.assertEqual(
            status_for_gameweek(
                "s", 4, 1, news=news,
                gw_deadline="2026-09-12T12:30:00Z",
            ),
            "a",
        )

    def test_dated_injury_stays_active_across_the_model_window(self):
        self.assertEqual(
            status_for_gameweek(
                "i", 6, 1, news="Leg injury - Expected back 28 Nov",
                gw_deadline="2026-10-10T10:00:00Z",
            ),
            "i",
        )

    def test_doubtful_chance_is_a_next_deadline_multiplier(self):
        self.assertAlmostEqual(
            deadline_start_probability(0.80, "d", chance=75), 0.60
        )

    def test_reserve_goalkeeper_has_no_default_cameo_route(self):
        forecast = availability_forecast(
            player_id=999,
            gw=1,
            base_start=0.05,
            base_start_minutes=90,
            position="GKP",
            status="a",
            overrides=[],
        )

        self.assertEqual(forecast.p_start, 0.05)
        self.assertEqual(forecast.p_cameo, 0.0)
        self.assertEqual(forecast.p_play, 0.05)


if __name__ == "__main__":
    unittest.main()
