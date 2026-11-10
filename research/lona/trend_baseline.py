# LONA research only: simplified EMA policy, not the Ramon SignalFlow engine.
# Entries/exits are market orders on the next bar open (buy_on_close=False).
# Stops and targets are evaluated on CLOSES, not intrabar stop orders.
class RamonTrendBaseline(bt.Strategy):
    params = (('order_size_pct', 0.10),)

    def __init__(self):
        self.inds, self.orders, self.entry_bar, self.entry_atr, self.seen = {}, {}, {}, {}, {}
        for d in self.datas:
            self.inds[d] = {'fast': bt.indicators.EMA(d, period=20),
                            'slow': bt.indicators.EMA(d, period=50),
                            'atr': bt.indicators.ATR(d, period=14)}
            self.orders[d] = None
            self.entry_bar[d] = None
            self.entry_atr[d] = None
            self.seen[d] = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.entry_bar[order.data] = len(order.data) if self.getposition(data=order.data).size else None
        if not order.alive():
            self.orders[order.data] = None

    def next(self):
        for d in self.datas:
            if len(d) < 51 or len(d) == self.seen[d]:
                continue
            self.seen[d] = len(d)
            if self.orders[d]:
                continue
            ind = self.inds[d]
            fast, slow, atr = ind['fast'][0], ind['slow'][0], ind['atr'][0]
            if atr <= 0:
                continue
            position = self.getposition(data=d)
            if position.size:
                direction = 1 if position.size > 0 else -1
                move = direction * (d.close[0] - position.price)
                held = len(d) - self.entry_bar[d]
                trend_broken = fast <= slow if direction > 0 else fast >= slow
                if trend_broken or held >= 24 or move <= -2 * self.entry_atr[d] or move >= 3 * self.entry_atr[d]:
                    self.orders[d] = self.order_target_percent(data=d, target=0.0)
                continue
            direction = 1 if fast > slow and ind['fast'][-1] <= ind['slow'][-1] else -1 if fast < slow and ind['fast'][-1] >= ind['slow'][-1] else 0
            if direction:
                self.entry_atr[d] = atr
                self.orders[d] = self.order_target_percent(data=d, target=direction * self.p.order_size_pct)
