"""Append-only clock ledger and publication record."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import T0, make_config

from gatecheck.ledger import append_clock, append_publication, scan_clock, tail
from gatecheck.model import Lock


def make_lock(**overrides) -> Lock:
    data = dict(
        v=1,
        platform="toutiao",
        title="标题",
        state="confirmed",
        owner="tester@host:1",
        claimed_at=T0.isoformat(),
        claim_ttl=7200,
        published_at=T0.isoformat(),
        confirmed_at=T0.isoformat(),
        list_row="标题 10-25 21:00 审核中",
    )
    data.update(overrides)
    return Lock(**data)


class LedgerBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.cfg = make_config(self.root)

    def tearDown(self):
        self._td.cleanup()


class TestClock(LedgerBase):
    def test_append_fields(self):
        path = append_clock(
            self.cfg,
            {
                "kind": "publication",
                "owner": "tester",
                "platform": "xhs",
                "slot": "night",
                "action": "confirm",
                "criteria": "列表行包含标题",
                "evidence": "标题 21:00",
                "missing": ["曝光", "收藏"],
            },
            now=T0,
        )
        self.assertEqual(path.name, "clock-2026-10-25.jsonl")
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["v"], 1)
        self.assertEqual(row["ts"], "2026-10-25T21:00:00+08:00")
        self.assertEqual(row["missing"], ["曝光", "收藏"])

    def test_one_line_per_call(self):
        for i in range(3):
            append_clock(self.cfg, {"owner": "t", "action": "a%d" % i}, now=T0)
        path = self.cfg.ledger_dir / "clock-2026-10-25.jsonl"
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 3)

    def test_tail_is_newest_first(self):
        for i in range(3):
            append_clock(self.cfg, {"owner": "t", "action": "a%d" % i}, now=T0)
        rows = tail(self.cfg, 2)
        self.assertEqual([obj["action"] for _, obj in rows], ["a2", "a1"])

    def test_tail_empty(self):
        self.assertEqual(tail(self.cfg, 5), [])

    def test_scan_reports_invalid_lines(self):
        path = self.cfg.ledger_dir / "clock-2026-10-25.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"ts": T0.isoformat(), "action": "ok"}, ensure_ascii=False)
        path.write_text(good + "\n{broken\n" + json.dumps({"action": "no-ts"}) + "\n", encoding="utf-8")
        entries, invalid = scan_clock(self.cfg, days=7)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(invalid), 2)

    def test_scan_skips_old_entries(self):
        path = self.cfg.ledger_dir / "clock-2026-10-01.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ts": "2026-10-01T10:00:00+08:00", "action": "old"}) + "\n",
            encoding="utf-8",
        )
        entries, invalid = scan_clock(self.cfg, days=7)
        self.assertEqual(entries, [])


class TestPublication(LedgerBase):
    def test_creates_dated_record(self):
        path, appended = append_publication(self.cfg, make_lock(), now=T0)
        self.assertTrue(appended)
        text = path.read_text(encoding="utf-8")
        self.assertIn("### toutiao｜标题｜21:00", text)
        self.assertIn("| 列表行 | 标题 10-25 21:00 审核中 |", text)

    def test_idempotent_heading(self):
        append_publication(self.cfg, make_lock(), now=T0)
        path, appended = append_publication(self.cfg, make_lock(), now=T0)
        self.assertFalse(appended)
        self.assertEqual(path.read_text(encoding="utf-8").count("### toutiao｜标题｜"), 1)

    def test_existing_content_is_untouched(self):
        target = self.cfg.published_dir / "2026-10-25.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "# 2026-10-25 发布记录\n\n| 平台 | 状态 |\n|---|---|\n| 小红书 | 审核中 |\n"
        target.write_text(original, encoding="utf-8")
        append_publication(self.cfg, make_lock(), now=T0)
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original), text[:80])
        self.assertIn("### toutiao｜标题｜21:00", text)

    def test_pipe_is_escaped(self):
        lock = make_lock(title="带|竖线的标题")
        path, _ = append_publication(self.cfg, lock, now=T0)
        self.assertIn("带\\|竖线的标题", path.read_text(encoding="utf-8"))

    def test_uses_now_for_date(self):
        later = T0 + timedelta(days=1)
        path, _ = append_publication(self.cfg, make_lock(), now=later)
        self.assertEqual(path.name, "2026-10-26.md")


if __name__ == "__main__":
    unittest.main()
