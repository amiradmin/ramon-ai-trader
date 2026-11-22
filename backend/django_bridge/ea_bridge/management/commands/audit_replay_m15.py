"""Read-only audit of historical M15 timestamps; never mutates MarketBar."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from django.core.management.base import BaseCommand, CommandError
from ea_bridge.models import MarketBar

class Command(BaseCommand):
    help = "Audit original stored M15 timestamp offsets and reception periods; never rewrite candles."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", default="XAUUSD_o")
        parser.add_argument("--start", required=True, help="UTC YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="UTC YYYY-MM-DD")

    def handle(self, *args, **options):
        try:
            start=date.fromisoformat(options["start"])
            end=date.fromisoformat(options["end"])
            if end<start:
                raise ValueError()
        except ValueError:
            raise CommandError("Expected start <= end, both YYYY-MM-DD")
        begin=datetime.combine(start,time.min,tzinfo=timezone.utc)
        finish=datetime.combine(end+timedelta(days=1),time.min,tzinfo=timezone.utc)
        qs=MarketBar.objects.filter(source=options["source"],symbol=options["symbol"],
            timeframe="M15",timestamp__gte=begin,timestamp__lt=finish).order_by("timestamp")
        offsets=Counter()
        by_received=defaultdict(Counter)
        examples=[]
        gaps=[]
        previous=None
        for stamp,received in qs.values_list("timestamp","received_at").iterator(chunk_size=2000):
            ts=int(stamp.timestamp())
            remainder=ts%900
            offsets[remainder]+=1
            key=received.date().isoformat() if received else "unknown"
            by_received[key][remainder]+=1
            if remainder and len(examples)<20:
                examples.append((stamp.isoformat(),key,remainder))
            if previous is not None and ts-previous>900 and len(gaps)<20:
                gaps.append((datetime.fromtimestamp(previous,tz=timezone.utc).isoformat(),
                             stamp.isoformat(),ts-previous))
            previous=ts
        self.stdout.write("SOURCE: "+options["source"]+" / "+options["symbol"]+" / M15")
        self.stdout.write("TOTAL: "+str(sum(offsets.values())))
        self.stdout.write("OFFSETS_SECONDS: "+str(dict(offsets.most_common(30))))
        self.stdout.write("RECEIVED_DAYS_AND_OFFSETS:")
        for key,counts in sorted(by_received.items()):
            self.stdout.write("  "+key+": "+str(dict(counts.most_common(8))))
        self.stdout.write("FIRST_20_OFF_GRID: "+str(examples))
        self.stdout.write("FIRST_20_GAPS: "+str(gaps))
        self.stdout.write("READ_ONLY: No timestamps modified. Obtain authoritative MT5 candle-open times before reimporting.")
