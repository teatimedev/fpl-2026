import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from v2.news_pipeline import run


class NewsPriceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime(2026, 9, 18, 6, tzinfo=timezone.utc)
        self.bootstrap = {
            'events': [{'id': 5, 'deadline_time': '2026-09-18T17:30:00Z'}],
            'teams': [
                {'id': 1, 'short_name': 'MCI', 'name': 'Manchester City'},
                {'id': 2, 'short_name': 'BRE', 'name': 'Brentford'},
            ],
            'elements': [
                {'id': 411, 'team': 1, 'web_name': 'Haaland', 'now_cost': 156,
                 'status': 'a', 'news': '', 'chance_of_playing_next_round': None},
                {'id': 106, 'team': 2, 'web_name': 'Thiago', 'now_cost': 78,
                 'status': 'a', 'news': '', 'chance_of_playing_next_round': None},
            ],
        }
        self.fixtures = [{'event': 5, 'team_h': 1, 'team_a': 2,
                          'kickoff_time': '2026-09-19T14:00:00Z'}]
        self.forecast = {'players': [{'id': 411, 'price': 15.5}, {'id': 106, 'price': 7.9}]}
        self.write('app/public/data/fpl.json', self.forecast)
        self.write('data/weekly.json', {'squad': {'ids': [411]}, 'model': {'captain': 411}})
        self.write('data/news/latest_run.json', {
            'official_fpl_ok': True,
            'official_fpl': {str(player['id']): {'status': 'a', 'news': '', 'chance': None}
                             for player in self.bootstrap['elements']},
        })

    def write(self, relative, payload):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def scan(self, fresh=True):
        with patch('v2.news_pipeline._official_json', side_effect=[
                (self.bootstrap, fresh), (self.fixtures, fresh)]), \
                patch('v2.news_pipeline.load_sources', return_value=[]), \
                patch('v2.news_pipeline.fetch_all', return_value=([], [], set(), set())):
            return run(self.root, now=self.now)

    def test_price_only_changes_rebuild_with_unchanged_player_news(self):
        result = self.scan()
        self.assertTrue(result['rebuild_required'])
        self.assertEqual(result['price_changed'], [106, 411])
        self.assertFalse(result['notify_required'])
        self.assertFalse(result['urgent'])

    def test_unowned_target_price_change_also_rebuilds(self):
        self.bootstrap['elements'][0]['now_cost'] = 155
        result = self.scan()
        self.assertTrue(result['rebuild_required'])
        self.assertEqual(result['price_changed'], [106])

    def test_rechecks_keep_requesting_rebuild_until_forecast_is_updated(self):
        self.assertTrue(self.scan()['rebuild_required'])
        self.assertTrue(self.scan()['rebuild_required'])
        self.forecast['players'][0]['price'] = 15.6
        self.forecast['players'][1]['price'] = 7.8
        self.write('app/public/data/fpl.json', self.forecast)
        result = self.scan()
        self.assertFalse(result['rebuild_required'])
        self.assertEqual(result['price_changed'], [])

    def test_cached_official_prices_cannot_trigger_a_rebuild(self):
        result = self.scan(fresh=False)
        self.assertFalse(result['official_fpl_ok'])
        self.assertFalse(result['rebuild_required'])


if __name__ == '__main__':
    unittest.main()
