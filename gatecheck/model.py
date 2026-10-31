"""Data types shared by rules, locks and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SEVERITIES = ("fail", "warn", "info", "skip")


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    path: str = ""
    line: int = 0
    excerpt: str = ""
    fix: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "excerpt": self.excerpt,
            "message": self.message,
            "fix": self.fix,
            "data": self.data,
        }


@dataclass
class Draft:
    path: Optional[str]
    text: str
    meta: Optional[Dict[str, Any]] = None
    meta_path: Optional[str] = None
    platform: str = ""
    title: str = ""

    @property
    def lines(self) -> List[str]:
        return self.text.splitlines()

    @property
    def chars(self) -> int:
        return len("".join(self.text.split()))

    def line_of(self, index: int) -> int:
        return self.text[:index].count("\n") + 1

    def excerpt(self, index: int, width: int = 24) -> str:
        start = max(0, index - width)
        end = min(len(self.text), index + width)
        return self.text[start:end].replace("\n", " ").strip()


@dataclass
class Lock:
    v: int
    platform: str
    title: str
    state: str
    owner: str
    claimed_at: str
    claim_ttl: int
    verified_at: Optional[str] = None
    published_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    evidence: Optional[str] = None
    list_row: Optional[str] = None
    prev_evidence: Optional[str] = None
    release_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": self.v,
            "platform": self.platform,
            "title": self.title,
            "state": self.state,
            "owner": self.owner,
            "claimed_at": self.claimed_at,
            "claim_ttl": self.claim_ttl,
            "verified_at": self.verified_at,
            "published_at": self.published_at,
            "confirmed_at": self.confirmed_at,
            "evidence": self.evidence,
            "list_row": self.list_row,
            "prev_evidence": self.prev_evidence,
            "release_reason": self.release_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Lock":
        fields = {
            "v": int(data.get("v", 1)),
            "platform": str(data.get("platform", "")),
            "title": str(data.get("title", "")),
            "state": str(data.get("state", "claimed")),
            "owner": str(data.get("owner", "")),
            "claimed_at": str(data.get("claimed_at", "")),
            "claim_ttl": int(data.get("claim_ttl", 7200)),
            "verified_at": data.get("verified_at"),
            "published_at": data.get("published_at"),
            "confirmed_at": data.get("confirmed_at"),
            "evidence": data.get("evidence"),
            "list_row": data.get("list_row"),
            "prev_evidence": data.get("prev_evidence"),
            "release_reason": data.get("release_reason"),
        }
        return cls(**fields)
