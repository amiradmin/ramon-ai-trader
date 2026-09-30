from pathlib import Path

EA = Path(__file__).parents[1] / 'mt5' / 'Ramon.mq5'


def test_manual_orders_keep_risk_and_ownership_guards():
    text = EA.read_text()
    body = text.split('void ExecuteManualAction(', 1)[1].split('void OnChartEvent(', 1)[0]
    assert body.index('AuditManualAction(action,"REQUEST")') < body.index('Trade.PositionClose(')
    assert body.index('LiveExecutionReady(reason)') < body.index('Trade.PositionClose(')
    close = body.split('if(action=="CLOSE")', 1)[1].split('else', 1)[0]
    assert 'POSITION_MAGIC)!=MagicNumber' in close
    assert 'PositionGetString(POSITION_SYMBOL)!=_Symbol' in close
    entry = body.split('else', 1)[1]
    for guard in ('OtherPositionOnSymbol()', 'SelectVolume(side,entry,stop)',
                  'SmallProfitStop(', 'SmallProfitTarget(', '-loss>cap+0.00001',
                  'MaxSpreadPoints', 'OrderCalcMargin(', 'LastForecastReceivedLocal'):
        assert guard in entry
    assert entry.index('-loss>cap+0.00001') < entry.index('Trade.Buy(')
    assert 'RamonManual:' in entry
    assert 'StageEntrySizing(' not in entry


def test_manual_provenance_survives_csv_and_partial_exit_learning():
    text = EA.read_text()
    payload = text.split('bool ClosedTradePayload(', 1)[1].split('// Read execution history', 1)[0]
    exits = payload.split('else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)', 1)[1]
    assert exits.index('manual_close') < exits.index('out_volume+=volume')
    assert 'ManualDealComment(deal)' in text
    assert 'FILE_ANSI|FILE_COMMON' in text.split('void MarkManualOrder(', 1)[1].split('string ManualDealComment(', 1)[0]
    audit = text.split('bool AuditManualAction(', 1)[1].split('void ManualBlocked(', 1)[0]
    assert 'WriteCsvLogs' not in audit
    assert 'USER_MANUAL' in audit
    assert 'FileFlush(file)' in audit


def test_manual_actions_only_run_on_button_clicks():
    text = EA.read_text()
    timer = text.split('void OnTimer()', 1)[1].split('void OnTradeTransaction(', 1)[0]
    assert 'ExecuteManualAction' not in timer
    chart = text.split('void OnChartEvent(', 1)[1].split('int OnInit()', 1)[0]
    assert 'CHARTEVENT_OBJECT_CLICK' in chart
    for button in ('MANUAL_BUY', 'MANUAL_SELL', 'MANUAL_CLOSE'):
        assert button in chart
