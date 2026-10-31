"""Operational health checks for locks, ledger, records and milestones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .config import Config
from .ledger import scan_clock
from .locks import LockStore, _seconds, parse_iso
from .metrics import audit


@dataclass
class Check:
    level: str
    name: str
    detail: str


def _fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "时间未知"
    seconds = max(0, seconds)
    if seconds < 3600:
        return "%.0f 分钟" % (seconds / 60)
    if seconds < 86400:
        return "%.1f 小时" % (seconds / 3600)
    return "%.1f 天" % (seconds / 86400)


def run(config: Config, now: Optional[datetime] = None) -> List[Check]:
    now = now or datetime.now().astimezone()
    checks: List[Check] = []
    cfg = config.lock_config
    claim_ttl = int(cfg.get("claim_ttl", 7200))
    verify_window = int(cfg.get("verify_to_publish", 60))
    confirm_window = int(cfg.get("publish_to_confirm", 900))

    # ---- locks
    if not config.state_dir.is_dir():
        checks.append(Check("info", "锁目录", "还没有使用过发布锁"))
    else:
        store = LockStore(config)
        locks = store.list_locks()
        if not locks:
            checks.append(Check("info", "发布锁", "没有锁记录"))
        for path, lock in locks:
            label = "%s / %s" % (lock.platform, lock.title)
            state = lock.state
            if state in ("claimed", "verified"):
                claimed_age = _seconds(parse_iso(lock.claimed_at), now)
                if claimed_age is not None and claimed_age > claim_ttl:
                    checks.append(
                        Check(
                            "warn",
                            "过期占用",
                            "%s（%s，已占 %s）" % (label, state, _fmt_age(claimed_age)),
                        )
                    )
                elif state == "verified":
                    age = _seconds(parse_iso(lock.verified_at), now)
                    if age is not None and age > verify_window:
                        checks.append(
                            Check(
                                "warn",
                                "发前复查过期",
                                "%s（%s，窗口 %d 秒）" % (label, _fmt_age(age), verify_window),
                            )
                        )
            elif state == "published":
                age = _seconds(parse_iso(lock.published_at), now)
                if age is not None and age > confirm_window:
                    checks.append(
                        Check(
                            "fail",
                            "已发布未确认",
                            "%s（已过 %s，可能已上线；不要重发，先查列表）"
                            % (label, _fmt_age(age)),
                        )
                    )
                else:
                    checks.append(
                        Check("info", "待确认", "%s（发布 %s）" % (label, _fmt_age(age)))
                    )
            elif state == "confirmed":
                checks.append(Check("info", "已确认", label))
            elif state == "released":
                checks.append(Check("info", "已释放", label))
            else:
                checks.append(Check("fail", "未知锁状态", "%s：%r" % (label, state)))

    # ---- ledger
    if not config.ledger_dir.is_dir():
        checks.append(Check("warn", "台账目录", "不存在：%s" % config.ledger_dir))
    else:
        entries, invalid = scan_clock(config, days=7)
        for path, number in invalid[:3]:
            checks.append(Check("fail", "台账损坏", "%s 第 %d 行不是合法 JSON" % (path, number)))
        stale = 0
        for entry in entries:
            missing = entry.get("missing") or []
            if not missing:
                continue
            try:
                ts = datetime.fromisoformat(str(entry.get("ts", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if now - ts > timedelta(hours=24):
                stale += 1
        if stale:
            checks.append(Check("warn", "未采到清单过期", "%d 条超过 24h 仍未补齐" % stale))
        today_file = config.ledger_dir / ("clock-%s.jsonl" % now.strftime("%Y-%m-%d"))
        checks.append(
            Check(
                "info" if today_file.is_file() else "warn",
                "今日台账",
                "已写入" if today_file.is_file() else "今天还没有台账记录",
            )
        )

    # ---- publication records
    if not config.published_dir.is_dir():
        checks.append(Check("warn", "发布记录目录", "不存在：%s" % config.published_dir))
    else:
        today_record = config.published_dir / ("%s.md" % now.strftime("%Y-%m-%d"))
        checks.append(
            Check(
                "ok" if today_record.is_file() else "info",
                "今日发布记录",
                str(today_record) if today_record.is_file() else "今天还没有发布记录",
            )
        )

    # ---- corpus
    roots = config.corpus.get("roots", [])
    missing_roots = []
    for root in roots:
        path = Path(str(root))
        if not path.is_absolute():
            path = config.root / path
        if not path.exists():
            missing_roots.append(str(root))
    if missing_roots:
        checks.append(Check("warn", "语料目录", "不存在：%s" % ", ".join(missing_roots)))

    # ---- milestones
    milestones = config.get("milestones") or []
    if milestones:
        for finding in audit(milestones, path=str(config.path or "")):
            checks.append(Check(finding.severity, "G008 判据", finding.message))

    return checks


def exit_code(checks: List[Check]) -> int:
    return 1 if any(c.level == "fail" for c in checks) else 0
