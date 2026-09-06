import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from v2 import scouting as S

NOW = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
DEADLINE = '2026-09-12T12:30:00Z'
PLAYERS = {106: dict(id=106, name='Thiago', team='BRE')}
DOC = dict(source_id='club', publisher='Club', url='https://example.com/report',
           published_at='2026-09-05T20:00:00Z',
           text='Thiago struggled to find space. His aerial duels were effective.')
CLAIM = dict(player_id=106, mechanism='chance_volume', direction='negative',
             observation='The reporter saw Thiago struggle to find space.',
             quote='Thiago struggled to find space.', scope='single_match')


class ScoutingTests(unittest.TestCase):
    def validate(self, claim=CLAIM, doc=DOC, now=NOW):
        return S.validate_claims({'claims': [claim]}, doc, PLAYERS, 4, DEADLINE, now)

    def test_supported_quote_keeps_scope_and_expiry(self):
        rows, errors = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['scope'], 'single_match')
        self.assertEqual(rows[0]['status'], 'review')
        self.assertEqual(rows[0]['expires_at'], '2026-09-12T12:30:00+00:00')

    def test_invented_quote_and_wrong_player_rejected(self):
        for c in [dict(CLAIM, quote='He lost his place permanently'), dict(CLAIM, player_id=70)]:
            self.assertEqual(self.validate(c)[0], [])

    def test_missing_future_stale_dates_and_closed_deadline_rejected(self):
        for date in [None, '2026-09-10T20:00:00Z', '2026-08-01T20:00:00Z', '2026-09-05']:
            self.assertEqual(self.validate(doc=dict(DOC, published_at=date))[0], [])
        self.assertEqual(self.validate(now=S.utc(DEADLINE))[0], [])

    def test_conflicts_and_missing_coverage_remain_visible(self):
        c = self.validate()[0][0]
        other = dict(c, id='other', direction='positive', origin_group='independent')
        result = S.player_summary([c, other], [106, 70], NOW, 4)
        self.assertEqual(result[0]['coverage'], 'conflicting')
        self.assertEqual(result[0]['sources'], 2)
        self.assertEqual(result[1]['coverage'], 'missing')
        self.assertEqual(S.player_summary([c], [106], NOW, 5)[0]['coverage'], 'missing')

    def test_copied_article_is_one_paid_call_and_next_run_cached(self):
        with tempfile.TemporaryDirectory() as td, patch.object(S, 'CACHE', Path(td)/'cache'), \
                patch.object(S, 'OUT', Path(td)/'latest.json'), patch.object(S, 'api_key', return_value='test'), \
                patch.object(S.DeepSeek, 'extract', return_value={'claims': [CLAIM]}) as extract, \
                patch.object(S, 'datetime') as clock:
            clock.now.return_value = NOW
            clock.fromisoformat = datetime.fromisoformat
            result = S.run(PLAYERS, 4, DEADLINE, documents=[DOC, dict(DOC, url='https://copy.com/report')])
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(len(result['claims']), 1)
            again = S.run(PLAYERS, 4, DEADLINE, documents=[DOC])
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(again['cached'], 1)
            self.assertNotIn('text', json.loads((Path(td)/'latest.json').read_text()))

    def test_budget_and_missing_key_fail_before_network(self):
        for client in [S.DeepSeek(None), S.DeepSeek('test', max_calls=0), S.DeepSeek('test', budget_usd=0)]:
            with patch.object(S.urllib.request, 'urlopen') as network:
                with self.assertRaises(RuntimeError): client.extract(DOC, PLAYERS)
                network.assert_not_called()

    def test_rejects_non_object_response(self):
        self.assertEqual(S.validate_claims([], DOC, PLAYERS, 4, DEADLINE, NOW)[0], [])

    def test_real_request_contract_uses_max_reasoning_and_full_output_allowance(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"claims":[]}'}}],
                                   'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}).encode()
        with patch.object(S.urllib.request, 'urlopen', return_value=Response()) as network:
            client = S.DeepSeek('test')
            client.extract(DOC, PLAYERS)
            body = json.loads(network.call_args.args[0].data)
            self.assertEqual(body['model'], 'deepseek-v4-flash')
            self.assertEqual(body['reasoning_effort'], 'max')
            self.assertEqual(body['thinking']['type'], 'enabled')
            self.assertEqual(body['max_tokens'], 393216)
            self.assertNotIn('test', json.dumps(client.usage))


if __name__ == '__main__':
    unittest.main()
