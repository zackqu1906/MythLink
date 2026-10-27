"""Check lossless eight-direction collection, persistence and descriptive features."""
import copy
import json
import math
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import eight_direction_selection as profile
from analyze_eight_direction_selection import features, analyze, write_report
from ring_selection_collector import CollectionWindow
from selection_capture import ContactCapture
from check_selection_capture import move, contact

ROOT = Path(__file__).resolve().parents[1]


def make_trace(vectors):
    capture = ContactCapture()
    capture.feed(contact("down", 1, 3))
    for step, (dx, dy) in enumerate([(0, 0)]+vectors, 1):
        capture.feed(move(step, dx, dy))
    return capture.feed(contact("up", len(vectors)+2, len(vectors)+5))


class StudyTests(unittest.TestCase):
    def test_plan_has_every_direction_variant_and_confusable_up_controls(self):
        tasks = profile.inventory()
        self.assertEqual(len(tasks), 100)
        self.assertEqual(len({t['label'] for t in tasks}), 100)
        self.assertEqual(sum(t['kind'] == 'selection' for t in tasks), 36)
        for d, _, _ in profile.DIRECTIONS:
            for variant in ('natural', 'short', 'long', 'pause'):
                self.assertIn(f'select-{d}-{variant}', {t['label'] for t in tasks})
        self.assertIn('single-up-long-pause', {t['label'] for t in tasks})
        for phase in ('train', 'evaluation'):
            session = profile.new_session('P01', phase, 3)
            self.assertEqual(len(session['tasks']), 300)
            profile.validate_session(session)
        self.assertEqual(len([m for m in profile.RADIAL_MENU if m[1] == 0]), 3)
        self.assertEqual(len([m for m in profile.RADIAL_MENU if m[1] != 0]), 5)
        menu = {d: text for d, _, _, text in profile.RADIAL_MENU}
        for d, rank in [('down', 1), ('down-left', 2), ('down-right', 3), ('left', 4), ('right', 5)]:
            self.assertIn(f'候选 {rank}', menu[d])

    def test_rejects_legacy_wrong_labels_duplicate_and_completed_draft(self):
        session = profile.new_session('P01')
        bad = copy.deepcopy(session)
        bad['tasks'][0] = copy.deepcopy(bad['tasks'][1])
        with self.assertRaises(ValueError): profile.validate_session(bad)
        bad = copy.deepcopy(session)
        bad['tasks'][0]['note'] = 'changed'
        with self.assertRaises(ValueError): profile.validate_session(bad)
        legacy = copy.deepcopy(session)
        legacy['schema'] = 'ring-selection-collection'
        with self.assertRaises(ValueError): profile.validate_session(legacy)
        trace = make_trace([(0, -2)]*30)
        session['samples'] = [{**trace, 'taskIndex': i, 'task': t} for i, t in enumerate(session['tasks'])]
        session['draft'] = trace
        with self.assertRaises(ValueError): profile.validate_session(session)

    def test_turn_split_handles_return_to_origin_instead_of_net_direction(self):
        f = features(make_trace([(0, -2)]*40+[(0, 2)]*40))
        self.assertEqual(f['integrationError'], 0)
        self.assertIsNone(f['netAngleDeg'])
        self.assertEqual(f['geometrySplit']['firstAngleDeg'], -90)
        self.assertEqual(f['geometrySplit']['secondAngleDeg'], 90)
        self.assertAlmostEqual(f['geometrySplit']['chordGain'], 1)

    def test_all_eight_second_directions_at_turn(self):
        for direction, _, vector in profile.DIRECTIONS:
            norm = math.hypot(*vector)
            second = (2*vector[0]/norm, 2*vector[1]/norm)
            f = features(make_trace([(0, -2)]*40+[second]*40))
            with self.subTest(direction=direction):
                self.assertAlmostEqual(f['geometrySplit']['firstAngleDeg'], -90)
                self.assertAlmostEqual(f['geometrySplit']['secondAngleDeg'], math.degrees(math.atan2(second[1], second[0])))

    def test_pause_evidence_and_identical_up_up_are_label_independent(self):
        continuous = make_trace([(0, -2)]*100)
        paused = make_trace([(0, -2)]*40+[(0, 0)]*40+[(0, -2)]*40)
        a, b = features(continuous), features(paused)
        self.assertAlmostEqual(a['geometrySplit']['chordGain'], 0)
        self.assertAlmostEqual(a['pauseWindows']['60']['speedRatio'], 1)
        self.assertAlmostEqual(b['pauseWindows']['60']['speedRatio'], 0)
        self.assertEqual(b['lowSpeedSplit']['firstAngleDeg'], -90)
        self.assertEqual(b['lowSpeedSplit']['secondAngleDeg'], -90)
        continuous['task'] = {'direction': 'up', 'kind': 'selection'}
        self.assertEqual(a, features(continuous))
        continuous['task'] = {'direction': 'up', 'kind': 'control'}
        self.assertEqual(a, features(continuous))

    def test_returning_motion_is_not_a_pause_and_stationary_is_not_a_direction(self):
        loop = features(make_trace([(1, 0), (-1, 0)]*100))
        self.assertAlmostEqual(loop['pauseWindows']['60']['speedRatio'], 1)
        zero = features(make_trace([(0, 0)]*50))
        self.assertIsNone(zero['netAngleDeg'])
        self.assertIsNone(zero['geometrySplit'])
        self.assertEqual(zero['pauseWindows'], {})

    def test_report_roundtrip_and_empty_export_without_fabricated_accuracy(self):
        session = profile.new_session('P01')
        trace = make_trace([(0, -2)]*40+[(2, 0)]*40)
        session['samples'] = [{**trace, 'taskIndex': 0, 'task': session['tasks'][0]}]
        session['draft'] = make_trace([(0, -2)]*40)
        report = analyze(session)
        self.assertEqual(report['confirmedCount'], 1)
        self.assertTrue(report['draftExcluded'])
        self.assertNotIn('accuracy', report)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path = write_report(report, folder)
            self.assertEqual(json.loads(path.with_suffix('.json').read_text(encoding='utf-8')), report)
            self.assertIn('geometrySplit_secondAngleDeg', path.with_suffix('.csv').read_text(encoding='utf-8-sig'))
            write_report(analyze(profile.new_session('P01')), folder)

    def test_window_menu_default_rounds_delayed_edges_pause_and_resume(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory(dir=ROOT) as folder:
                window = CollectionWindow(root, folder, profile=profile)
                self.assertEqual(window.rounds.get(), 1)
                self.assertEqual(len(window.direction_cells), 8)
                window.create()
                window.connected = window.warm = True
                window.arm()
                for step in range(1, 6): window.event(move(step, 0, -2))
                window.event(contact('down', 1, 5))
                for step in range(6, 81): window.event(move(step, 0, 0 if 30 <= step < 55 else -2))
                window.event(contact('up', 81, 85))
                self.assertEqual(window.session['draft']['startStep'], 1)
                self.assertEqual(len(window.session['draft']['points']), 81)
                window.save_sample()
                restored = profile.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
                self.assertEqual(len(restored['samples']), 1)
                self.assertEqual(restored['samples'][0]['points'][35]['y'], restored['samples'][0]['points'][36]['y'])
                self.assertIn('八向', window.root.title())
                path = window.path
                window.raw_stream.close()
                window.raw_stream = None
                with patch('ring_selection_collector.filedialog.askopenfilename', return_value=str(path)), \
                     patch('ring_selection_collector.messagebox.askyesno', return_value=True):
                    window.load()
                self.assertEqual(len(window.session['samples']), 1)
                self.assertEqual(window.prefix, 'selection-eight')
                window.raw_stream.close()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
