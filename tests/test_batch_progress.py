"""Check whole-queue estimates across stages, split outputs and failed inputs."""

import unittest

from analog_fpv_compressor.gui.progress import BatchProgress, duration_text


class BatchProgressTests(unittest.TestCase):
    def test_multiple_files_and_parts_do_not_reset_overall_progress(self):
        progress = BatchProgress(2)
        def update(stage, fraction, part=1, job=1):
            progress.update("progress", {"stage": stage, "fraction": fraction,
                            "job_index": job, "part_index": part, "part_count": 2})
        update("snow", .5)
        analysis = progress.fraction
        update("encode", 1)
        first = progress.fraction
        update("validate", None)
        self.assertGreaterEqual(progress.fraction, first)
        update("encode", 0, part=2)
        self.assertGreater(progress.fraction, first)
        self.assertGreater(first, analysis)
        progress.update("job.completed", {"job_index": 1})
        self.assertEqual(progress.fraction, .5)
        update("prepare", 0, job=2)
        self.assertEqual(progress.fraction, .5)
        progress.update("job.failed", {"job_index": 2})
        self.assertLess(progress.fraction, 1)

    def test_analysis_only_skips_output_budget(self):
        progress = BatchProgress(1, analyze_only=True)
        progress.update("progress", {"job_index": 1, "stage": "interlace",
                                    "state": "completed", "fraction": None})
        self.assertGreater(progress.fraction, .99)
        self.assertLess(progress.fraction, 1)

    def test_cancel_does_not_fill_unprocessed_work(self):
        progress = BatchProgress(3)
        progress.update("job.failed", {"job_index": 1})
        progress.update("job.cancelled", {"job_index": 2})
        self.assertAlmostEqual(progress.fraction, 1 / 3)

    def test_estimate_requires_progress_and_uses_elapsed_time(self):
        progress = BatchProgress(2)
        self.assertIsNone(progress.estimated_total(30))
        progress.update("job.completed", {"job_index": 1})
        self.assertIsNone(progress.estimated_total(1))
        self.assertEqual(progress.estimated_total(30), 60)
        self.assertEqual(duration_text(3661), "01:01:01")
