"""Configuration loading and path resolution."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_NAME = "gatecheck.json"

DEFAULTS: Dict[str, Any] = {
    "state_dir": ".gatecheck",
    "ledger_dir": "ledger",
    "published_dir": "published",
    "corpus": {"roots": ["drafts"], "window_days": 14, "max_docs": 200},
    "duplicate": {"warn_ratio": 0.86},
    "publish_lock": {
        "claim_ttl": 7200,
        "verify_to_publish": 60,
        "publish_to_confirm": 900,
        "dedupe_hours": 24,
    },
    "platforms": {
        "toutiao": {
            "title_max": 22,
            "ai_declaration": ["作品声明-引用AI 勾选", "头条首发 不勾", "同时发布微头条 不勾"],
        },
        "xhs": {
            "title_max": 20,
            "ai_declaration": ["AI 生成图需在发布页勾选声明"],
        },
        "csdn": {"title_max": 80},
        "kuaishou": {"title_max": 60},
        "xianyu": {"title_max": 30},
        "baijiahao": {"title_max": 40},
        "douyin": {"title_max": 55},
        "wechat": {"title_max": 64},
    },
    "milestones": [],
    "cta_template": "都收在合集《{collection}》里，每天 11:30 更新一篇",
}


class ConfigError(Exception):
    pass


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: Dict[str, Any], root: Path, path: Optional[Path] = None):
        self.data = data
        self.root = Path(root).resolve()
        self.path = path

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def path_of(self, key: str, default: str) -> Path:
        value = self.data.get(key) or default
        p = Path(str(value)).expanduser()
        return p if p.is_absolute() else (self.root / p)

    @property
    def state_dir(self) -> Path:
        return self.path_of("state_dir", ".gatecheck")

    @property
    def ledger_dir(self) -> Path:
        return self.path_of("ledger_dir", "ledger")

    @property
    def published_dir(self) -> Path:
        return self.path_of("published_dir", "published")

    @property
    def lock_config(self) -> Dict[str, Any]:
        return self.data.get("publish_lock", {})

    @property
    def duplicate(self) -> Dict[str, Any]:
        return self.data.get("duplicate", {})

    @property
    def corpus(self) -> Dict[str, Any]:
        return self.data.get("corpus", {})

    def platform(self, name: str) -> Dict[str, Any]:
        platforms = self.data.get("platforms", {})
        if name not in platforms:
            raise ConfigError(
                "未知平台 %r；请在 %s 的 platforms 中配置，或使用内置平台之一"
                % (name, self.path or CONFIG_NAME)
            )
        return deep_merge({"title_max": 80, "ai_declaration": []}, platforms[name])

    def platform_names(self):
        return list(self.data.get("platforms", {}).keys())


def _find_up(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for candidate in [cur] + list(cur.parents):
        p = candidate / CONFIG_NAME
        if p.is_file():
            return p
    return None


def load_config(config_path: Optional[str] = None, root: Optional[str] = None) -> Config:
    env_path = os.environ.get("GATECHECK_CONFIG")
    chosen = config_path or env_path
    base_root = Path(root).expanduser().resolve() if root else Path.cwd().resolve()

    if chosen:
        path = Path(chosen).expanduser()
        if not path.is_absolute():
            path = (base_root / path).resolve()
        if not path.is_file():
            raise ConfigError("配置文件不存在：%s" % path)
        config_root = path.parent
    else:
        found = _find_up(base_root)
        path = found
        config_root = found.parent if found else base_root

    data = copy.deepcopy(DEFAULTS)
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError("配置无法解析：%s（%s）" % (path, exc))
        if not isinstance(raw, dict):
            raise ConfigError("配置顶层必须是 JSON 对象：%s" % path)
        data = deep_merge(data, raw)

    return Config(data, config_root, path)


def write_default_config(path: Path) -> None:
    if path.exists():
        raise ConfigError("已存在配置，未覆盖：%s" % path)
    path.write_text(
        json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
