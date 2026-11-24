#!/usr/bin/env bash
set -u
cd "${RAMON_DIR:-$(dirname "$(realpath "${BASH_SOURCE[0]}")")}" || exit 1
umask 077
REPORT="$PWD/ramon-diagnostic-$(date +%Y%m%d-%H%M%S).txt"
export DIAG_SYMBOL="${DIAG_SYMBOL:-XAUUSD_l}"
export DIAG_SINCE="${DIAG_SINCE:-6h}"
exec > >(tee "$REPORT") 2>&1
section(){ printf '\n========== %s ==========\n' "$*"; }
run(){ timeout 90 "$@"; local rc=$?; if ((rc)); then echo "CHECK EXIT CODE: $rc"; fi; return 0; }
section 'HOST AND VERSION'
date -Is; date -u; git rev-parse --short HEAD; git status --short
section 'CONTAINERS'
run docker compose ps -a
run docker compose config --services
section 'DJANGO READ-ONLY DATABASE DIAGNOSTICS'
timeout 120 docker compose exec -T -e DIAG_SYMBOL="$DIAG_SYMBOL" django python manage.py shell <<'PY'
import os, re, json
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings
symbol=os.environ['DIAG_SYMBOL']
print('UTC NOW:',timezone.now().isoformat(), 'SYMBOL:',symbol)
secret=re.compile(r'password|secret|token|credential|api.?key|authorization|email|cookie|private.?key',re.I)
def clean(v, depth=0):
    if depth>5: return '[depth limit]'
    if isinstance(v,dict): return {str(k):('[redacted]' if secret.search(str(k)) else clean(x,depth+1)) for k,x in list(v.items())[:60]}
    if isinstance(v,(list,tuple)): return [clean(x,depth+1) for x in v[:20]]
    if isinstance(v,str): return v[:1800]
    return v
try:
    print('CACHE BACKEND:',settings.CACHES['default']['BACKEND'])
    print('CACHE READ:',repr(cache.get('ramon_diagnostic_nonexistent_probe')))
except Exception as e: print('CACHE ERROR:',type(e).__name__,str(e))
with transaction.atomic():
    with connection.cursor() as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL statement_timeout = '8000ms'")
        c.execute('SELECT CURRENT_TIMESTAMP'); print('DATABASE UTC:',c.fetchone())
    models=list(apps.get_app_config('ea_bridge').get_models())
    print('MODEL INVENTORY:',', '.join(m.__name__ for m in models))
    for m in models:
        name=m.__name__
        if not re.search(r'worker|control|strategy|decision|command|position|account|snapshot|quote|risk|opportun|signal|execution|setting|heartbeat|trade|event|bridge|terminal|market',name,re.I): continue
        if re.search(r'candle|histor|backtest|replay|laboratory|research|paper',name,re.I): continue
        print('\n---',name,'---')
        try:
            with transaction.atomic():
                fields=[f for f in m._meta.concrete_fields if not secret.search(f.name)]
                names=[f.attname for f in fields]
                print('FIELDS:',', '.join(names))
                qs=m.objects.all()
                if 'symbol' in names: qs=qs.filter(symbol=symbol)
                order=next((x for x in ['updated_at','created_at','last_seen','received_at','timestamp','id'] if x in names),m._meta.pk.attname)
                limit=40 if re.search(r'control|setting|worker|strategy',name,re.I) else 12
                rows=list(qs.order_by('-'+order).values(*names)[:limit])
                print('RECENT ROWS:',len(rows))
                for row in rows: print(json.dumps(clean(row),default=str,ensure_ascii=False))
        except Exception as e: print('MODEL CHECK ERROR:',type(e).__name__,str(e))
    try:
        with transaction.atomic():
            from ea_bridge.models import StrategyWorkerControl
            from ea_bridge.live_strategy_settings import active_settings
            from ea_bridge.thresholds import defaults_for
            print('\n--- STANDARD DEFAULTS ---'); print(json.dumps(clean(defaults_for('standard')),default=str))
            print('\n--- EFFECTIVE STRATEGY SETTINGS ---')
            for c in StrategyWorkerControl.objects.filter(symbol=symbol)[:50]:
                scope=[c.source,c.account_login,c.server_name,c.symbol]
                print(c.setup, json.dumps(clean(active_settings(c.setup,scope)),default=str))
    except Exception as e: print('SETTINGS CHECK ERROR:',type(e).__name__,str(e))
PY
rc=$?; echo "DJANGO DIAGNOSTIC EXIT: $rc"
section 'RECENT SERVICE LOGS'
while IFS= read -r svc; do
  case "$svc" in *backtest*|*replay*|*lab*|*research*|*backup*) continue;; esac
  section "SERVICE: $svc"
  run docker compose logs --no-color --since "$DIAG_SINCE" --tail 180 "$svc"
done < <(docker compose config --services)
section 'END OF REPORT'
echo 'Review report for account identifiers, credentials in raw logs, and other private data before sharing.'
echo "Report: $REPORT"
