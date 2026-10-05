import json
from unittest.mock import patch

from ramon.trade_outbox import deliver_pending


def test_success_requires_saved_ack_and_recovers_after_receipt(tmp_path):
    item = tmp_path / 'outcome.json'
    item.write_text(json.dumps({'trade_key': 'server:account:position'}))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"saved":true}'
    with patch('ramon.trade_outbox.urlopen', return_value=Response()):
        assert deliver_pending(tmp_path, 'http://model/trades') == 1
    assert not item.exists()
    assert item.with_suffix('.ack').read_text() == 'server:account:position'
    item.write_text(json.dumps({'trade_key': 'server:account:position'}))
    with patch('ramon.trade_outbox.urlopen') as send:
        assert deliver_pending(tmp_path, 'http://model/trades') == 0
        send.assert_not_called()
    assert not item.exists()


def test_network_failure_preserves_outcome_without_receipt(tmp_path):
    item = tmp_path / 'outcome.json'
    item.write_text('{"trade_key":"server:account:position"}')
    with patch('ramon.trade_outbox.urlopen', side_effect=OSError('offline')):
        assert deliver_pending(tmp_path, 'http://model/trades') == 0
    assert item.exists()
    assert not item.with_suffix('.ack').exists()
