"""Five-state publish lock: ordering, windows, atomicity, dedupe."""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import T0, make_config

from gatecheck.locks import LockError, LockStore, default_owner, parse_iso

TITLE = "测试标题"


class LockBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.cfg = make_config(self.root)
        self.store = LockStore(self.cfg)

    def tearDown(self):
        self._td.cleanup()

    def at(self, seconds=0, hours=0):
        return T0 + timedelta(seconds=seconds, hours=hours)

    def to_published(self, title=TITLE, t0=None):
        t0 = t0 or T0
        self.store.claim("toutiao", title, now=t0)
        self.store.verify("toutiao", title, "列表没有同题", now=t0 + timedelta(seconds=5))
        return self.store.publish("toutiao", title, now=t0 + timedelta(seconds=15))


class TestHappyPath(LockBase):
    def test_five_states(self):
        lock = self.store.claim("toutiao", TITLE, now=T0)
        self.assertEqual(lock.state, "claimed")
        self.assertEqual(lock.claim_ttl, 7200)
        self.assertIn("@", lock.owner)

        lock = self.store.verify("toutiao", TITLE, "列表里没有同标题行", now=self.at(30))
        self.assertEqual(lock.state, "verified")
        self.assertEqual(lock.evidence, "列表里没有同标题行")

        lock = self.store.publish("toutiao", TITLE, now=self.at(60))
        self.assertEqual(lock.state, "published")

        lock, path, appended = self.store.confirm(
            "toutiao", TITLE, TITLE + " 10-25 21:01 审核中", now=self.at(120)
        )
        self.assertEqual(lock.state, "confirmed")
        self.assertTrue(appended)
        self.assertTrue(path.name.endswith("2026-10-25.md"))
        self.assertIn("### toutiao｜" + TITLE, path.read_text(encoding="utf-8"))

    def test_status_reads_back(self):
        self.to_published()
        lock = self.store.status("toutiao", TITLE)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.state, "published")

    def test_returns_none_for_unknown(self):
        self.assertIsNone(self.store.status("toutiao", "没有这个"))


class TestWindow(LockBase):
    def test_publish_requires_verify(self):
        self.store.claim("toutiao", TITLE, now=T0)
        with self.assertRaises(LockError) as ctx:
            self.store.publish("toutiao", TITLE, now=self.at(1))
        self.assertEqual(ctx.exception.code, 3)

    def test_publish_within_60s(self):
        self.store.claim("toutiao", TITLE, now=T0)
        self.store.verify("toutiao", TITLE, "无同题", now=T0)
        self.store.publish("toutiao", TITLE, now=self.at(59))

    def test_publish_at_60s_is_boundary(self):
        self.store.claim("toutiao", TITLE, now=T0)
        self.store.verify("toutiao", TITLE, "无同题", now=T0)
        self.store.publish("toutiao", TITLE, now=self.at(60))

    def test_publish_after_61s_rejected(self):
        self.store.claim("toutiao", TITLE, now=T0)
        self.store.verify("toutiao", TITLE, "无同题", now=T0)
        with self.assertRaises(LockError) as ctx:
            self.store.publish("toutiao", TITLE, now=self.at(61))
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(self.store.status("toutiao", TITLE).state, "verified")

    def test_confirm_after_15min_rejected(self):
        self.to_published()
        with self.assertRaises(LockError) as ctx:
            self.store.confirm("toutiao", TITLE, TITLE + " 行", now=self.at(15 + 20 * 60))
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(self.store.status("toutiao", TITLE).state, "published")

    def test_confirm_without_title_in_row_rejected(self):
        self.to_published()
        with self.assertRaises(LockError) as ctx:
            self.store.confirm("toutiao", TITLE, "完全不相干的列表行", now=self.at(120))
        self.assertEqual(ctx.exception.code, 3)

    def test_confirm_accepts_punctuation_difference(self):
        self.to_published(title="充电器插一年电费不到一块")
        lock, _, _ = self.store.confirm(
            "toutiao", "充电器插一年电费不到一块", "充电器插一年 电费 不到一块 10-25 21:01 审核中", now=self.at(120)
        )
        self.assertEqual(lock.state, "confirmed")


class TestClaimConflicts(LockBase):
    def test_second_claim_rejected(self):
        self.store.claim("toutiao", TITLE, now=T0)
        with self.assertRaises(LockError) as ctx:
            self.store.claim("toutiao", TITLE, now=self.at(30))
        self.assertEqual(ctx.exception.code, 3)
        self.assertIsNotNone(ctx.exception.lock)

    def test_claim_after_ttl_expiry_allowed(self):
        self.store.claim("toutiao", TITLE, now=T0, ttl=600)
        lock = self.store.claim("toutiao", TITLE, now=self.at(601))
        self.assertEqual(lock.state, "claimed")

    def test_claim_within_24h_of_publish_rejected(self):
        self.to_published()
        with self.assertRaises(LockError) as ctx:
            self.store.claim("toutiao", TITLE, now=self.at(hours=2))
        self.assertEqual(ctx.exception.code, 3)
        self.assertIn("24", str(ctx.exception))

    def test_claim_after_24h_allowed(self):
        self.to_published()
        lock = self.store.claim("toutiao", TITLE, now=self.at(hours=25))
        self.assertEqual(lock.state, "claimed")

    def test_other_platform_same_title_allowed(self):
        self.store.claim("toutiao", TITLE, now=T0)
        lock = self.store.claim("xhs", TITLE, now=self.at(30))
        self.assertEqual(lock.state, "claimed")

    def test_empty_title_rejected(self):
        with self.assertRaises(LockError) as ctx:
            self.store.claim("toutiao", "   ", now=T0)
        self.assertEqual(ctx.exception.code, 2)


class TestRelease(LockBase):
    def test_release_requires_reason(self):
        self.store.claim("toutiao", TITLE, now=T0)
        with self.assertRaises(LockError) as ctx:
            self.store.release("toutiao", TITLE, "  ", now=self.at(10))
        self.assertEqual(ctx.exception.code, 2)

    def test_release_then_reclaim(self):
        self.store.claim("toutiao", TITLE, now=T0)
        lock = self.store.release("toutiao", TITLE, "换题", now=self.at(10))
        self.assertEqual(lock.state, "released")
        again = self.store.claim("toutiao", TITLE, now=self.at(20))
        self.assertEqual(again.state, "claimed")

    def test_release_after_publish_still_dedupes(self):
        self.to_published()
        lock = self.store.release("toutiao", TITLE, "已提交待确认", now=self.at(300))
        self.assertEqual(lock.state, "released")
        self.assertIsNotNone(lock.published_at)
        with self.assertRaises(LockError):
            self.store.claim("toutiao", TITLE, now=self.at(hours=2))

    def test_confirmed_cannot_be_released(self):
        lock = self.to_published()
        self.store.confirm("toutiao", TITLE, TITLE + " 行", now=self.at(120))
        with self.assertRaises(LockError) as ctx:
            self.store.release("toutiao", TITLE, "想撤回", now=self.at(200))
        self.assertEqual(ctx.exception.code, 3)


class TestAtomicity(LockBase):
    def test_held_guard_blocks_claim(self):
        key = self.store.key("toutiao", TITLE)
        guard = self.store.dir / (key + ".guard")
        fd = os.open(str(guard), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(LockError) as ctx:
                self.store.claim("toutiao", TITLE, now=T0)
            self.assertEqual(ctx.exception.code, 3)
            self.assertIn("另一个进程", str(ctx.exception))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_concurrent_claims_only_one_wins(self):
        results = []
        barrier = threading.Barrier(6)

        def attempt(index):
            barrier.wait()
            try:
                self.store.claim("toutiao", TITLE, owner="t%d" % index, now=T0)
                results.append("ok")
            except LockError:
                results.append("blocked")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(len(self.store.list_locks()), 1)

    def test_parallel_process_claims(self):
        """Two real processes, same title: one succeeds, one exits 3."""
        import subprocess
        import sys

        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        cmd = [
            sys.executable, "-m", "gatecheck",
            "--config", str(self.cfg.path or (self.root / "gatecheck.json")),
            "--now", T0.isoformat(),
            "claim", "--platform", "toutiao", "--title", TITLE,
        ]
        script = self.root / "gatecheck.json"
        script.write_text(
            '{"state_dir": "%s", "ledger_dir": "%s", "published_dir": "%s"}'
            % (self.root / ".gc", self.root / "ledger", self.root / "published"),
            encoding="utf-8",
        )
        procs = [subprocess.Popen(cmd + ["--owner", "p%d" % i], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for i in range(2)]
        codes = [p.wait() for p in procs]
        for proc in procs:
            proc.communicate()
        self.assertEqual(sorted(codes), [0, 3], codes)


class TestTimeParsing(unittest.TestCase):
    def test_z_suffix(self):
        parsed = parse_iso("2026-10-25T21:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_invalid(self):
        self.assertIsNone(parse_iso("昨天"))

    def test_none(self):
        self.assertIsNone(parse_iso(None))


class TestOwner(unittest.TestCase):
    def test_default_owner_shape(self):
        owner = default_owner()
        self.assertIn("@", owner)
        self.assertIn(":", owner)


if __name__ == "__main__":
    unittest.main()
