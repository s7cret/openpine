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
            self.assertIn('параметры вне профиля line_style, line_width ·', detail)
            self.assertIn('force_overlay по имени', detail)
            self.assertEqual(rows['array.push', '0'][0], 'PARTIAL')
            self.assertEqual(rows['timestamp', '0'][0], 'MISSING')  # String overload.


if __name__ == '__main__':
    unittest.main()
