"""Independent accounting examples for report-only metrics."""
import unittest
from packages.optimizer.reporting import strategy_metrics


class ResearchMetricsTests(unittest.TestCase):
    def test_drawdown_percentage_closed_net_and_open_equity_are_distinct(self):
        summary = {'simulation': {'policy': {'initial_capital': '100'}}}
        events = [{'kind': 'mark', 'time': i, 'value': {'equity': value, 'commission': '3'}}
                  for i, value in enumerate(('100', '80', '120', '90'))]
        events += [{'kind': 'trade', 'time': 3, 'value': {'net_profit': value}} for value in ('10', '-5', '0')]
        result = strategy_metrics(summary, iter(events))
        values = result['values']
        self.assertEqual(values['return_percent'], '-10.0')
        self.assertEqual(values['max_drawdown'], '30')
        self.assertEqual(values['max_drawdown_percent'], '25.00')
        self.assertEqual(values['profit_factor'], '2')
        self.assertEqual(values['closed_trades'], '3')
        self.assertTrue(values['win_rate_percent'].startswith('33.3333'))

    def test_missing_denominators_and_window_are_explicit(self):
        summary = {'simulation': {'policy': {'initial_capital': '100'}},
                   'evaluation_window': {'score_start_ms': 1, 'end_ms': 3}}
        events = [{'kind': 'trade', 'time': 0, 'value': {'net_profit': '-99'}},
                  {'kind': 'mark', 'time': 1, 'value': {'equity': '100', 'commission': '0'}}]
        result = strategy_metrics(summary, events)
        self.assertIsNone(result['values']['profit_factor'])
        self.assertIsNone(result['values']['win_rate_percent'])
        self.assertEqual(result['values']['closed_trades'], '0')
        self.assertEqual(strategy_metrics({}, [])['status'], 'not_strategy')
        self.assertEqual(strategy_metrics(summary, [])['status'], 'no_scored_marks')
        with self.assertRaises(InterruptedError):strategy_metrics(summary, events, cancelled=lambda: True)
