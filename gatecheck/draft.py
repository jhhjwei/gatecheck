"""Draft/meta loading, text normalisation and corpus scanning."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import Config
from .model import Draft

URL_RE = re.compile(r"https?://\S+", re.I)
IMG_RE = re.compile(r"\[\[IMG:[^\]]*\]\]")
SPACE_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)

SKIP_DIRS = {".git", ".gatecheck", ".thumb", "node_modules", "__pycache__", "assets"}
SKIP_NAMES = {"readme.md", "readme.txt"}


class DraftError(Exception):
    pass


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def normalize(text: str) -> str:
    """NFKC + lower + strip URLs/images/punctuation. Keeps CJK characters."""
    out = unicodedata.normalize("NFKC", text).lower()
    out = URL_RE.sub("", out)
    out = IMG_RE.sub("", out)
    out = SPACE_PUNCT_RE.sub("", out)
    return out


def digest(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def resolve_meta(path: Path) -> Optional[Path]:
    path = Path(path)
    if path.name.endswith(".meta.json"):
        return path if path.is_file() else None
    stem = path.stem
    candidates = [path.with_name(stem + ".meta.json")]
    if stem.endswith("-body"):
        # 正文常写成 xxx-body.txt，meta 却挂在 xxx.meta.json
        candidates.append(path.with_name(stem[: -len("-body")] + ".meta.json"))
    else:
        candidates.append(path.with_name(stem + "-body.meta.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def resolve_body(meta_path: Path) -> Optional[Path]:
    name = meta_path.name
    if name.endswith(".meta.json"):
        base = name[: -len(".meta.json")]
    else:
        base = meta_path.stem
    for suffix in (".txt", ".md"):
        candidate = meta_path.with_name(base + suffix)
        if candidate.is_file():
            return candidate
    if not base.endswith("-body"):
        for suffix in (".txt", ".md"):
            candidate = meta_path.with_name(base + "-body" + suffix)
            if candidate.is_file():
                return candidate
    return None


def load_meta(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def first_line_title(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean
    return ""


def fenced_only(text: str) -> str:
    """Return only fenced blocks.

    Ops drafts often wrap the real post body in ``` fences and keep review
    notes, change tables and platform reminders outside. Those notes contain
    banned words *as examples*, so scanning them would flag a clean post.
    """
    blocks = FENCE_RE.findall(text)
    return "\n\n".join(block.rstrip() for block in blocks) if blocks else text


def load_draft(
    path: Path,
    platform: str,
    meta_override: Optional[str] = None,
    fenced: bool = False,
) -> Draft:
    path = Path(path)
    meta_path: Optional[Path] = Path(meta_override) if meta_override else None
    if path.name.endswith(".meta.json"):
        meta_path = meta_path or path
        body = resolve_body(path)
        if body is None:
            raise DraftError("找不到正文：%s 旁挂的 .txt/.md" % path)
        text = read_text(body)
        draft_path = str(body)
    else:
        text = read_text(path)
        draft_path = str(path)
        if meta_path is None:
            meta_path = resolve_meta(path)

    if fenced:
        text = fenced_only(text)

    meta = load_meta(meta_path)
    title = ""
    if meta:
        title = str(meta.get("title") or "").strip()
    if not title:
        title = first_line_title(text)

    return Draft(
        path=draft_path,
        text=text,
        meta=meta,
        meta_path=str(meta_path) if meta_path else None,
        platform=platform,
        title=title,
    )


def _is_candidate(path: Path) -> bool:
    name = path.name.lower()
    if name in SKIP_NAMES or name.startswith("."):
        return False
    if name.endswith(".meta.json") or name.endswith(".score.txt"):
        return False
    if path.suffix.lower() not in (".md", ".txt"):
        return False
    return True


def iter_draft_files(path: Path) -> Iterable[Path]:
    path = Path(path)
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        raise DraftError("路径不存在：%s" % path)
    for candidate in sorted(path.rglob("*")):
        if not candidate.is_file() or not _is_candidate(candidate):
            continue
        if any(part in SKIP_DIRS for part in candidate.parts):
            continue
        yield candidate


def looks_like_note(text: str) -> bool:
    """Ops notes start with a markdown heading and carry bullet/table lines."""
    head = text.lstrip()
    if head.startswith("#"):
        return True
    bullets = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "| ")):
            bullets += 1
    return bullets >= 2


def build_corpus(config: Config, exclude: Iterable[Path] = ()) -> List[Dict[str, Any]]:
    corpus_cfg = config.corpus
    roots = [str(r) for r in corpus_cfg.get("roots", [])]
    window_days = int(corpus_cfg.get("window_days", 14))
    max_docs = int(corpus_cfg.get("max_docs", 200))
    exclude_globs = [str(p) for p in corpus_cfg.get("exclude", [])]
    cutoff = time.time() - window_days * 86400

    excluded = {Path(p).resolve() for p in exclude}
    docs: List[Dict[str, Any]] = []
    seen = set()
    for root_str in roots:
        root = Path(root_str)
        if not root.is_absolute():
            root = config.root / root
        if not root.exists():
            continue
        for path in iter_draft_files(root):
            resolved = path.resolve()
            if resolved in excluded or resolved in seen:
                continue
            if any(fnmatch(path.name, glob) for glob in exclude_globs):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue
            try:
                text = read_text(path)
            except OSError:
                continue
            norm = normalize(text)[:3000]
            if len(norm) < 80:
                continue
            seen.add(resolved)
            docs.append(
                {
                    "path": str(path),
                    "norm": norm,
                    "digest": hashlib.sha1(norm.encode("utf-8")).hexdigest(),
                    "mtime": mtime,
                    "is_note": looks_like_note(text),
                }
            )
    docs.sort(key=lambda d: d["mtime"], reverse=True)
    return docs[:max_docs]
