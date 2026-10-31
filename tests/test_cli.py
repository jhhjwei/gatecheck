"""CLI contract: exit codes, JSON shape, and the lock flow through argv."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import T0, write_draft

from gatecheck.cli import main

GOOD_SOURCE = [
    {"url": "https://example.com/reg", "fetched_at": "2026-10-25T21:38:00+08:00"}
]


class CliBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.config = self.root / "gatecheck.json"
        self.config.write_text(
            json.dumps(
                {
                    "state_dir": "state",
                    "ledger_dir": "ledger",
                    "published_dir": "published",
                    "corpus": {"roots": [], "window_days": 14, "max_docs": 20},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self._td.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def json_cli(self, argv):
        args = list(argv)
        if "--json" not in args:
            if args[:1] == ["--config"]:
                args = args[:2] + ["--json"] + args[2:]
            else:
                args = ["--json"] + args
        code, out, err = self.run_cli(args)
        return code, json.loads(out) if out.strip().startswith("{") else None, err

    def argv(self, *rest):
        return ["--config", str(self.config)] + list(rest)


class TestLint(CliBase):
    def test_pass_exit_zero(self):
        path = write_draft(
            self.root, "ok.txt",
            "先把结论放前面。\n\n空载 0.10W 乘 8760 小时等于 0.88 度。\n",
            {"title": "充电器插一年", "sources": GOOD_SOURCE},
        )
        code, out, _ = self.run_cli(self.argv("lint", str(path), "--platform", "toutiao"))
        self.assertEqual(code, 0)
        self.assertIn("PASS", out)

    def test_fail_exit_one(self):
        path = write_draft(self.root, "bad.txt", "老照片做成短片，不代登录。\n")
        code, out, _ = self.run_cli(self.argv("lint", str(path), "--platform", "xianyu"))
        self.assertEqual(code, 1)
        self.assertIn("G009", out)

    def test_json_shape(self):
        path = write_draft(self.root, "bad.txt", "全网最好的剪辑服务。\n")
        code, payload, _ = self.json_cli(
            self.argv("lint", str(path), "--platform", "xhs")
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["tool"], "gatecheck")
        self.assertEqual(payload["command"], "lint")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit"], 1)
        self.assertIn("counts", payload)
        first = payload["items"][0]
        for key in ("rule", "severity", "path", "line", "excerpt", "message", "fix", "data"):
            self.assertIn(key, first)
        self.assertEqual(first["data"]["platform"], "xhs")

    def test_json_flag_after_verb(self):
        path = write_draft(self.root, "a.txt", "全网最好的服务。\n")
        code, out, _ = self.run_cli(
            self.argv("lint", str(path), "--platform", "xhs", "--json")
        )
        self.assertEqual(code, 1)
        self.assertTrue(out.strip().startswith("{"), out)

    def test_unknown_platform_exit_two(self):
        path = write_draft(self.root, "a.txt", "正文。\n")
        code, payload, _ = self.json_cli(
            self.argv("lint", str(path), "--platform", "weibo")
        )
        self.assertEqual(code, 2)
        self.assertIn("未知平台", payload["error"])

    def test_missing_path_exit_two(self):
        code, _, err = self.run_cli(self.argv("lint", str(self.root / "nope.txt"), "--platform", "csdn"))
        self.assertEqual(code, 2)

    def test_directory_mode(self):
        sub = self.root / "drafts"
        sub.mkdir()
        write_draft(sub, "a.txt", "这是一段没有问题的正文，讲清楚了做法。\n", {"sources": GOOD_SOURCE})
        write_draft(sub, "b.txt", "第二篇，内容完全不同，讲封面与标题。\n", {"sources": GOOD_SOURCE})
        code, payload, _ = self.json_cli(self.argv("lint", str(sub), "--platform", "csdn"))
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["files"]), 2)

    def test_multiple_platforms(self):
        path = write_draft(self.root, "a.txt", "全网最好的服务。\n")
        code, payload, _ = self.json_cli(
            self.argv("lint", str(path), "--platform", "xhs,csdn")
        )
        self.assertEqual(code, 1)
        platforms = {item["data"]["platform"] for item in payload["items"]}
        self.assertEqual(platforms, {"xhs", "csdn"})


class TestLockCli(CliBase):
    def test_full_flow(self):
        base = self.argv("--now", T0.isoformat(), "claim", "--platform", "toutiao", "--title", "标题A")
        code, out, _ = self.run_cli(base)
        self.assertEqual(code, 0, out)

        code, out, _ = self.run_cli(
            self.argv("--now", (T0 + timedelta(seconds=10)).isoformat(),
                      "verify", "--platform", "toutiao", "--title", "标题A",
                      "--evidence", "列表无同题")
        )
        self.assertEqual(code, 0, out)

        code, out, _ = self.run_cli(
            self.argv("--now", (T0 + timedelta(seconds=20)).isoformat(),
                      "publish", "--platform", "toutiao", "--title", "标题A")
        )
        self.assertEqual(code, 0, out)

        code, out, _ = self.run_cli(
            self.argv("--now", (T0 + timedelta(seconds=90)).isoformat(),
                      "confirm", "--platform", "toutiao", "--title", "标题A",
                      "--list-row", "标题A 10-25 21:01 审核中")
        )
        self.assertEqual(code, 0, out)

        code, payload, _ = self.json_cli(
            self.argv("status", "--platform", "toutiao", "--title", "标题A")
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["lock"]["state"], "confirmed")

        record = self.root / "published" / "2026-10-25.md"
        self.assertTrue(record.is_file())
        self.assertIn("### toutiao｜标题A", record.read_text(encoding="utf-8"))

    def test_conflict_exit_three(self):
        self.run_cli(self.argv("--now", T0.isoformat(), "claim", "--platform", "toutiao", "--title", "标题B"))
        code, payload, _ = self.json_cli(
            self.argv("--now", (T0 + timedelta(minutes=1)).isoformat(),
                      "claim", "--platform", "toutiao", "--title", "标题B")
        )
        self.assertEqual(code, 3)
        self.assertFalse(payload["ok"])
        self.assertIn("占用", payload["error"])

    def test_publish_window_via_cli(self):
        self.run_cli(self.argv("--now", T0.isoformat(), "claim", "--platform", "toutiao", "--title", "标题C"))
        self.run_cli(self.argv("--now", T0.isoformat(), "verify", "--platform", "toutiao",
                               "--title", "标题C", "--evidence", "无同题"))
        code, payload, _ = self.json_cli(
            self.argv("--now", (T0 + timedelta(seconds=61)).isoformat(),
                      "publish", "--platform", "toutiao", "--title", "标题C")
        )
        self.assertEqual(code, 3)
        self.assertIn("过期", payload["error"])

    def test_status_unknown(self):
        code, payload, _ = self.json_cli(
            self.argv("status", "--platform", "toutiao", "--title", "没有")
        )
        self.assertEqual(code, 0)
        self.assertIsNone(payload["lock"])


class TestLedgerCli(CliBase):
    def test_add_and_tail(self):
        code, out, _ = self.run_cli(
            self.argv("ledger", "add", "--owner", "tester", "--action", "写稿",
                      "--criteria", "有实测数据", "--evidence", "0.88 度",
                      "--missing", "曝光,收藏")
        )
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli(self.argv("ledger", "tail", "--n", "5"))
        self.assertEqual(code, 0)
        self.assertIn("tester", out)
        self.assertIn("曝光,收藏", out)

    def test_add_refuses_now(self):
        code, _, _ = self.run_cli(
            self.argv("--now", T0.isoformat(), "ledger", "add", "--owner", "t",
                      "--action", "a", "--criteria", "c")
        )
        self.assertEqual(code, 2)

    def test_tail_empty(self):
        code, out, _ = self.run_cli(self.argv("ledger", "tail"))
        self.assertEqual(code, 0)
        self.assertIn("没有台账", out)


class TestMetricsCli(CliBase):
    def test_good_file(self):
        path = self.root / "m.json"
        path.write_text(json.dumps([{
            "id": "D1", "metric": "net_revenue", "target": 500,
            "baseline": "2026-10-24", "window_hours": 72,
            "source_field": "pnl.csv:net", "income_stream": "service",
            "check_after": "2026-10-28T12:00:00+08:00",
        }]), encoding="utf-8")
        code, out, _ = self.run_cli(self.argv("metrics", "check", str(path)))
        self.assertEqual(code, 0, out)

    def test_tautology_fails(self):
        path = self.root / "m.json"
        path.write_text(json.dumps([{
            "id": "D1", "metric": "net_revenue", "target": 500, "baseline": "累计",
            "window_hours": 72, "source_field": "pnl.csv:net",
        }]), encoding="utf-8")
        code, payload, _ = self.json_cli(self.argv("metrics", "check", str(path)))
        self.assertEqual(code, 1)
        self.assertGreaterEqual(payload["counts"]["fail"], 1)

    def test_missing_file_exit_two(self):
        code, _, _ = self.run_cli(self.argv("metrics", "check", str(self.root / "x.json")))
        self.assertEqual(code, 2)


class TestDoctorCli(CliBase):
    def test_empty_project_is_ok(self):
        code, out, _ = self.run_cli(self.argv("doctor"))
        self.assertEqual(code, 0, out)

    def test_stale_published_lock_fails(self):
        self.run_cli(self.argv("--now", T0.isoformat(), "claim", "--platform", "toutiao", "--title", "标题D"))
        self.run_cli(self.argv("--now", T0.isoformat(), "verify", "--platform", "toutiao",
                               "--title", "标题D", "--evidence", "无同题"))
        self.run_cli(self.argv("--now", (T0 + timedelta(seconds=10)).isoformat(),
                               "publish", "--platform", "toutiao", "--title", "标题D"))
        code, payload, _ = self.json_cli(
            self.argv("--now", (T0 + timedelta(minutes=20)).isoformat(), "doctor")
        )
        self.assertEqual(code, 1)
        names = [c["name"] for c in payload["checks"]]
        self.assertIn("已发布未确认", names)


class TestInit(CliBase):
    def test_init_writes_and_refuses_overwrite(self):
        cwd = os.getcwd()
        project = self.root / "project"
        project.mkdir()
        try:
            os.chdir(str(project))
            code, out, _ = self.run_cli(["--json", "init"])
            self.assertEqual(code, 0, out)
            self.assertTrue(Path("gatecheck.json").is_file())
            code, payload, _ = self.json_cli(["init"])
            self.assertEqual(code, 2)
            self.assertIn("已存在", payload["error"])
        finally:
            os.chdir(cwd)


class TestSelfTest(CliBase):
    def test_self_test_passes(self):
        code, out, _ = self.run_cli(["--self-test"])
        self.assertEqual(code, 0, out)
        self.assertIn("self-test PASS", out)


if __name__ == "__main__":
    unittest.main()
