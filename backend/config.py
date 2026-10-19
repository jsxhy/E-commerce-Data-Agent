"""配置加载：config.example.yaml → config.local.yaml，环境变量优先级最高。

放在项目根目录，供 backend/app 与 backend/scripts 共用。
"""
from __future__ import annotations

import copy
import os
import pathlib
import uuid
from functools import lru_cache
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS: dict[str, Any] = {
    "app": {"name": "电商数据分析 Agent", "host": "127.0.0.1", "port": 8000,
            "static_dir": "backend/app/static"},
    "database": {"url": "sqlite:///backend/data/ecom.db", "echo": False},
    "auth": {"jwt_secret": "dev-only-secret", "jwt_algorithm": "HS256",
             "access_token_expire_minutes": 720},
    "llm": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat",
            "api_key": "", "temperature": 0.2, "timeout_seconds": 90, "max_retries": 3},
    "mcp": {"mount_path": "/mcp", "host": "127.0.0.1", "port": 8765},
    "rag": {"model_dir": "backend/models/bge-small-zh",
            "persist_dir": "backend/data/chroma", "collection": "reviews_demo",
            "top_k": 5, "relevant_threshold": 0.5},
    "agent": {
        "sql_timeout_seconds": 5, "sql_max_rows": 200, "sql_max_retries": 2,
        "allowed_tables": ["products", "orders", "reviews", "diagnosis_reports"],
        "allowed_actions": ["save_diagnosis_report", "send_alert_notification",
                            "mark_product_pending_review"],
        "risk": {"bad_rating_max": 2, "high_risk_bad_review_count": 2,
                 "high_risk_rating_below": 2.5},
    },
}

ENV_MAP = {
    "ECOM_DB_URL": ("database", "url"),
    "ECOM_LLM_API_KEY": ("llm", "api_key"),
    "ECOM_LLM_BASE_URL": ("llm", "base_url"),
    "ECOM_LLM_MODEL": ("llm", "model"),
    "ECOM_JWT_SECRET": ("auth", "jwt_secret"),
    "ECOM_HOST": ("app", "host"),
    "ECOM_PORT": ("app", "port"),
    "ECOM_STATIC_DIR": ("app", "static_dir"),
    "ECOM_RAG_PERSIST_DIR": ("rag", "persist_dir"),
    "ECOM_RAG_COLLECTION": ("rag", "collection"),
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _apply_env(cfg: dict[str, Any]) -> dict[str, Any]:
    for env_name, (section, key) in ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        value: Any = raw
        if key == "port":
            value = int(raw)
        cfg[section][key] = value
    # LLM 密钥兜底：DeepSeek 官方环境变量
    if not cfg["llm"].get("api_key"):
        cfg["llm"]["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "")
    return cfg


def load_config(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """读取配置。path 为空时依次尝试 config.local.yaml、config.example.yaml。"""
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        cfg = _deep_merge(cfg, _read_yaml(pathlib.Path(path)))
    else:
        for name in ("config.local.yaml", "config.example.yaml"):
            candidate = ROOT / name
            if candidate.exists():
                cfg = _deep_merge(cfg, _read_yaml(candidate))
                break
    return _apply_env(cfg)


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    return load_config()


def resolve_path(value: str) -> pathlib.Path:
    """把配置里的相对路径解析成绝对路径（相对项目根）。"""
    p = pathlib.Path(value)
    return p if p.is_absolute() else (ROOT / p)


def _dir_is_writable(path: pathlib.Path) -> bool:
    """真正建一个临时文件来判断目录可写（只看权限位不够，ACL 会骗人）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-probe-{uuid.uuid4().hex}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def ensure_writable_tempdir() -> pathlib.Path | None:
    """确保 TMP/TEMP 指向一个真正可写的目录，否则 Chroma 起不来。

    背景（本机实测的真实故障）：Chroma 1.x 的 Rust 绑定在建库时会调用 Rust 的
    ``std::env::temp_dir()``，Windows 下按 ``TMP`` → ``TEMP`` → ``USERPROFILE``
    的顺序取第一个存在的值。如果它落在一个**存在但不可写**的目录上（受限/沙箱
    环境下 ``C:\\Users\\<user>\\AppData\\Local\\Temp`` 常常如此），Chroma 会报
    ``InternalError: error returned from database: (code: 14) unable to open
    database file``，表现为「向量库不可用」——而 SQLite 自己写库却完全正常，
    极难定位。

    这里在导入 chromadb 之前把 ``TMP``/``TEMP`` 改指到工作区内可写的
    ``backend/data/tmp``。返回被选中的目录；本来就可用时返回 ``None``（不改动环境）。
    """
    candidates = [os.environ.get("TMP"), os.environ.get("TEMP"),
                  os.environ.get("USERPROFILE")]
    current = next((pathlib.Path(c) for c in candidates if c), None)
    if current is not None and _dir_is_writable(current):
        return None

    fallback = ROOT / "backend" / "data" / "tmp"
    if not _dir_is_writable(fallback):
        return None  # 连兜底目录都写不了，交给上层按「向量库不可用」降级

    os.environ["TMP"] = str(fallback)
    os.environ["TEMP"] = str(fallback)
    return fallback


def reset_config_cache() -> None:
    get_config.cache_clear()


class Config:
    """点号访问包装，便于 `cfg.llm.model` 这种写法。"""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, item: str) -> Any:
        try:
            value = self._data[item]
        except KeyError as exc:  # pragma: no cover - 配置拼写错误时才触发
            raise AttributeError(item) from exc
        return Config(value) if isinstance(value, dict) else value

    def get(self, item: str, default: Any = None) -> Any:
        return self._data.get(item, default)

    def __getitem__(self, item: str) -> Any:
        return self._data[item]

    def as_dict(self) -> dict[str, Any]:
        return self._data


def config() -> Config:
    return Config(get_config())
