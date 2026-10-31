"""Five-state publish lock.

claimed -> verified -> published -> confirmed
Any non-confirmed state can be released, but a published lock keeps its
published_at so a second claim inside the dedupe window is still rejected.
"""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
import socket
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config
from .draft import nfc, normalize
from .model import Lock


class LockError(Exception):
    def __init__(self, message: str, code: int = 3, lock: Optional[Lock] = None):
        super().__init__(message)
        self.code = code
        self.lock = lock


def now_local() -> datetime:
    return datetime.now().astimezone()


def to_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def default_owner() -> str:
    return "%s@%s:%d" % (getpass.getuser(), socket.gethostname(), os.getpid())


def _seconds(start: Optional[datetime], end: datetime) -> Optional[float]:
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.astimezone()
    return (end - start).total_seconds()


class LockStore:
    def __init__(self, config: Config):
        self.config = config
        self.dir = config.state_dir / "locks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = config.lock_config

    def key(self, platform: str, title: str) -> str:
        raw = "%s\n%s" % (platform, nfc(title.strip()))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def path(self, key: str) -> Path:
        return self.dir / (key + ".json")

    def _guard(self, key: str):
        guard = self.dir / (key + ".guard")
        fd = os.open(str(guard), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LockError("另一个进程正在操作同一发布锁，稍后重试", 3)
        return fd

    def _read(self, key: str) -> Optional[Lock]:
        path = self.path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LockError("锁文件损坏：%s（%s）" % (path, exc), 2)
        if not isinstance(data, dict):
            raise LockError("锁文件格式错误：%s" % path, 2)
        return Lock.from_dict(data)

    def _write(self, key: str, lock: Lock) -> None:
        target = self.path(key)
        fd, tmp_name = tempfile.mkstemp(prefix=key + ".", suffix=".tmp", dir=str(self.dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(lock.to_dict(), ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, str(target))
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def _modify(
        self,
        platform: str,
        title: str,
        now: datetime,
        fn: Callable[[Optional[Lock]], Lock],
    ) -> Lock:
        key = self.key(platform, title)
        fd = self._guard(key)
        try:
            existing = self._read(key)
            lock = fn(existing)
            lock.claimed_at = lock.claimed_at or to_iso(now)
            self._write(key, lock)
            return lock
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def claim(
        self,
        platform: str,
        title: str,
        owner: Optional[str] = None,
        ttl: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Lock:
        now = now or now_local()
        ttl = int(ttl or self.cfg.get("claim_ttl", 7200))
        dedupe_hours = float(self.cfg.get("dedupe_hours", 24))
        title = title.strip()
        if not title:
            raise LockError("标题不能为空", 2)

        def apply(existing: Optional[Lock]) -> Lock:
            prev_evidence = None
            if existing is not None:
                prev_evidence = existing.evidence or existing.list_row or existing.prev_evidence
                published = parse_iso(existing.published_at)
                confirmed = parse_iso(existing.confirmed_at)
                if published is not None and _seconds(published, now) < dedupe_hours * 3600:
                    raise LockError(
                        "同平台同标题在 %.0f 小时内已发布过：%s"
                        % (dedupe_hours, existing.evidence or existing.list_row or existing.state),
                        3,
                        existing,
                    )
                if confirmed is not None and _seconds(confirmed, now) < dedupe_hours * 3600:
                    raise LockError(
                        "同平台同标题刚确认发布：%s" % (existing.list_row or existing.evidence or ""),
                        3,
                        existing,
                    )
                if existing.state in ("claimed", "verified"):
                    claimed = parse_iso(existing.claimed_at)
                    age = _seconds(claimed, now)
                    if age is not None and age < int(existing.claim_ttl or ttl):
                        raise LockError(
                            "标题已被 %s 占用（%s，状态 %s）。先 release 或等过期"
                            % (existing.owner, existing.claimed_at, existing.state),
                            3,
                            existing,
                        )
            return Lock(
                v=1,
                platform=platform,
                title=title,
                state="claimed",
                owner=owner or default_owner(),
                claimed_at=to_iso(now),
                claim_ttl=ttl,
                prev_evidence=prev_evidence,
            )

        return self._modify(platform, title, now, apply)

    def verify(
        self,
        platform: str,
        title: str,
        evidence: str,
        now: Optional[datetime] = None,
    ) -> Lock:
        now = now or now_local()
        if not evidence.strip():
            raise LockError("verify 必须写证据：列表里没有同标题行", 2)

        def apply(existing: Optional[Lock]) -> Lock:
            if existing is None:
                raise LockError("没有 claim 记录，先 claim", 3)
            if existing.state != "claimed":
                raise LockError("当前状态 %s，不能 verify（需 claimed）" % existing.state, 3, existing)
            claimed = parse_iso(existing.claimed_at)
            age = _seconds(claimed, now)
            if age is None or age > int(existing.claim_ttl):
                raise LockError("claim 已过期，重新 claim", 3, existing)
            existing.state = "verified"
            existing.verified_at = to_iso(now)
            existing.evidence = evidence.strip()
            return existing

        return self._modify(platform, title, now, apply)

    def publish(self, platform: str, title: str, now: Optional[datetime] = None) -> Lock:
        now = now or now_local()
        window = int(self.cfg.get("verify_to_publish", 60))

        def apply(existing: Optional[Lock]) -> Lock:
            if existing is None:
                raise LockError("没有 claim 记录，先 claim", 3)
            if existing.state != "verified":
                raise LockError("当前状态 %s，不能 publish（需 verified）" % existing.state, 3, existing)
            verified = parse_iso(existing.verified_at)
            age = _seconds(verified, now)
            if age is None or age < 0:
                raise LockError("verify 时间异常，重新 verify", 3, existing)
            if age > window:
                raise LockError(
                    "发前复查已过期 %.0f 秒（窗口 %d 秒），重新 verify 再点发布"
                    % (age - window, window),
                    3,
                    existing,
                )
            existing.state = "published"
            existing.published_at = to_iso(now)
            return existing

        return self._modify(platform, title, now, apply)

    def confirm(
        self,
        platform: str,
        title: str,
        list_row: str,
        now: Optional[datetime] = None,
    ) -> Tuple[Lock, Path, bool]:
        now = now or now_local()
        window = int(self.cfg.get("publish_to_confirm", 900))
        list_row = list_row.strip()
        if not list_row:
            raise LockError("confirm 必须给 --list-row（列表里真实出现的整行）", 2)
        if normalize(title) not in normalize(list_row):
            raise LockError("列表行里找不到标题，不能确认落地：%r" % list_row, 3)

        from .ledger import append_clock, append_publication

        def apply(existing: Optional[Lock]) -> Lock:
            if existing is None:
                raise LockError("没有 claim 记录，先 claim", 3)
            if existing.state != "published":
                raise LockError("当前状态 %s，不能 confirm（需 published）" % existing.state, 3, existing)
            published = parse_iso(existing.published_at)
            age = _seconds(published, now)
            if age is None or age > window:
                raise LockError(
                    "发布后超过 %d 分钟仍未确认；先查列表，可能已上线，不要重发" % (window // 60),
                    3,
                    existing,
                )
            existing.state = "confirmed"
            existing.confirmed_at = to_iso(now)
            existing.list_row = list_row
            return existing

        lock = self._modify(platform, title, now, apply)
        path, appended = append_publication(self.config, lock, now)
        append_clock(
            self.config,
            {
                "kind": "publication",
                "owner": lock.owner,
                "platform": lock.platform,
                "slot": "",
                "action": "confirm",
                "criteria": "列表行包含标题",
                "evidence": lock.list_row or "",
                "missing": [],
            },
            now=now,
        )
        return lock, path, appended

    def release(self, platform: str, title: str, reason: str, now: Optional[datetime] = None) -> Lock:
        now = now or now_local()
        if not reason.strip():
            raise LockError("release 必须写 --reason（为什么没发成）", 2)

        def apply(existing: Optional[Lock]) -> Lock:
            if existing is None:
                raise LockError("没有这个锁", 3)
            if existing.state == "confirmed":
                raise LockError("已确认发布，不能 release；要再发请换标题或等去重窗口过去", 3, existing)
            existing.state = "released"
            existing.release_reason = reason.strip()
            return existing

        return self._modify(platform, title, now, apply)

    def status(self, platform: str, title: str) -> Optional[Lock]:
        return self._read(self.key(platform, title))

    def list_locks(self) -> List[Tuple[Path, Lock]]:
        items: List[Tuple[Path, Lock]] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append((path, Lock.from_dict(data)))
            except (OSError, ValueError, TypeError):
                continue
        return items
