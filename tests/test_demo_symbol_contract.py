from pathlib import Path


def test_ea_accepts_chart_symbol_for_gold_variants():
    source=Path("mt5/Ramon.mq5").read_text()
    assert 'input string TradeSymbol = "";' in source
    assert '(TradeSymbol!="" && _Symbol!=TradeSymbol)' in source
    assert 'StringFind(_Symbol,"XAUUSD")!=0' in source
    assert '"symbol\\":\\""+_Symbol' not in source or '_Symbol' in source
    assert 'input bool EnableLiveTrading = false;' in source


def test_collector_follows_chart_symbol_and_names_outputs_separately():
    source=Path("mt5/RamonM1M5Collector.mq5").read_text()
    assert 'input string ExportSymbol="";' in source
    assert 'ActiveExportSymbol=(ExportSymbol=="" ? _Symbol : ExportSymbol);' in source
    assert 'CopyRates(ActiveExportSymbol,' in source
    assert 'filename="RamonLive_"+ActiveExportSymbol' in source
    assert 'SymbolSelect(ActiveExportSymbol,true)' in source
