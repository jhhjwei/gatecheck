"""Fast smoke test for `gatecheck --self-test`.

This is deliberately not the full unit suite: it runs a handful of end-to-end
checks in a throwaway directory so a user can verify an install in one second.
"""

from __future__ import annotations

import copy
import json
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Tuple

from .config import DEFAULTS, Config
from .draft import load_draft
from .ledger import append_clock, tail
from .locks import LockError, LockStore
from .metrics import audit, load_milestones
from .rules import RuleContext, run_rules

T0 = datetime(2026, 10, 25, 21, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def _config(root: Path) -> Config:
    return Config(copy.deepcopy(DEFAULTS), root)


def _write_draft(root: Path, name: str, text: str, meta: dict = None) -> Path:
    body = root / name
    body.write_text(text, encoding="utf-8")
    if meta is not None:
        body.with_name(body.stem + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    return body


def _severities(path: Path, platform: str, config: Config, corpus=None) -> List[str]:
    draft = load_draft(path, platform)
    findings = run_rules(RuleContext(draft, config, corpus or [], T0))
    return [f.severity for f in findings]


CHECKS: List[Tuple[str, Callable[[], None]]] = []


def check(name: str):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn

    return decorator


@check("干净草稿可以通过 lint")
def _clean_draft():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        path = _write_draft(
            root,
            "clean.txt",
            "先把结论放前面。\n\n空载 0.10W 乘 8760 小时，是 0.88 度。",
            {"title": "充电器插一年电费多少", "sources": [
                {"url": "https://example.com/reg", "fetched_at": "2026-10-25T21:00:00+08:00"}
            ]},
        )
        sevs = _severities(path, "toutiao", cfg)
        assert "fail" not in sevs, sevs


@check("否定句里的禁词仍然判 fail")
def _negated_banned_word():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        path = _write_draft(
            root,
            "bad.txt",
            "老照片做成短片，不代登录、不代发布，也不用换脸。\n",
            {"title": "老照片短片", "sources": [
                {"url": "https://example.com/x", "fetched_at": "2026-10-25T21:00:00+08:00"}
            ]},
        )
        sevs = _severities(path, "xianyu", cfg)
        assert sevs.count("fail") >= 2, sevs


@check("内部序号当对外数量会被拦下")
def _number_provenance():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        path = _write_draft(
            root,
            "series.txt",
            "这个系列已经写到第九篇了，合集共九篇。\n",
            {"title": "系列更新", "sources": [
                {"url": "https://example.com/x", "fetched_at": "2026-10-25T21:00:00+08:00"}
            ]},
        )
        sevs = _severities(path, "csdn", cfg)
        assert "fail" in sevs, sevs


@check("五态锁：claim → verify → publish → confirm")
def _lock_flow():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        store = LockStore(cfg)
        title = "测试标题不重名"
        lock = store.claim("toutiao", title, now=T0)
        assert lock.state == "claimed"

        lock = store.verify("toutiao", title, "列表里没有同标题行", now=T0 + timedelta(seconds=10))
        assert lock.state == "verified"

        lock = store.publish("toutiao", title, now=T0 + timedelta(seconds=40))
        assert lock.state == "published"

        lock, record, appended = store.confirm(
            "toutiao", title, title + " 10-25 21:01 审核中", now=T0 + timedelta(seconds=70)
        )
        assert lock.state == "confirmed"
        assert appended is True, appended
        text = Path(record).read_text(encoding="utf-8")
        assert "### toutiao｜" + title in text, text

        clock = cfg.ledger_dir / "clock-2026-10-25.jsonl"
        assert clock.is_file(), list(cfg.ledger_dir.iterdir())
        assert any(obj and obj.get("kind") == "publication" for _, obj in tail(cfg, 5))


@check("超过 60 秒才 publish 会被拒")
def _late_publish():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        store = LockStore(cfg)
        title = "超时标题"
        store.claim("toutiao", title, now=T0)
        store.verify("toutiao", title, "列表无同题", now=T0)
        try:
            store.publish("toutiao", title, now=T0 + timedelta(seconds=61))
        except LockError as exc:
            assert exc.code == 3, exc.code
            return
        raise AssertionError("61 秒后 publish 竟然通过了")


@check("24 小时内同标题不能再 claim")
def _dedupe():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        store = LockStore(cfg)
        title = "刚发过的标题"
        store.claim("xhs", title, now=T0)
        store.verify("xhs", title, "无同题", now=T0)
        store.publish("xhs", title, now=T0 + timedelta(seconds=5))
        store.confirm("xhs", title, title + " 10-25 21:00 审核中", now=T0 + timedelta(seconds=20))
        try:
            store.claim("xhs", title, now=T0 + timedelta(hours=1))
        except LockError as exc:
            assert exc.code == 3, exc.code
            return
        raise AssertionError("去重窗口内又 claim 成功了")


@check("台账追加与回读")
def _ledger():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _config(root)
        append_clock(cfg, {"owner": "self-test", "action": "写稿", "criteria": "有数", "evidence": "0.88 度"}, now=T0)
        rows = tail(cfg, 3)
        assert len(rows) == 1 and rows[0][1]["owner"] == "self-test", rows


@check("G008 能识别恒真判据")
def _metrics():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bad = [
            {"id": "D1", "metric": "net_revenue", "target": 500,
             "baseline": "累计", "window_hours": 72, "source_field": "pnl.csv:net",
             "income_stream": "service"},
            {"id": "D2", "metric": "net_revenue", "target": 500,
             "baseline": "2026-10-24", "window_hours": 72,
             "source_field": "pnl.csv:net", "income_stream": "service",
             "check_after": "2026-10-28T12:00:00+08:00"},
        ]
        path = root / "milestones.json"
        path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        findings = audit(load_milestones(path), str(path))
        assert any(f.severity == "fail" for f in findings), findings
        assert not any(f.severity == "fail" for f in findings if "D2" in f.message), findings


def run() -> int:
    failures = 0
    for name, fn in CHECKS:
        try:
            fn()
        except Exception:
            failures += 1
            print("FAIL  " + name)
            for line in traceback.format_exc().strip().splitlines()[-3:]:
                print("      " + line)
        else:
            print("ok    " + name)
    print("\nself-test %s (%d checks)" % ("PASS" if failures == 0 else "FAILED", len(CHECKS)))
    return 0 if failures == 0 else 1
