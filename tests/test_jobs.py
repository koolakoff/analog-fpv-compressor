"""Verify batch naming, collision protection and frame-preserving flight plans."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from analog_fpv_compressor.jobs import expand_inputs, make_jobs, plan_outputs, validate_jobs, write_manifest
from analog_fpv_compressor.models import Analysis, Plan, Settings


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
        return path

    def test_globs_are_sorted_deduplicated_and_literal_brackets_work(self):
        second, first = self.source("b.AVI"), self.source("a.AVI")
        bracketed = self.source("flight [one].mkv")
        inputs = expand_inputs([second, str(self.root / "*.AVI"), first, bracketed])
        self.assertEqual(inputs, (second.resolve(), first.resolve(), bracketed.resolve()))

    def test_unmatched_pattern_is_an_error_not_a_silent_skip(self):
        source = self.source("a.AVI")
        with self.assertRaisesRegex(ValueError, "No input files match"):
            make_jobs([source, str(self.root / "missing*.AVI")])
        self.assertFalse((self.root / "a_converted.mkv.log").exists())

    def test_default_names_beside_each_input(self):
        one, two = self.source("one/a.avi"), self.source("two/b.avi")
        jobs = make_jobs([one, two])
        self.assertEqual([job.output_path for job in jobs],
                         [one.with_name("a_converted.mkv"), two.with_name("b_converted.mkv")])

    def test_output_directory_suffix_and_format_combine(self):
        source = self.source("a.avi")
        output = self.root / "new/subdirectory"
        job = make_jobs([source], output_dir=output, output_suffix="_small", output_format="mp4")[0]
        self.assertTrue(output.is_dir())
        self.assertEqual(job.output_path, output / "a_small.mp4")
        self.assertEqual(job.report_path, output / "a_small.mp4.report.json")

    def test_same_stem_in_one_output_directory_fails_before_processing(self):
        one, two = self.source("one/a.avi"), self.source("two/a.avi")
        with self.assertRaisesRegex(ValueError, "collides"):
            make_jobs([one, two], output_dir=self.root / "outputs")
        self.assertEqual(one.read_bytes(), b"original")
        self.assertEqual(two.read_bytes(), b"original")

    def test_existing_sidecar_and_empty_suffix_cannot_replace_sources(self):
        source = self.source("a.mkv")
        with self.assertRaises(ValueError):
            make_jobs([source], output_suffix="")
        (self.root / "a_converted.mkv.log.jsonl").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            make_jobs([source])
        self.assertEqual((self.root / "a_converted.mkv.log.jsonl").read_text(), "keep")

    def test_explicit_output_is_single_input_and_unambiguous(self):
        one, two = self.source("a.avi"), self.source("b.avi")
        for options in ({"output_path": self.root / "out.mkv"},
                        {"report_path": self.root / "report.json"},
                        {"log_path": self.root / "log.txt"}):
            with self.assertRaises(ValueError):
                make_jobs([one, two], **options)
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            make_jobs([one], output_path=self.root / "out.mkv", output_suffix="_small")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            make_jobs([one], output_path=self.root / "out.mkv", output_format="mp4")

    def test_suffix_cannot_escape_output_directory(self):
        source = self.source("a.avi")
        for suffix in ("/other", "\\other", ":stream", "\x00", "*"):
            with self.assertRaisesRegex(ValueError, "suffix"):
                make_jobs([source], output_suffix=suffix)

    def test_cross_input_report_collision_is_rejected(self):
        one, two = self.source("a.avi"), self.source("a_converted.mkv.report.json")
        with self.assertRaises(ValueError):
            make_jobs([one, two])

    def plan(self):
        source = self.source("flight.avi")
        settings = make_jobs([source])[0]
        analysis = Analysis(source, {"streams": [{"codec_type": "video", "time_base": "1/10"}]},
                            frame_pts=(0, 1, 30, 31, 60, 61))
        return Plan(settings, analysis, {}, ((0., .2), (3., 3.2), (6., 6.2)),
                    ((.2, 3.), (3.2, 6.)), (), {}, "test")

    def test_split_preserves_short_useful_segments_and_resets_each_clock(self):
        plan = self.plan()
        parts = plan_outputs(plan, True)
        self.assertEqual([part.settings.output_path.name for part in parts],
                         ["flight_converted_1.mkv", "flight_converted_2.mkv", "flight_converted_3.mkv"])
        for part, interval in zip(parts, plan.keep_intervals):
            self.assertEqual(part.keep_intervals, (interval,))
            self.assertEqual(part.removed_intervals, plan.removed_intervals)
            self.assertEqual(part.mapping[0]["output_start"], 0.)
            self.assertAlmostEqual(part.mapping[0]["output_end"], .2)
        self.assertEqual(parts[-1].reasons["split_flights"]["index"], 3)

    def test_split_skips_empty_timestamp_intervals_and_numbers_contiguously(self):
        plan = replace(self.plan(), keep_intervals=((0., .2), (1., 1.1), (3., 3.2)))
        parts = plan_outputs(plan, True)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[1].settings.output_path.name, "flight_converted_2.mkv")
        self.assertEqual(parts[1].reasons["split_flights"]["empty_intervals_skipped"], 1)

    def test_split_off_is_identity_and_cut_off_conflicts(self):
        plan = self.plan()
        self.assertIs(plan_outputs(plan)[0], plan)
        with self.assertRaisesRegex(ValueError, "requires"):
            plan_outputs(replace(plan, settings=replace(plan.settings, cut_no_signal="off")), True)

    def test_part_collision_with_another_reserved_job_is_rejected(self):
        plan = self.plan()
        protected = self.root / "flight_converted_1.mkv"
        with self.assertRaisesRegex(ValueError, "collides"):
            validate_jobs([part.settings for part in plan_outputs(plan, True)], [protected])

    def test_manifest_publication_never_overwrites_and_cleans_temporary(self):
        path = self.root / "summary.json"
        write_manifest(path, {"status": "analyzed"})
        with self.assertRaises(FileExistsError):
            write_manifest(path, {"status": "different"})
        self.assertIn("analyzed", path.read_text())
        self.assertFalse(list(self.root.glob("*.partial")))


if __name__ == "__main__":
    unittest.main()
