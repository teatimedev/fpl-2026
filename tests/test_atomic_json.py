import concurrent.futures
import json

import pytest

from v2.decision_state import atomic_json


def test_concurrent_writers_publish_complete_owned_payloads(tmp_path):
    path = tmp_path / 'output.json'
    payloads = [dict(writer=i, values=[i] * 3000) for i in range(40)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda payload: atomic_json(path, payload), payloads))
    result = json.loads(path.read_text())
    assert result in payloads
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_payload_preserves_the_previous_file(tmp_path):
    path = tmp_path / 'output.json'
    atomic_json(path, dict(value=1))
    with pytest.raises(ValueError):
        atomic_json(path, dict(value=float('nan')))
    assert json.loads(path.read_text()) == dict(value=1)
