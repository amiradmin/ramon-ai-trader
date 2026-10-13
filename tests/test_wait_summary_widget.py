"""The inline WAIT widget must be present and served by monitor.py."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/"src/ramon/monitor_assets/index.html").read_text(encoding="utf-8")
JS=(ROOT/"src/ramon/monitor_assets/wait-summary.js").read_text(encoding="utf-8")
SERVER=(ROOT/"src/ramon/monitor.py").read_text(encoding="utf-8")

def test_inline_wait_is_above_training_health():
    assert HTML.index('id="wait-summary-panel"') < HTML.index('aria-label="سلامت داده‌های آموزشی رامون"')
    assert 'id="wait-summary-grid"' in HTML
    assert '<script src="/wait-summary.js" defer></script>' in HTML

def test_wait_summary_read_only_and_linked():
    assert 'fetch("/api/wait-analysis?limit=300"' in JS
    assert 'setInterval(refresh, 60000)' in JS
    assert 'order_intent' not in JS
    assert "method: 'POST'" not in JS and 'method:"POST"' not in JS
    assert 'href="/wait-analysis"' in HTML

def test_asset_mime_and_route():
    assert '"/wait-summary.js": "wait-summary.js"' in SERVER
    assert '"wait-summary.js": "text/javascript"' in SERVER
