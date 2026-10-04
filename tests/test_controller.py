"""Behavioral safeguards for noise classification and option resolution."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from analog_fpv_compressor.controller import validate_settings
from analog_fpv_compressor.detection import complement, snow_intervals
from analog_fpv_compressor.models import Settings


class SnowSafetyTests(unittest.TestCase):
    def test_stationary_scene_is_never_snow(self):
        times = np.arange(120) / 30
        rows = np.tile([140, 28, 26, 1.08, .999], (120, 1))
        self.assertEqual(snow_intervals(rows, times, 1 / 30)[0], ())

    def test_single_structured_scene_inside_snow_is_preserved(self):
        times = np.arange(180) / 30
        rows = np.tile([160, 35, 4, 40, .01], (180, 1))
        rows[90] = [120, 60, 55, 35, .05]
        cuts, _ = snow_intervals(rows, times, 1 / 30)
        self.assertEqual(len(cuts), 2)
        self.assertTrue(all(not begin <= times[90] < end for begin, end in cuts))

    def test_short_interference_is_not_cut(self):
        times = np.arange(20) / 30
        rows = np.tile([160, 35, 4, 40, .01], (20, 1))
        self.assertEqual(snow_intervals(rows, times, 1 / 30)[0], ())

    def test_gap_is_not_new_snow_evidence(self):
        # Two decoded noise images separated by a huge gap are insufficient.
        rows = [[140, 30, 3, 40, .01], [140, 30, 3, 40, .01]]
        self.assertEqual(snow_intervals(rows, [0, 20], 1 / 30)[0], ())

    def test_complement_keeps_returning_scene(self):
        self.assertEqual(complement([(61, 87), (90, 94)], 0, 162),
                         ((0, 61), (87, 90), (94, 162)))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        source = self.root / "input.avi"
        source.write_bytes(b"fixture")
        self.settings = Settings(source, self.root / "output.mkv")

    def tearDown(self):
        self.directory.cleanup()

    def test_rate_control_conflict_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CRF and bitrate"):
            validate_settings(replace(self.settings, crf=48, bitrate="500k"))

    def test_existing_source_cannot_be_output(self):
        with self.assertRaisesRegex(ValueError, "different files"):
            validate_settings(replace(self.settings, output_path=self.settings.input_path))

    def test_invalid_options_fail_before_processing(self):
        for options in ({"threads": 0}, {"crf": 64}, {"scale": "479x360"},
                        {"no_signal_min_duration": float("nan")}, {"bitrate": "0k"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_settings(replace(self.settings, **options))


if __name__ == "__main__":
    unittest.main()
