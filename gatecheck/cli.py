"""Gatecheck command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import CONFIG_NAME, Config, ConfigError, load_config, write_default_config
from .doctor import exit_code as doctor_exit
from .doctor import run as doctor_run
from .draft import DraftError, build_corpus, iter_draft_files, load_draft
from .ledger import append_clock, tail as ledger_tail
from .locks import LockError, LockStore, default_owner, now_local, parse_iso
from .metrics import MetricsError, audit, load_milestones
from .model import Finding
from .rules import RuleContext, run_rules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gatecheck",
        description="发布前预检 + 五态发布锁 + 真实时钟台账（不登录、不代发）",
    )
    parser.add_argument("--config", help="配置文件路径；默认向上查找 gatecheck.json")
    parser.add_argument("--root", help="项目根目录；默认当前目录或配置文件所在目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--now", help=argparse.SUPPRESS)  # testing only
    parser.add_argument("--version", action="version", version="gatecheck " + __version__)
    parser.add_argument("--self-test", action="store_true", help="跑内置自测")

    sub = parser.add_subparsers(dest="command")

    def leaf(name: str, **kwargs) -> argparse.ArgumentParser:
        """Subcommand that also accepts --json/--now after the verb.

        argparse only knows the root-level flags before the subcommand; users
        type `gatecheck lint ... --json`, so mirror them here with SUPPRESS so
        an absent flag never overwrites the root value.
        """
        p = sub.add_parser(name, **kwargs)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="输出机器可读 JSON")
        p.add_argument("--now", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return p

    lint = leaf("lint", help="检查草稿（可传文件或目录）")
    lint.add_argument("paths", nargs="+", type=Path)
    lint.add_argument("--platform", required=True, help="逗号分隔，例如 toutiao,xhs")
    lint.add_argument("--meta", type=Path, help="显式指定 meta.json（仅单文件）")
    lint.add_argument("--fenced", action="store_true", help="只检查 ``` 代码块里的正文（运营笔记型草稿）")
    lint.add_argument("--no-dup", action="store_true", help="跳过跨稿相似度检查")

    claim = leaf("claim", help="占锁：声明即将发布的标题")
    claim.add_argument("--platform", required=True)
    claim.add_argument("--title", required=True)
    claim.add_argument("--owner", default="")
    claim.add_argument("--ttl", type=int, default=0)

    verify = leaf("verify", help="发前复查列表并记录证据")
    verify.add_argument("--platform", required=True)
    verify.add_argument("--title", required=True)
    verify.add_argument("--evidence", required=True)

    publish = leaf("publish", help="点发布时执行；强制 60 秒窗口")
    publish.add_argument("--platform", required=True)
    publish.add_argument("--title", required=True)

    confirm = leaf("confirm", help="列表出现同标题行后确认落地")
    confirm.add_argument("--platform", required=True)
    confirm.add_argument("--title", required=True)
    confirm.add_argument("--list-row", required=True)

    release = leaf("release", help="释放锁（必须写原因）")
    release.add_argument("--platform", required=True)
    release.add_argument("--title", required=True)
    release.add_argument("--reason", required=True)

    status = leaf("status", help="查看某个标题的锁状态")
    status.add_argument("--platform", required=True)
    status.add_argument("--title", required=True)

    ledger = leaf("ledger", help="真实时钟台账")
    ledger_sub = ledger.add_subparsers(dest="ledger_command")
    add = ledger_sub.add_parser("add", help="追加一条交接记录")
    add.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    add.add_argument("--owner", required=True)
    add.add_argument("--action", required=True)
    add.add_argument("--criteria", required=True)
    add.add_argument("--platform", default="")
    add.add_argument("--slot", default="")
    add.add_argument("--evidence", default="")
    add.add_argument("--missing", default="", help="逗号分隔的未采到清单")
    tail = ledger_sub.add_parser("tail", help="查看最近记录")
    tail.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    tail.add_argument("--n", type=int, default=20)

    metrics = leaf("metrics", help="判据审计")
    metrics_sub = metrics.add_subparsers(dest="metrics_command")
    check = metrics_sub.add_parser("check", help="检查 milestones JSON")
    check.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    check.add_argument("file", type=Path)

    leaf("doctor", help="体检：锁、台账、发布记录、判据")
    leaf("init", help="在当前目录生成 gatecheck.json")

    return parser


def _meta(command: str, ok: bool, code: int, now: datetime, **extra: Any) -> Dict[str, Any]:
    payload = {
        "tool": "gatecheck",
        "version": __version__,
        "command": command,
        "ok": ok,
        "exit": code,
        "now": now.isoformat(timespec="seconds"),
    }
    payload.update(extra)
    return payload


def _emit(args: argparse.Namespace, payload: Dict[str, Any], text_lines: List[str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in text_lines:
            print(line)


def _fail(args: argparse.Namespace, command: str, message: str, code: int, now: datetime) -> int:
    if getattr(args, "json", False):
        print(json.dumps(_meta(command, False, code, now, error=message), ensure_ascii=False, indent=2))
    else:
        print("错误：" + message, file=sys.stderr)
    return code


def _split_platforms(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def cmd_lint(args: argparse.Namespace, config: Config, now: datetime) -> int:
    platforms = _split_platforms(args.platform)
    if not platforms:
        return _fail(args, "lint", "--platform 不能为空", 2, now)
    for platform in platforms:
        try:
            config.platform(platform)
        except ConfigError as exc:
            return _fail(args, "lint", str(exc), 2, now)

    files: List[Path] = []
    try:
        for path in args.paths:
            files.extend(iter_draft_files(path))
    except DraftError as exc:
        return _fail(args, "lint", str(exc), 2, now)
    if not files:
        return _fail(args, "lint", "没有找到 .md/.txt 草稿", 2, now)

    unique: List[Path] = []
    seen = set()
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    files = unique

    if args.meta and len(files) > 1:
        return _fail(args, "lint", "--meta 只能配合单个草稿使用", 2, now)

    corpus = [] if args.no_dup else build_corpus(config, exclude=files)
    findings: List[Finding] = []
    try:
        for path in files:
            for platform in platforms:
                draft = load_draft(
                    path,
                    platform,
                    str(args.meta) if args.meta else None,
                    fenced=args.fenced,
                )
                for finding in run_rules(RuleContext(draft, config, corpus, now)):
                    finding.data.setdefault("platform", platform)
                    findings.append(finding)
    except DraftError as exc:
        return _fail(args, "lint", str(exc), 2, now)

    counts = {"fail": 0, "warn": 0, "info": 0, "skip": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    ok = counts["fail"] == 0
    code = 0 if ok else 1

    lines: List[str] = []
    for finding in findings:
        platform = finding.data.get("platform", "")
        icon = {"fail": "✗", "warn": "⚠", "info": "ℹ", "skip": "-"}.get(finding.severity, "·")
        location = finding.path
        if finding.line:
            location += ":%d" % finding.line
        excerpt = "「%s」" % finding.excerpt if finding.excerpt else ""
        lines.append(
            "%s %s %s [%s] %s — %s" % (icon, finding.rule, location, platform, excerpt, finding.message)
        )
        if finding.fix:
            lines.append("    fix: " + finding.fix)
    if not findings:
        lines.append("✓ 没有发现问题")
    lines.append(
        "结论：%s（%d fail / %d warn / %d info）"
        % ("PASS" if ok else "FAIL", counts["fail"], counts["warn"], counts["info"])
    )

    payload = _meta(
        "lint",
        ok,
        code,
        now,
        counts=counts,
        files=[str(p) for p in files],
        items=[f.to_dict() for f in findings],
    )
    _emit(args, payload, lines)
    return code


def _lock_store(config: Config) -> LockStore:
    return LockStore(config)


def cmd_claim(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock = store.claim(
        args.platform,
        args.title,
        owner=args.owner or default_owner(),
        ttl=args.ttl or None,
        now=now,
    )
    lines = [
        "✓ 已占锁：%s / %s" % (lock.platform, lock.title),
        "  owner=%s  claimed_at=%s  TTL=%ds" % (lock.owner, lock.claimed_at, lock.claim_ttl),
        "  下一步：发布前跑 verify --evidence \"列表里没有同标题行\"",
    ]
    payload = _meta("claim", True, 0, now, lock=lock.to_dict())
    _emit(args, payload, lines)
    return 0


def cmd_verify(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock = store.verify(args.platform, args.title, args.evidence, now=now)
    window = int(config.lock_config.get("verify_to_publish", 60))
    lines = [
        "✓ 发前复查已记录（%s）" % lock.verified_at,
        "  现在 %d 秒内点发布；超时重跑 verify" % window,
    ]
    _emit(args, _meta("verify", True, 0, now, lock=lock.to_dict()), lines)
    return 0


def cmd_publish(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock = store.publish(args.platform, args.title, now=now)
    lines = [
        "✓ 已记录发布落点（%s）" % lock.published_at,
        "  等列表出现同标题行后：confirm --list-row \"标题 时间 状态\"",
    ]
    _emit(args, _meta("publish", True, 0, now, lock=lock.to_dict()), lines)
    return 0


def cmd_confirm(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock, path, appended = store.confirm(
        args.platform, args.title, args.list_row, now=now
    )
    lines = [
        "✓ 已确认落地：%s" % lock.list_row,
        "  记录：%s%s" % (path, "" if appended else "（已存在，未重复追加）"),
    ]
    payload = _meta(
        "confirm", True, 0, now, lock=lock.to_dict(), published_path=str(path), appended=appended
    )
    _emit(args, payload, lines)
    return 0


def cmd_release(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock = store.release(args.platform, args.title, args.reason, now=now)
    lines = [
        "✓ 已释放：%s / %s" % (lock.platform, lock.title),
        "  reason=%s" % lock.release_reason,
    ]
    _emit(args, _meta("release", True, 0, now, lock=lock.to_dict()), lines)
    return 0


def cmd_status(args: argparse.Namespace, config: Config, now: datetime) -> int:
    store = _lock_store(config)
    lock = store.status(args.platform, args.title)
    if lock is None:
        lines = ["没有锁记录：%s / %s" % (args.platform, args.title)]
        payload = _meta("status", True, 0, now, lock=None)
    else:
        data = lock.to_dict()
        lines = ["%s / %s → %s" % (lock.platform, lock.title, lock.state)]
        for key in ("owner", "claimed_at", "verified_at", "published_at", "confirmed_at", "list_row", "evidence", "release_reason"):
            value = data.get(key)
            if value:
                lines.append("  %s=%s" % (key, value))
        payload = _meta("status", True, 0, now, lock=data)
    _emit(args, payload, lines)
    return 0


def cmd_ledger(args: argparse.Namespace, config: Config, now: datetime) -> int:
    if args.ledger_command == "add":
        if args.now:
            return _fail(args, "ledger", "台账使用本机真实时钟，不接受 --now", 2, now)
        missing = [item.strip() for item in args.missing.split(",") if item.strip()]
        path = append_clock(
            config,
            {
                "kind": "note",
                "owner": args.owner,
                "platform": args.platform,
                "slot": args.slot,
                "action": args.action,
                "criteria": args.criteria,
                "evidence": args.evidence,
                "missing": missing,
            },
        )
        lines = ["✓ 已写入 %s" % path, "  ts=%s owner=%s" % (now.isoformat(timespec="seconds"), args.owner)]
        _emit(args, _meta("ledger", True, 0, now, path=str(path)), lines)
        return 0

    if args.ledger_command == "tail":
        rows = ledger_tail(config, args.n)
        lines = []
        for path, obj in rows:
            if obj is None:
                lines.append("! 损坏行 %s" % path)
                continue
            lines.append(
                "%s | %s | %s | %s | %s | %s"
                % (
                    obj.get("ts", ""),
                    obj.get("owner", ""),
                    obj.get("action", ""),
                    obj.get("criteria", ""),
                    obj.get("evidence", ""),
                    ",".join(obj.get("missing") or []),
                )
            )
        if not rows:
            lines.append("（没有台账记录）")
        _emit(args, _meta("ledger", True, 0, now, rows=[obj for _, obj in rows]), lines)
        return 0

    return _fail(args, "ledger", "用法：gatecheck ledger add|tail", 2, now)


def cmd_metrics(args: argparse.Namespace, config: Config, now: datetime) -> int:
    if args.metrics_command != "check":
        return _fail(args, "metrics", "用法：gatecheck metrics check FILE", 2, now)
    try:
        milestones = load_milestones(args.file)
    except MetricsError as exc:
        return _fail(args, "metrics", str(exc), 2, now)
    findings = audit(milestones, path=str(args.file))
    fail = sum(1 for f in findings if f.severity == "fail")
    warn = sum(1 for f in findings if f.severity == "warn")
    ok = fail == 0
    lines = []
    for finding in findings:
        icon = "✗" if finding.severity == "fail" else "⚠"
        lines.append("%s %s — %s" % (icon, finding.rule, finding.message))
        if finding.fix:
            lines.append("    fix: " + finding.fix)
    if not findings:
        lines.append("✓ 判据可鉴别：都有基线、窗口与数据源")
    lines.append("结论：%s（%d fail / %d warn）" % ("PASS" if ok else "FAIL", fail, warn))
    payload = _meta(
        "metrics",
        ok,
        0 if ok else 1,
        now,
        counts={"fail": fail, "warn": warn},
        items=[f.to_dict() for f in findings],
    )
    _emit(args, payload, lines)
    return 0 if ok else 1


def cmd_doctor(args: argparse.Namespace, config: Config, now: datetime) -> int:
    checks = doctor_run(config, now)
    code = doctor_exit(checks)
    lines = []
    for check in checks:
        lines.append("[%s] %s — %s" % (check.level, check.name, check.detail))
    if not checks:
        lines.append("[ok] 没有发现问题")
    lines.append("结论：%s（%d 项）" % ("PASS" if code == 0 else "FAIL", len(checks)))
    payload = _meta(
        "doctor",
        code == 0,
        code,
        now,
        checks=[{"level": c.level, "name": c.name, "detail": c.detail} for c in checks],
    )
    _emit(args, payload, lines)
    return code


def cmd_init(args: argparse.Namespace, config: Config, now: datetime) -> int:
    target = Path.cwd() / CONFIG_NAME
    try:
        write_default_config(target)
    except ConfigError as exc:
        return _fail(args, "init", str(exc), 2, now)
    lines = ["✓ 已生成 %s" % target, "  按项目改 state_dir / ledger_dir / published_dir 与 platforms"]
    _emit(args, _meta("init", True, 0, now, path=str(target)), lines)
    return 0


HANDLERS = {
    "lint": cmd_lint,
    "claim": cmd_claim,
    "verify": cmd_verify,
    "publish": cmd_publish,
    "confirm": cmd_confirm,
    "release": cmd_release,
    "status": cmd_status,
    "ledger": cmd_ledger,
    "metrics": cmd_metrics,
    "doctor": cmd_doctor,
    "init": cmd_init,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        from .selftest import run as run_selftest

        return run_selftest()

    if not args.command:
        parser.print_help()
        return 2

    try:
        config = load_config(args.config, args.root)
    except ConfigError as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 2

    now = parse_iso(args.now) if args.now else now_local()
    if now is None:
        print("错误：--now 不是合法 ISO 时间", file=sys.stderr)
        return 2

    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        return 2

    try:
        return handler(args, config, now)
    except LockError as exc:
        extra = "；上次证据：%s" % (exc.lock.evidence or exc.lock.list_row) if exc.lock else ""
        return _fail(args, args.command, str(exc) + extra, exc.code, now)
    except ConfigError as exc:
        return _fail(args, args.command, str(exc), 2, now)
    except (DraftError, MetricsError, OSError) as exc:
        return _fail(args, args.command, str(exc), 2, now)
