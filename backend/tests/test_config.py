"""配置层回归测试：可写临时目录修复（Chroma 起不来的真实故障）。

背景：Chroma 1.x 的 Rust 绑定建库时用 Rust 的 ``std::env::temp_dir()``，
Windows 下按 ``TMP`` → ``TEMP`` → ``USERPROFILE`` 取第一个存在的值。若它落在
**存在但不可写**的目录（受限/沙箱环境下 ``...\\AppData\\Local\\Temp`` 常见），
Chroma 报 ``InternalError: ... (code: 14) unable to open database file``，
而 SQLite 自己写库完全正常 —— 极难定位。``ensure_writable_tempdir()`` 负责
在导入 chromadb 之前把 ``TMP``/``TEMP`` 改指到可写的 ``backend/data/tmp``。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from backend.config import (
    _dir_is_writable,
    ensure_writable_tempdir,
    load_config,
    resolve_path,
)


@pytest.fixture
def restore_temp_env():
    """保存并还原 TMP/TEMP，避免污染其它测试。"""
    saved = {k: os.environ.get(k) for k in ("TMP", "TEMP")}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    tempfile.tempdir = None  # 让 gettempdir 重新从环境推断


def test_dir_is_writable_true_for_real_dir(tmp_path: Path):
    assert _dir_is_writable(tmp_path) is True


def test_dir_is_writable_creates_missing_dir(tmp_path: Path):
    target = tmp_path / "nested" / "deeper"
    assert _dir_is_writable(target) is True
    assert target.is_dir()


def test_dir_is_writable_false_for_file(tmp_path: Path):
    """路径已被一个文件占用时不可能当目录用。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert _dir_is_writable(blocker) is False


def test_dir_is_writable_probe_file_is_cleaned(tmp_path: Path):
    assert _dir_is_writable(tmp_path) is True
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".write-probe-")]
    assert leftovers == [], f"探测文件未清理：{leftovers}"


def test_ensure_writable_tempdir_keeps_good_dir(restore_temp_env, tmp_path: Path):
    """TMP 本来就可写时不改动环境，返回 None。"""
    good = tmp_path / "good"
    good.mkdir()
    os.environ["TMP"] = str(good)
    os.environ["TEMP"] = str(good)
    tempfile.tempdir = None

    assert ensure_writable_tempdir() is None
    assert os.environ["TMP"] == str(good)
    assert os.environ["TEMP"] == str(good)


def test_ensure_writable_tempdir_redirects_bad_dir(restore_temp_env, tmp_path: Path):
    """TMP 指向不可写路径时改指到 backend/data/tmp。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    os.environ["TMP"] = str(blocker)
    os.environ["TEMP"] = str(blocker)
    tempfile.tempdir = None

    chosen = ensure_writable_tempdir()

    assert chosen is not None, "不可写的 TMP 必须被改指"
    assert chosen.is_dir()
    assert os.environ["TMP"] == str(chosen)
    assert os.environ["TEMP"] == str(chosen)
    # 改指之后 Python 自己也应该认这个目录
    assert Path(tempfile.gettempdir()) == chosen


def test_ensure_writable_tempdir_fallback_is_inside_project(restore_temp_env, tmp_path: Path):
    """兜底目录必须落在工作区内，否则沙箱下同样写不了。"""
    blocker = tmp_path / "nope"
    blocker.write_text("x", encoding="utf-8")
    os.environ["TMP"] = str(blocker)
    os.environ["TEMP"] = str(blocker)
    tempfile.tempdir = None

    chosen = ensure_writable_tempdir()
    assert chosen is not None
    assert chosen == resolve_path("backend/data/tmp")


def test_vector_store_tempdir_is_writable_after_fix():
    """真实回归：拿向量库前，临时目录必须可写（否则 Chroma 会 code 14）。"""
    from backend.app.rag.store import get_store

    store = get_store()
    # 只要求不抛异常；命中 0 条也算正常（空库）
    count = store.count()
    assert count >= 0
    assert _dir_is_writable(Path(tempfile.gettempdir()))


def test_config_load_has_expected_keys():
    """配置组装的最小不变量（顺带覆盖 load_config 路径）。"""
    cfg = load_config()
    for section in ("app", "database", "auth", "llm", "mcp", "rag", "agent"):
        assert section in cfg, f"缺少配置段 {section}"
    assert "sqlite" in cfg["database"]["url"] or "mysql" in cfg["database"]["url"]
    assert cfg["rag"]["collection"]
    assert cfg["agent"]["allowed_actions"] == [
        "save_diagnosis_report",
        "send_alert_notification",
        "mark_product_pending_review",
    ]
