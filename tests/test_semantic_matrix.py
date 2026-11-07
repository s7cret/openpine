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
            else:
                self.assertEqual(rows['0'][0], 'MISSING')
                for overload, operation in (('1', 'bands.fill_hline'), ('2', 'bands.fill_plot')):
                    self.assertEqual(rows[overload][0], 'PARTIAL')
                    self.assertIn(operation, rows[overload][1])

    def test_table_overlay_only_expands_the_v5_and_v6_constructor(self):
        self.assertEqual(self.availability['table.new'], {4, 5, 6})
        for version in (4, 5, 6):
            (status, detail), = ((status, detail) for name, _, status, detail in self.rows[version] if name == 'table.new')
            self.assertEqual(status, 'PARTIAL')
            self.assertEqual('table.new_overlay' in detail, version >= 5)
            self.assertIn('frame_color, frame_width, border_color, border_width', detail)


if __name__ == '__main__':
    unittest.main()
