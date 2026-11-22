"""Read-only data-quality view; no strategy or trading controls."""
from datetime import datetime, timezone
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET
from .management.commands.audit_market_bar_quality import inspect_rows
from .models import MarketBar

MAX_ROWS = 50000


@require_GET
def strategy_validation(request):
    source = request.GET.get('source', 'ramon-command-ea-1').strip()
    symbol = request.GET.get('symbol', 'XAUUSD_i').strip()
    try:
        year = int(request.GET.get('year', '2026'))
    except ValueError:
        return HttpResponseBadRequest('Invalid year')
    if not source or len(source) > 128 or not symbol or len(symbol) > 32 or not 2000 <= year <= 2100:
        return HttpResponseBadRequest('Invalid source, symbol or year')
    query = MarketBar.objects.filter(source=source, symbol=symbol,
        timeframe__in=['M1', 'M5', 'M15', 'H1'],
        timestamp__gte=datetime(year, 1, 1, tzinfo=timezone.utc),
        timestamp__lt=datetime(year+1, 1, 1, tzinfo=timezone.utc))
    count = query.count()
    groups = None
    if count <= MAX_ROWS:
        rows = query.order_by('source', 'symbol', 'timeframe', 'timestamp', 'pk').iterator(chunk_size=5000)
        found = {group['timeframe']: group for group in inspect_rows(rows)}
        groups = [found.get(tf, {'timeframe': tf, 'status': 'NO_DATA', 'rows': 0})
                  for tf in ('M1', 'M5', 'M15', 'H1')]
    return render(request, 'ea_bridge/strategy_validation.html', {
        'source': source, 'symbol': symbol, 'year': year, 'groups': groups,
        'row_limit_exceeded': count > MAX_ROWS, 'max_rows': MAX_ROWS})
