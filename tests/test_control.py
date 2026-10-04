import math
from pathlib import Path
import pytest
from ramon.monitor import save_control, control_state

@pytest.mark.parametrize('value', [None, True, '0.2', 0, -1, .51, math.inf, math.nan])
def test_invalid_control_never_changes_file(tmp_path, value):
    path = tmp_path / 'Ramon_Diagnostic.txt'
    save_control(path, .35)
    with pytest.raises(ValueError):
        save_control(path, value)
    assert (tmp_path / 'Ramon_Control.txt').read_text().strip() == '0.35000000'

def test_saved_cap_is_not_claimed_as_applied(tmp_path):
    path = tmp_path / 'Ramon_Diagnostic.txt'
    save_control(path, .20)
    state = control_state(path)
    assert state['requested'] == .20
    assert state['observed'] is None
    assert not state['fresh']
    assert list(tmp_path.iterdir()) == [tmp_path / 'Ramon_Control.txt']

def test_cap_is_checked_before_broker_order():
    source = Path('mt5/Ramon.mq5').read_text()
    gate = source.index('Entry risk exceeds Control cap')
    order = source.index('Trade.Buy(volume,_Symbol,0.0,stop,target,trade_comment)')
    assert gate < order
    assert 'MathMin(EffectiveRiskPerTradeUSD(),MaxExecutableRiskUSD)' in source
    assert 'if(SmallOnlyMode) return;' in source[source.index('void ReadControlRiskCap()'):]

def test_control_http_save_and_cross_origin_rejection(tmp_path):
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from ramon.monitor import handler_for
    diag = tmp_path / 'Ramon_Diagnostic.txt'
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(tmp_path / 'history.db', diag, 'XAUUSD_l', 'http://127.0.0.1:1/health'))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for route in ('/control', '/control.js', '/api/control'):
            with urlopen(base + route) as response:
                assert response.status == 200
        request = Request(base + '/api/control', data=b'{"max_executable_risk_usd":0.21}', headers={'Content-Type': 'application/json', 'Origin': base})
        with urlopen(request) as response:
            assert json.load(response)['requested'] == .21
        request = Request(base + '/api/control', data=b'{"max_executable_risk_usd":0.40}', headers={'Content-Type': 'application/json', 'Origin': 'https://example.org'})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
        assert control_state(diag)['requested'] == .21
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
