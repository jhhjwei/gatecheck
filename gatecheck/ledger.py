"""Append-only clock ledger and publication record."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .model import Lock


def _clock_path(config: Config, now: datetime) -> Path:
    return config.ledger_dir / ("clock-%s.jsonl" % now.strftime("%Y-%m-%d"))


def append_clock(
    config: Config,
    record: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Path:
    now = now or datetime.now().astimezone()
    config.ledger_dir.mkdir(parents=True, exist_ok=True)
    path = _clock_path(config, now)

    payload = {
        "v": 1,
        "ts": now.isoformat(timespec="seconds"),
        "kind": record.get("kind", "note"),
        "owner": record.get("owner", ""),
        "platform": record.get("platform", ""),
        "slot": record.get("slot", ""),
        "action": record.get("action", ""),
        "criteria": record.get("criteria", ""),
        "evidence": record.get("evidence", ""),
        "missing": list(record.get("missing") or []),
    }
    line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return path


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def append_publication(
    config: Config,
    lock: Lock,
    now: Optional[datetime] = None,
) -> Tuple[Path, bool]:
    now = now or datetime.now().astimezone()
    config.published_dir.mkdir(parents=True, exist_ok=True)
    path = config.published_dir / ("%s.md" % now.strftime("%Y-%m-%d"))
    stamp = now.strftime("%Y-%m-%d %H:%M")

    block = "\n".join(
        [
            "",
            "### %s｜%s｜%s" % (lock.platform, lock.title, now.strftime("%H:%M")),
            "",
            "| 字段 | 内容 |",
            "|---|---|",
            "| 标题 | %s |" % _cell(lock.title),
            "| 平台 | %s |" % _cell(lock.platform),
            "| 提交时间 | %s |" % stamp,
            "| 列表行 | %s |" % _cell(lock.list_row or ""),
            "| 锁 owner | %s |" % _cell(lock.owner),
            "",
        ]
    )

    with open(str(path), "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = handle.read()
            marker = "### %s｜%s｜" % (lock.platform, lock.title)
            if marker in existing:
                return path, False
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return path, True


def tail(config: Config, n: int = 20) -> List[Tuple[Path, Optional[Dict[str, Any]]]]:
    files = sorted(config.ledger_dir.glob("clock-*.jsonl"))
    out: List[Tuple[Path, Optional[Dict[str, Any]]]] = []
    for path in reversed(files):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
            out.append((path, obj if isinstance(obj, dict) else None))
            if len(out) >= n:
                return out
    return out


def scan_clock(
    config: Config, days: int = 7
) -> Tuple[List[Dict[str, Any]], List[Tuple[Path, int]]]:
    """Return parsed entries and invalid line locations for the last N days."""
    entries: List[Dict[str, Any]] = []
    invalid: List[Tuple[Path, int]] = []
    if not config.ledger_dir.is_dir():
        return entries, invalid
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    for path in sorted(config.ledger_dir.glob("clock-*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                invalid.append((path, number))
                continue
            if not isinstance(obj, dict):
                invalid.append((path, number))
                continue
            ts = obj.get("ts", "")
            try:
                parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                invalid.append((path, number))
                continue
            if parsed >= cutoff:
                entries.append(obj)
    return entries, invalid
