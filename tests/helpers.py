"""Shared test helpers (not collected as tests)."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gatecheck.config import DEFAULTS, Config
from gatecheck.draft import load_draft, normalize
from gatecheck.rules import RuleContext, run_rules

T0 = datetime(2026, 10, 25, 21, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def make_config(root: Path) -> Config:
    return Config(copy.deepcopy(DEFAULTS), root)


def write_draft(
    root: Path,
    name: str,
    text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    if meta is not None:
        path.with_name(path.stem + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    return path


def findings_for(
    path: Path,
    platform: str,
    config: Config,
    corpus: Optional[List[Dict[str, Any]]] = None,
    fenced: bool = False,
):
    draft = load_draft(path, platform, None, fenced=fenced)
    return run_rules(RuleContext(draft, config, corpus or [], T0))


def corpus_row(path: Path, text: str, is_note: bool = False) -> Dict[str, Any]:
    norm = normalize(text)[:3000]
    return {"path": str(path), "norm": norm, "digest": "", "mtime": 0.0, "is_note": is_note}
