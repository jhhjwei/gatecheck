"""Rule implementations G001-G007 and G009.

Every rule returns findings instead of printing, so the CLI can render text or
JSON from the same result. Anything that cannot be judged reliably is
downgraded to warn/skip on purpose: a noisy gate gets uninstalled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from . import platforms
from .config import Config, ConfigError
from .draft import normalize
from .model import Draft, Finding

FAIL, WARN, INFO, SKIP = "fail", "warn", "info", "skip"

URL_RE = re.compile(r"https?://\S+", re.I)
CMD_RE = re.compile(r"`[^`\n]{2,}`")
PATH_RE = re.compile(r"(?:^|\s)(?:\.{0,2}/)?(?:ops|drafts|src)/[\w./-]+")

COUNT_COLLECTION_RE = re.compile(
    r"(合集|系列|专栏)(?!第)[^。！？\n]{0,6}?([0-9一二三四五六七八九十百]+)\s*篇"
)
COUNT_SUFFIX_RE = re.compile(
    r"([0-9一二三四五六七八九十百]+)\s*篇[^。！？\n]{0,6}?(合集|系列|专栏)"
)

CTA_FAIL_RE = re.compile(r"关注我|关注不迷路|点赞关注|求关注|求赞|评论区扣|扣1|加我")
CTA_WARN_RE = re.compile(r"点赞|转发|收藏")
REQUEST_RE = re.compile(r"(你|您|大家|记得|欢迎|请|随手|帮忙|别忘了|求|一定)")
NEGATION_RE = re.compile(r"(不是|不用|不做|不接|不写|不提|不说|禁止|杜绝|绝不|不会|无需|而非)")
CLAUSE_BREAK = "。！？；\n"

TEMPLATE_WORDS = (
    "赋能",
    "闭环",
    "打造",
    "抓手",
    "底层逻辑",
    "在这个时代",
    "众所周知",
    "不得不说",
    "干货满满",
    "性价比之王",
)


@dataclass
class RuleContext:
    draft: Draft
    config: Config
    corpus: List[Dict[str, Any]] = field(default_factory=list)
    now: Any = None


def _f(
    rule: str,
    severity: str,
    message: str,
    draft: Draft,
    line: int = 0,
    excerpt: str = "",
    fix: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> Finding:
    return Finding(
        rule=rule,
        severity=severity,
        message=message,
        path=draft.path or "",
        line=line,
        excerpt=excerpt,
        fix=fix,
        data=data or {},
    )


def _negated_before(text: str, pos: int) -> bool:
    """True only when the negation sits in the same clause as the match."""
    window = text[max(0, pos - 8) : pos]
    cut = max(window.rfind(ch) for ch in CLAUSE_BREAK)
    if cut >= 0:
        window = window[cut + 1 :]
    return bool(NEGATION_RE.search(window))


def _same_sentence(text: str, pos: int) -> str:
    start = max(text.rfind(ch, 0, pos) for ch in CLAUSE_BREAK) + 1
    ends = [text.find(ch, pos) for ch in CLAUSE_BREAK]
    ends = [e for e in ends if e >= 0]
    end = min(ends) if ends else len(text)
    return text[start:end]


def g001_anchor(ctx: RuleContext) -> List[Finding]:
    """Every published draft needs at least one traceable anchor."""
    draft = ctx.draft
    meta = draft.meta
    if meta is None:
        weak = URL_RE.search(draft.text) or CMD_RE.search(draft.text) or PATH_RE.search(draft.text)
        if weak:
            return [
                _f(
                    "G001",
                    INFO,
                    "没有旁挂 meta；正文存在弱锚点，但无法核对抓取时间",
                    draft,
                    excerpt=weak.group(0)[:60],
                    fix="补 <name>.meta.json，在 sources[] 写 url/ref 与 fetched_at",
                )
            ]
        return [
            _f(
                "G001",
                WARN,
                "没有旁挂 meta，正文也找不到可点/可跑的锚点",
                draft,
                fix="补 <name>.meta.json 的 sources[]，或正文加入官方链接/可复现命令",
            )
        ]

    sources = meta.get("sources") or []
    valid = [
        s
        for s in sources
        if isinstance(s, dict) and (str(s.get("url") or "").strip() or str(s.get("ref") or "").strip())
    ]
    if not valid:
        return [
            _f(
                "G001",
                FAIL,
                "meta 里没有一条有效锚点（sources[].url 或 ref 均为空）",
                draft,
                fix="每条事实写 url/ref，并补 fetched_at；取不到就删掉对应数字",
            )
        ]

    missing_ts = [s for s in valid if not s.get("fetched_at")]
    if missing_ts:
        return [
            _f(
                "G001",
                WARN,
                "有 %d 条锚点缺 fetched_at，无法判断时效" % len(missing_ts),
                draft,
                fix="补抓取时间，例如 2026-10-25T21:38:00+08:00",
            )
        ]
    return []


def _has_verified_counts(meta: Optional[Dict[str, Any]]) -> bool:
    if not meta:
        return False
    value = meta.get("verified_numbers")
    if isinstance(value, dict):
        return len(value) > 0
    if isinstance(value, list):
        return len(value) > 0
    return bool(value)


def g002_number_provenance(ctx: RuleContext) -> List[Finding]:
    """F2: an internal series number must never be published as a total count."""
    draft = ctx.draft
    if _has_verified_counts(draft.meta):
        return []

    findings: List[Finding] = []
    seen = set()
    for regex in (COUNT_COLLECTION_RE, COUNT_SUFFIX_RE):
        for match in regex.finditer(draft.text):
            key = (match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _f(
                    "G002",
                    FAIL,
                    "对外数量未核实：%r。前台数不出来，就不能写" % match.group(0).strip(),
                    draft,
                    line=draft.line_of(match.start()),
                    excerpt=draft.excerpt(match.start(), 30),
                    fix="改成「本系列第 N 篇」这类不承诺总量的说法，或在 meta.verified_numbers 写前台实测数",
                    data={"match": match.group(0).strip()},
                )
            )
    return findings


def g003_title_payoff(ctx: RuleContext) -> List[Finding]:
    """A question headline must be answered by a criterion in the body."""
    draft = ctx.draft
    title = draft.title.strip()
    if not title:
        return []
    if not re.search(r"[？?]|为什么|怎么|如何|能不能|值不值", title):
        return []
    meta = draft.meta or {}
    if meta.get("answers_in_150") is True:
        return []

    text = draft.text
    # 标题里的问句，正文不一定会再问一遍：开头有数字 + 结论标记也算兑现。
    lead = text[:350]
    if re.search(r"\d", lead) and re.search(
        r"结论|答案|因为|取决于|判据|条件|实测|对比|其实|就是|倒推", lead
    ):
        return []
    for match in re.finditer(r"[？?]", text):
        window = text[match.end() : match.end() + 200]
        if re.search(r"\d|因为|取决于|判据|条件|实测|对比", window):
            return []
    return [
        _f(
            "G003",
            WARN,
            "疑问式标题没有在正文给出可判定的答案",
            draft,
            fix="在首段或对应小节写清判据（数字/条件/对比），或把标题改成陈述句",
        )
    ]


def g004_empty_cta(ctx: RuleContext) -> List[Finding]:
    draft = ctx.draft
    findings: List[Finding] = []
    fail_spans = []
    for match in CTA_FAIL_RE.finditer(draft.text):
        fail_spans.append((match.start(), match.end()))
        negated = _negated_before(draft.text, match.start())
        findings.append(
            _f(
                "G004",
                INFO if negated else FAIL,
                ("否定句里的空喊词，仍建议删掉：%r" if negated else "空喊式引流：%r") % match.group(0),
                draft,
                line=draft.line_of(match.start()),
                excerpt=draft.excerpt(match.start(), 26),
                fix="改成站内可检索资产 + 固定更新时间 + 一个二元提问",
            )
        )
    for match in CTA_WARN_RE.finditer(draft.text):
        if any(a <= match.start() < b for a, b in fail_spans):
            continue
        # 数据复盘的"收藏/转发"不是求互动：只在同句出现请求对象时才提醒。
        sentence = _same_sentence(draft.text, match.start())
        if not REQUEST_RE.search(sentence):
            continue
        findings.append(
            _f(
                "G004",
                WARN,
                "出现了求互动词：%r" % match.group(0),
                draft,
                line=draft.line_of(match.start()),
                excerpt=draft.excerpt(match.start(), 26),
                fix="删掉求赞/求转发，用具体问题引讨论",
            )
        )
    return findings[:8]


def g005_cross_post(ctx: RuleContext) -> List[Finding]:
    """F1-adjacent: identical text across platforms is a top-level platform risk."""
    if not ctx.corpus:
        return []
    draft = ctx.draft
    full = normalize(draft.text)
    mine = full[:3000]
    if len(mine) < 60:
        return []
    threshold = float(ctx.config.duplicate.get("warn_ratio", 0.86))
    my_path = str(draft.path or "")

    best_path = ""
    best_ratio = 0.0
    for doc in ctx.corpus:
        if doc["path"] == my_path:
            continue
        if doc["norm"] == mine:
            return [
                _f(
                    "G005",
                    FAIL,
                    "与已有稿件正文完全相同：%s" % doc["path"],
                    draft,
                    fix="一素材多形态：换开头、换案例、换图，不要跨平台同文",
                    data={"match": doc["path"], "ratio": 1.0},
                )
            ]
        other = doc["norm"]
        if not other:
            continue
        # CTA 摘录是本文的一部分；几乎等长的超集（标题行 + 正文）也是同一篇。
        if other in mine:
            continue
        if mine in other and (doc.get("is_note") or len(mine) >= 0.95 * len(other)):
            continue
        ratio_len = len(mine) / float(len(other))
        if ratio_len < 0.6 or ratio_len > 1.67:
            continue
        matcher = SequenceMatcher(None, mine, other, autojunk=False)
        if matcher.real_quick_ratio() < threshold:
            continue
        if matcher.quick_ratio() < threshold:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_path = doc["path"]

    if best_ratio >= threshold:
        return [
            _f(
                "G005",
                WARN,
                "与 %s 相似度 %.2f，接近同文分发" % (best_path, best_ratio),
                draft,
                fix="跨平台至少改写开头 30 秒与案例，保留同一事实锚点即可",
                data={"match": best_path, "ratio": round(best_ratio, 3)},
            )
        ]
    return []


def g006_template_smell(ctx: RuleContext) -> List[Finding]:
    draft = ctx.draft
    hits = {word: draft.text.count(word) for word in TEMPLATE_WORDS}
    total = sum(hits.values())
    if total == 0:
        return []
    chars = max(1, len("".join(draft.text.split())))
    density = total * 1000.0 / chars
    # 短稿里一个词就能把密度顶到阈值，必须先有足够样本。
    if total >= 5 or (chars >= 200 and density >= 3.0):
        top = "、".join("%s×%d" % (w, n) for w, n in hits.items() if n)
        return [
            _f(
                "G006",
                WARN,
                "模板词密度偏高（%d 处）：%s" % (total, top),
                draft,
                fix="删掉空泛词，换成具体数字、动作或对比",
                data={"hits": total, "density": round(density, 2)},
            )
        ]
    return []


def g007_ai_disclosure(ctx: RuleContext) -> List[Finding]:
    try:
        platform_cfg = ctx.config.platform(ctx.draft.platform)
    except ConfigError:
        return []
    checklist = platform_cfg.get("ai_declaration") or []
    items = "；".join(str(x) for x in checklist) if checklist else "按平台要求勾选"
    meta = ctx.draft.meta or {}
    declared = meta.get("ai_declaration")
    if declared is True:
        return [
            _f(
                "G007",
                INFO,
                "已声明 AI 辅助；发布页仍需逐项确认：%s" % items,
                ctx.draft,
            )
        ]
    if declared is False:
        return [
            _f(
                "G007",
                WARN,
                "meta.ai_declaration=false；若使用了 AI 辅助，发布页必须勾选：%s" % items,
                ctx.draft,
            )
        ]
    return [
        _f(
            "G007",
            INFO,
            "未记录 AI 声明状态；发布前确认：%s" % items,
            ctx.draft,
        )
    ]


def g009_platform_words(ctx: RuleContext) -> List[Finding]:
    """Banned words + length/first-line structure. Negation does NOT downgrade."""
    draft = ctx.draft
    rules = platforms.rules_for(draft.platform)
    try:
        platform_cfg = ctx.config.platform(draft.platform)
    except ConfigError:
        platform_cfg = {}

    findings: List[Finding] = []
    seen = set()
    for level, severity in (("high", FAIL), ("medium", WARN)):
        for pattern, why in rules.get(level, []):
            for match in re.finditer(pattern, draft.text):
                key = (match.start(), match.group(0))
                if key in seen:
                    continue
                seen.add(key)
                negated = _negated_before(draft.text, match.start())
                message = "命中平台禁词 %r — %s" % (match.group(0), why)
                if negated:
                    message += "（否定句同样命中：机审不读语义）"
                findings.append(
                    _f(
                        "G009",
                        severity,
                        message,
                        draft,
                        line=draft.line_of(match.start()),
                        excerpt=draft.excerpt(match.start(), 26),
                        fix="删掉该词，改写为能力/交付描述；否定声明也不写",
                        data={"match": match.group(0), "negated": bool(negated)},
                    )
                )

    for word in platform_cfg.get("extra_banned", []) or []:
        word = str(word).strip()
        if not word:
            continue
        for match in re.finditer(re.escape(word), draft.text):
            findings.append(
                _f(
                    "G009",
                    FAIL,
                    "命中项目自定义禁词 %r" % word,
                    draft,
                    line=draft.line_of(match.start()),
                    excerpt=draft.excerpt(match.start(), 26),
                    fix="按项目规则删改",
                )
            )

    max_len = int(rules.get("max_len", 60000))
    if draft.chars > max_len:
        findings.append(
            _f(
                "G009",
                FAIL,
                "正文 %d 字，超过 %s 上限 %d" % (draft.chars, draft.platform, max_len),
                draft,
                fix="删减到上限内；平台按字符截断会破坏结尾",
            )
        )

    # 有 meta 标题时，正文首行不是标题，改用 title_max 校验标题本身。
    meta_title = str((draft.meta or {}).get("title") or "").strip()
    if meta_title:
        title_max = int(platform_cfg.get("title_max", rules.get("first_line_max", 80)))
        if len(meta_title) > title_max:
            findings.append(
                _f(
                    "G009",
                    WARN,
                    "标题 %d 字，超过 %s 上限 %d 字" % (len(meta_title), draft.platform, title_max),
                    draft,
                    excerpt=meta_title[:40],
                    fix="压缩到 %d 字内，把限定词挪进正文" % title_max,
                    data={"title_len": len(meta_title), "title_max": title_max},
                )
            )
    elif draft.platform == "xianyu":
        # 闲鱼没有标题字段：描述首行就是标题，超长会被截断。
        # 其他平台的正文首行是段落，只有 meta.title 才是标题。
        first = next((ln.strip() for ln in draft.lines if ln.strip()), "")
        first_max = int(rules.get("first_line_max", 80))
        if first and not first.startswith("#") and len(first) > first_max:
            findings.append(
                _f(
                    "G009",
                    WARN,
                    "首行 %d 字，超过 %d 字，可能被截成标题" % (len(first), first_max),
                    draft,
                    line=1,
                    excerpt=first[:40],
                    fix="首行写完整标题，控制在 %d 字内" % first_max,
                )
            )

    if draft.platform in ("xianyu", "xhs") and URL_RE.search(draft.text):
        findings.append(
            _f(
                "G009",
                WARN,
                "正文含站外链接，易被判导流",
                draft,
                fix="删除链接，改成站内搜索词或主页路径",
            )
        )
    return findings


RULES = (
    g001_anchor,
    g002_number_provenance,
    g003_title_payoff,
    g004_empty_cta,
    g005_cross_post,
    g006_template_smell,
    g007_ai_disclosure,
    g009_platform_words,
)


def run_rules(ctx: RuleContext) -> List[Finding]:
    findings: List[Finding] = []
    for rule in RULES:
        findings.extend(rule(ctx))
    return findings
