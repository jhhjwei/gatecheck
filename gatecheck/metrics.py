"""G008: milestone criteria must be falsifiable, not tautologies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .model import Finding

TAUTOLOGY_BASELINES = {None, "", "cumulative", "total", "累计", "存量", "账面累计"}


class MetricsError(Exception):
    pass


def load_milestones(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MetricsError("无法读取判据文件：%s（%s）" % (path, exc))
    if isinstance(raw, dict):
        items = raw.get("milestones")
    else:
        items = raw
    if not isinstance(items, list):
        raise MetricsError("判据文件必须是数组，或含 milestones 数组的对象：%s" % path)
    return [item for item in items if isinstance(item, dict)]


def audit(milestones: Iterable[Dict[str, Any]], path: str = "") -> List[Finding]:
    findings: List[Finding] = []
    for index, item in enumerate(milestones, 1):
        name = str(item.get("id") or item.get("name") or "milestone-%d" % index)
        target = item.get("target")
        metric = str(item.get("metric") or "")
        notes = str(item.get("notes") or "")
        stream = str(item.get("income_stream") or "").lower()

        if target is None or str(target).strip() == "":
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：缺少 target，无法判断是否达成" % name,
                    path=path,
                    fix="写具体阈值与单位，例如 500 元 / 30000 次",
                )
            )

        target_text = str(target or "")
        baseline = item.get("baseline")
        if baseline in TAUTOLOGY_BASELINES:
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：baseline=%r 是恒真条件（切换前累计已满足）" % (name, baseline),
                    path=path,
                    fix="改成「切换后增量」，并写清基线读数与读取时间",
                )
            )
        if re.search(r"累计|存量|账面", target_text) and not re.search(r"增量|环比|窗口", notes):
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：target 写的是累计口径，判据会恒真" % name,
                    path=path,
                    fix="改为「切换后增量」并限定观察窗口",
                )
            )

        window = item.get("window_hours")
        try:
            window_value = float(window)
        except (TypeError, ValueError):
            window_value = 0.0
        if window_value <= 0:
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：缺少有效的 window_hours，观察窗口不明" % name,
                    path=path,
                    fix="写 72 这类明确小时数；短于 72h 的结论只能记为候选",
                )
            )

        source = str(item.get("source_field") or "").strip()
        if not source:
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：缺少 source_field，无法回查数据来源" % name,
                    path=path,
                    fix="写清接口字段或文件列，例如 stat_trends.impression_count",
                )
            )

        if stream in ("platform", "content", "分成", "ad_share", "广告分成") and re.search(
            r"激励|任务奖励|活动奖励|发稿有礼", notes
        ):
            findings.append(
                Finding(
                    "G008",
                    "fail",
                    "%s：把激励任务奖励当成了平台分成" % name,
                    path=path,
                    fix="看收益明细的 composition，激励收益单列，不计入内容分成",
                )
            )

        if metric.lower() in ("income", "revenue", "net_revenue") and not stream:
            findings.append(
                Finding(
                    "G008",
                    "warn",
                    "%s：收入类判据没写 income_stream，分成/服务/赞助会混在一起" % name,
                    path=path,
                    fix="补 income_stream（service / platform / sponsor）",
                )
            )

        if not item.get("check_after"):
            findings.append(
                Finding(
                    "G008",
                    "warn",
                    "%s：没写 check_after，判据没有到期复核时点" % name,
                    path=path,
                    fix="写 ISO 时间或相对天数，到期必须复核并记结论",
                )
            )
    return findings
