"""Pine drawing copies preserve records and use the normal drawing lifecycle."""

from dataclasses import asdict
from pathlib import Path
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.artifacts import export_program, import_program
from packages.execution.session import Session
from packages.marketdata import read_bars


class DrawingCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.bars = read_bars(Path('conformance/linefills/bars.json'))

    def test_copy_preserves_visual_records_and_new_ids(self):
        source = '''
indicator("Copies", overlay=true)
var line guide = line.new(0, 20, 60000, 25, xloc=xloc.bar_time, color=#112233, style=line.style_dashed, width=2)
var label tag = label.new(0, 20, "original", color=#112233, style=label.style_label_up)
var box zone = box.new(0, 20, 2, 10, bgcolor=#334455, text="zone")
var line guideCopy = line.copy(id=guide)
var label tagCopy = label.copy(tag)
var box zoneCopy = box.copy(zone)
if bar_index == 1
    line.set_width(guideCopy, 4)
    label.set_text(tagCopy, "copy")
    box.set_bgcolor(zoneCopy, #778899)
'''
        for version in (5, 6):
            with self.subTest(version=version):
                program = self.session.compile(f'//@version={version}\n{source}')
                result = self.session.execute(import_program(export_program(program)), data=self.bars)
                for records, change in ((result.lines, dict(width=4)), (result.labels, dict(text='copy')),
                                        (result.boxes, dict(bgcolor='#778899FF'))):
                    original, clone = map(asdict, records)
                    self.assertEqual(original['id'], 1)
                    self.assertEqual(clone, {**original, 'id': 2, **change})

    def test_capacity_and_dead_sources_use_normal_drawing_lifecycle(self):
        source = '''//@version=6
indicator("Copies", max_lines_count=1)
var line guide = line.new(0, 20, 3, 20)
if bar_index == 1
    line.copy(guide)
    line.copy(guide)
    line.copy(na)
    line.set_xy2(guide, 3, 25)
'''
        _, result = self.session.run(source, data=self.bars)
        self.assertEqual([asdict(line) for line in result.lines], [dict(id=2, x1=0, y1=20., x2=3, y2=20.)])

    def test_copy_version_and_type_rejections(self):
        for version in range(1, 5):
            for kind in ('line', 'label', 'box'):
                with self.subTest(version=version, kind=kind), self.assertRaises(OpenPineError) as error:
                    self.session.compile(f'//@version={version}\nstudy("Copies")\n{kind}.copy(na)\nplot(close)\n')
                self.assertEqual(error.exception.diagnostic.code, 'DRAWING_COPY_VERSION')
        with self.assertRaises(OpenPineError) as error:
            self.session.compile('//@version=6\nindicator("Copies")\nline.copy(label.new(0, 20))\n')
        self.assertEqual(error.exception.diagnostic.code, 'ARGUMENT_TYPE')
