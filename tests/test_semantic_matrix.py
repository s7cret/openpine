"""Keep Pine availability, admitted overloads and fixture expectations distinct."""
import unittest

from tools.semantic_matrix import matrix
from tools.specification import load_snapshot


class SemanticMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.availability = matrix()

    def test_each_pinned_function_overload_has_a_row(self):
        snapshot = load_snapshot()
        for version, rows in self.rows.items():
            entries = [entry for entry in snapshot.references[version]['entries'] if entry['category'] == 'functions']
            self.assertEqual(len(rows), len(entries), version)
            self.assertEqual({row[0] for row in rows}, {entry['name'] for entry in entries}, version)

    def test_full_partial_missing_and_absence_are_distinct(self):
        self.assertEqual(self.availability['polyline.new'], {5, 6})
        for version in (5, 6):
            rows = {(name, overload): (status, detail) for name, overload, status, detail in self.rows[version]}
            self.assertEqual(rows['chart.point.from_index', '0'][0], 'FULL')
            for overload in ('0', '1'):
                self.assertEqual(rows['line.new', overload][0], 'PARTIAL')
                self.assertIn('overlay', rows['line.new', overload][1])
            status, detail = rows['polyline.new', '0']
            self.assertEqual(status, 'PARTIAL')
            self.assertIn('polyline.new_styled', detail)
            self.assertIn('curved=false', detail)
            for name in ('plot', 'bgcolor', 'plotshape', 'plotchar'):
                self.assertTrue(any(status == 'PARTIAL' and 'force_overlay' not in detail
                    for (source, _), (status, detail) in rows.items() if source == name), name)
            self.assertEqual(rows['array.push', '0'][0], 'PARTIAL')
            self.assertEqual(rows['timestamp', '0'][0], 'MISSING')  # String overload.

    def test_arrows_are_admitted_across_versions_and_window_is_in_covered_slice(self):
        for version, rows in self.rows.items():
            for name in ('plotarrow','plotshape','plotchar'):
                (status, detail), = ((status, detail) for source,_,status,detail in rows if source==name)
                self.assertEqual(status,'PARTIAL')
                self.assertIn(f'markers.{name}',detail)
                if version>=3:
                    self.assertNotIn('show_last',detail)

    def test_visual_ohlc_is_partial_in_all_versions(self):
        for version, rows in self.rows.items():
            for source in ('plotbar', 'plotcandle'):
                (status, detail), = ((status, detail) for name, _, status, detail in rows if name == source)
                self.assertEqual(status, 'PARTIAL')
                self.assertIn(f'ohlc.{source}', detail)
                self.assertEqual('реконструирована' in detail, version < 3)

    def test_fill_maps_reference_handles_without_matching_the_gradient(self):
        for version, entries in self.rows.items():
            rows = {overload: (status, detail) for name, overload, status, detail in entries if name == 'fill'}
            if version < 5:
                (status, detail), = rows.values()
                self.assertEqual(status, 'PARTIAL')
                self.assertIn('bands.fill_plot', detail)
                self.assertIn('bands.fill_hline', detail)
                if version>=3:
                    self.assertNotIn('show_last',detail)
            else:
                self.assertEqual(rows['0'][0], 'PARTIAL')
                self.assertIn('bands.gradient_plot',rows['0'][1])
                self.assertNotIn('bands.gradient_hline',rows['0'][1])
                for overload, operation in (('1', 'bands.fill_hline'), ('2', 'bands.fill_plot')):
                    self.assertEqual(rows[overload][0], 'PARTIAL')
                    self.assertIn(operation, rows[overload][1])
                self.assertNotIn('show_last',rows['2'][1])

    def test_visual_window_parameters_follow_versioned_overloads(self):
        targets = {'plot','barcolor','bgcolor','plotbar','plotcandle'}
        for version in (3,4,5,6):
            rows = [row for row in self.rows[version] if row[0] in targets]
            self.assertEqual({row[0] for row in rows},targets)
            for name,_,status,detail in rows:
                self.assertEqual(status,'PARTIAL',name)
                self.assertNotIn('show_last',detail,name)

    def test_plot_appearance_is_reflected_in_every_pinned_version(self):
        for version,rows in self.rows.items():
            (status,detail),=((status,detail) for name,_,status,detail in rows if name=='plot')
            self.assertEqual(status,'PARTIAL',version)
            for parameter in ('histbase','trackprice','join','linestyle'):
                self.assertNotIn(parameter,detail,version)

    def test_table_overlay_only_expands_the_v5_and_v6_constructor(self):
        self.assertEqual(self.availability['table.new'], {4, 5, 6})
        for version in (4, 5, 6):
            (status, detail), = ((status, detail) for name, _, status, detail in self.rows[version] if name == 'table.new')
            self.assertEqual(status, 'PARTIAL')
            constructor = 'table.new_styled_overlay' if version >= 5 else 'table.new_styled'
            self.assertIn(f'`{constructor}`', detail)
            self.assertIn('frame_color, frame_width, border_color, border_width', detail)

    def test_close_all_and_exit_alert_arguments_match_pinned_overloads(self):
        for version in (4, 5, 6):
            for name, _, status, detail in self.rows[version]:
                if name not in ('strategy.close_all', 'strategy.exit', 'strategy.order'):
                    continue
                self.assertEqual(status, 'PARTIAL', (version, name))
                self.assertNotIn('alert_message', detail, (version, name))
                if version >= 5:
                    for parameter in ('disable_alert', 'alert_profit', 'alert_loss', 'alert_trailing'):
                        self.assertNotIn(parameter, detail, (version, name))


if __name__ == '__main__':
    unittest.main()
