"""G008: milestone criteria must be able to fail."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gatecheck.metrics import MetricsError, audit, load_milestones


def base(**overrides):
    item = {
        "id": "D45",
        "metric": "net_revenue",
        "target": 500,
        "baseline": "2026-10-24",
        "window_hours": 72,
        "source_field": "pnl.csv:net",
        "income_stream": "service",
        "check_after": "2026-10-28T12:00:00+08:00",
    }
    item.update(overrides)
    return item


def severities(items):
    return [(f.severity, f.message) for f in audit(items)]


class TestGoodCriteria(unittest.TestCase):
    def test_clean_milestone_has_no_findings(self):
        self.assertEqual(severities([base()]), [])

    def test_multiple_items_independent(self):
        self.assertEqual(severities([base(id="A"), base(id="B")]), [])


class TestTautology(unittest.TestCase):
    def test_cumulative_baseline_fails(self):
        found = severities([base(baseline="累计")])
        self.assertEqual(found[0][0], "fail")
        self.assertIn("恒真", found[0][1])

    def test_missing_baseline_fails(self):
        found = severities([base(baseline=None)])
        self.assertEqual(found[0][0], "fail")

    def test_cumulative_target_fails(self):
        found = severities([base(target="累计阅读 30000")])
        self.assertTrue(any(sev == "fail" and "累计" in msg for sev, msg in found))

    def test_cumulative_target_with_incremental_note_passes(self):
        found = severities([base(target="累计阅读 30000", notes="按切换后增量口径统计")])
        self.assertEqual(found, [])


class TestCompleteness(unittest.TestCase):
    def test_missing_target_fails(self):
        found = severities([base(target=None)])
        self.assertTrue(any("target" in msg for _, msg in found))

    def test_zero_window_fails(self):
        found = severities([base(window_hours=0)])
        self.assertTrue(any("window_hours" in msg for _, msg in found))

    def test_missing_source_fails(self):
        found = severities([base(source_field="")])
        self.assertTrue(any("source_field" in msg for _, msg in found))

    def test_missing_check_after_warns(self):
        found = severities([base(check_after=None)])
        self.assertEqual(found[0][0], "warn")

    def test_income_without_stream_warns(self):
        found = severities([base(income_stream="")])
        self.assertEqual(found[0][0], "warn")

    def test_non_income_metric_without_stream_is_fine(self):
        self.assertEqual(severities([base(metric="impressions", income_stream="")]), [])

    def test_views_metric_does_not_need_stream(self):
        self.assertEqual(severities([base(metric="views", target=30000, income_stream="")]), [])


class TestIncentiveConfusion(unittest.TestCase):
    def test_incentive_reward_counted_as_platform_income_fails(self):
        found = severities(
            [base(metric="platform_income", income_stream="platform", notes="含激励任务奖励")]
        )
        self.assertTrue(any(sev == "fail" and "激励" in msg for sev, msg in found))

    def test_incentive_in_service_stream_is_fine(self):
        self.assertEqual(
            severities([base(metric="platform_income", income_stream="service", notes="含活动奖励")]),
            [],
        )


class TestLoading(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_accepts_array(self):
        path = self.root / "m.json"
        path.write_text(json.dumps([base()], ensure_ascii=False), encoding="utf-8")
        self.assertEqual(len(load_milestones(path)), 1)

    def test_accepts_object_with_milestones(self):
        path = self.root / "m.json"
        path.write_text(json.dumps({"milestones": [base()]}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(len(load_milestones(path)), 1)

    def test_rejects_other_shapes(self):
        path = self.root / "m.json"
        path.write_text('{"foo": 1}', encoding="utf-8")
        with self.assertRaises(MetricsError):
            load_milestones(path)

    def test_missing_file(self):
        with self.assertRaises(MetricsError):
            load_milestones(self.root / "nope.json")

    def test_broken_json(self):
        path = self.root / "m.json"
        path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(MetricsError):
            load_milestones(path)


if __name__ == "__main__":
    unittest.main()
