import copy
import json
import pytest
from v2.confirmed_transfers import apply_confirmed_transfers
from datetime import datetime, timedelta, timezone


@pytest.fixture
def case(tmp_path):
    players = {1: {'name': 'Old', 'pos': 'FWD', 'team': 'BRE'},
               2: {'name': 'New', 'pos': 'FWD', 'team': 'CHE'}}
    state = dict(entry_id=123, picks_gw=3, ids=[1], bank=0, ft=3,
                 lineup={'captain': 1}, source='FPL entry 123, picks from GW3')
    report = dict(entry_id=123, gw=4, base_picks_gw=3,
                  confirmed_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                  expires_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                  basis='User confirmed, prices inferred',
                  transfers=[dict(out=1, **{'in': 2}, out_cost=79, in_cost=77)])
    path = tmp_path / 'confirmed.json'
    path.write_text(json.dumps(report))
    return state, players, path


def test_user_report_updates_squad_bank_ft_without_claiming_live_lineup(case):
    state, players, path = case
    original = copy.deepcopy(state)
    result = apply_confirmed_transfers(state, players, 4, path)
    assert result['ids'] == [2]
    assert (result['bank'], result['ft']) == (.2, 2)
    assert result['lineup'] is None
    assert result['confirmed_transfers'][0]['element_in_cost'] == 77
    assert state == original


def test_report_expires_for_next_deadline_and_ignores_other_accounts(case):
    state, players, path = case
    assert apply_confirmed_transfers(state, players, 5, path) is state
    state['entry_id'] = 456
    assert apply_confirmed_transfers(state, players, 4, path) is state


def test_conflict_fails_instead_of_double_spending_or_silently_guessing(case):
    state, players, path = case
    state['ids'] = [2]
    with pytest.raises(ValueError, match='conflicts'):
        apply_confirmed_transfers(state, players, 4, path)


def test_mismatched_public_baseline_is_rejected(case):
    state, players, path = case
    state['picks_gw'] = 2
    with pytest.raises(ValueError, match='baseline'):
        apply_confirmed_transfers(state, players, 4, path)


def test_expiry_does_not_depend_on_public_is_next_flag_rollover(case):
    state, players, path = case
    data = json.loads(path.read_text())
    data['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(data))
    assert apply_confirmed_transfers(state, players, 4, path) is state
