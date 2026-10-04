"""Check progress semantics that front ends use for determinate indicators."""

import unittest

from analog_fpv_compressor.progress import emit_progress


class ProgressTests(unittest.TestCase):
    def test_fraction_is_stage_local_and_preserves_legacy_counters(self):
        events = []
        emit_progress(events.append, "encode", 3, 10, unit="frames", frames=3, total_frames=10)
        self.assertEqual(events[0].code, "progress")
        self.assertEqual(events[0].data["fraction"], .3)
        self.assertEqual(events[0].data["completed"], events[0].data["frames"])
        self.assertEqual(events[0].data["total"], events[0].data["total_frames"])

    def test_unknown_and_empty_totals_do_not_claim_a_percentage(self):
        for completed, total in ((None, None), (0, None), (0, 0), (None, 10)):
            events = []
            emit_progress(events.append, "validate", completed, total)
            self.assertIsNone(events[0].data["fraction"])

    def test_fraction_stays_within_indicator_range(self):
        events = []
        emit_progress(events.append, "encode", 11, 10)
        emit_progress(events.append, "encode", -1, 10)
        self.assertEqual([event.data["fraction"] for event in events], [1., 0.])


if __name__ == "__main__":
    unittest.main()
