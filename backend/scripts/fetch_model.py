"""下载中文 embedding 模型 ``BAAI/bge-small-zh-v1.5``（ONNX 版）。

为什么需要这个脚本：

* HuggingFace 官方域名在国内常被墙，``huggingface.co`` 直连会超时；
  ``hf-mirror.com`` 可用，但对默认 UA 返回 403，必须显式带浏览器 UA；
* ``urllib.request.urlretrieve`` 在镜像上会 403，必须自己 ``Request`` + 分块写盘；
* 模型文件约 90MB，必须支持完整性校验，否则中断一次就得重下。

用法::

    python backend/scripts/fetch_model.py            # 缺什么补什么
    python backend/scripts/fetch_model.py --force    # 强制重下

产物落在 ``backend/models/bge-small-zh/``：``model.onnx`` / ``tokenizer.json`` / ``config.json``。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT / "backend" / "models" / "bge-small-zh"

#: 镜像优先（国内可直连），官方域名兜底
MIRRORS = (
    "https://hf-mirror.com/BAAI/bge-small-zh-v1.5/resolve/main",
    "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main",
)

#: 需要下载的文件及其期望字节数。
#: ``None`` 表示只校验「能解析成 JSON」：config.json 在不同镜像上会被重新序列化，
#: 大小会变（701 / 776 都出现过）。model.onnx 是二进制大文件，必须严格校验大小。
FILES: dict[str, int | None] = {
    "model.onnx": 94851877,
    "tokenizer.json": 439125,
    "config.json": None,
}

HEADERS = {
    # hf-mirror 对非浏览器 UA 直接 403
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}
CHUNK = 1024 * 1024


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _ok(path: pathlib.Path, expected: int | None) -> bool:
    """本地文件是否已经可用（大小 + 可解析性）。"""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if expected is None:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 解析失败即视为损坏
            return False
        return True
    return path.stat().st_size == expected


def _download_one(name: str, expected: int | None, force: bool) -> bool:
    target = MODEL_DIR / name
    if not force and _ok(target, expected):
        print(f"  [skip] {name} 已存在（{_human(target.stat().st_size)}）")
        return True

    last_error: Exception | None = None
    for base in MIRRORS:
        url = f"{base}/{name}"
        tmp = target.with_suffix(target.suffix + ".part")
        print(f"  [get ] {name} <- {url}")
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with tmp.open("wb") as fh:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done * 100 // total
                            print(f"\r         {pct:3d}%  {_human(done)}/{_human(total)}",
                                  end="", flush=True)
                print()
            if expected is not None and tmp.stat().st_size != expected:
                raise IOError(
                    f"大小不符：期望 {expected} 字节，实际 {tmp.stat().st_size} 字节")
            os.replace(tmp, target)
            print(f"  [ ok ] {name} -> {target}（{_human(target.stat().st_size)}）")
            return True
        except Exception as exc:  # noqa: BLE001 - 镜像逐个尝试
            last_error = exc
            print(f"  [fail] {name}：{exc}")
            tmp.unlink(missing_ok=True)

    print(f"  [dead] {name} 所有镜像均失败：{last_error}", file=sys.stderr)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 bge-small-zh-v1.5 ONNX 模型")
    parser.add_argument("--force", action="store_true", help="忽略本地已有文件，强制重下")
    args = parser.parse_args()

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"目标目录：{MODEL_DIR}")

    failed = [name for name, expected in FILES.items()
              if not _download_one(name, expected, args.force)]
    if failed:
        print(f"\n下载失败：{', '.join(failed)}", file=sys.stderr)
        print("可手动从 https://hf-mirror.com/BAAI/bge-small-zh-v1.5/tree/main 下载后放入上述目录。",
              file=sys.stderr)
        return 1

    print("\n校验模型可加载 ...")
    from backend.app.rag.embedding import get_embedder

    embedder = get_embedder()
    vector = embedder.encode(["跑鞋脱胶"])[0]
    print(f"  维度 {len(vector)}，样例前 3 维 {[round(x, 4) for x in vector[:3]]}")
    print("模型就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
