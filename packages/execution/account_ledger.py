"""Settle journal fills in a reporting currency and mark open exposure per bar."""
from decimal import Decimal, localcontext


def account_rows(events, initial_capital, rate_at, start):
    def amount(value):
        return format(value.normalize(), 'f') if value else '0'

    rows = []
    cash = None
    gross = fees = Decimal(0)
    with localcontext() as context:
        context.prec = 80
        for event in events:
            rate = Decimal(str(rate_at(event['time'])))
            if cash is None:
                cash = Decimal(initial_capital) * rate
            value = event['value']
            if event['kind'] == 'fill':
                proceeds = -value['direction'] * Decimal(value['quantity']) * Decimal(value['price']) * rate
                fee = Decimal(value['commission']) * rate
                gross += proceeds
                fees += fee
                cash += proceeds - fee
            elif event['kind'] == 'mark':
                if event['bar_index'] >= start:
                    native_cash = Decimal(value.get('cash_exact', value['cash']))
                    native_equity = Decimal(value.get('equity_exact', value['equity']))
                    exposure = (native_equity - native_cash) * rate
                    rows.append(dict(bar_index=event['bar_index'], time=event['time'], rate=amount(rate),
                                     position=value['position_size'], gross_flow=amount(gross), fee=amount(fees),
                                     cash=amount(cash), exposure=amount(exposure), equity=amount(cash+exposure)))
                gross = fees = Decimal(0)
    return rows
