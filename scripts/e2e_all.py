"""一键端到端验收：重建干净种子库 → 起服务 → 跑检查 → 停服务。

用法：

    python scripts/e2e_all.py                 # 端口 8000
    python scripts/e2e_all.py --port 8010     # 换端口
    python scripts/e2e_all.py --keep-server   # 跑完不停服务，方便手工点界面

为什么要先重建库：`e2e_check.py` 的第 2 / 4 段断言的是**种子库不变量**
（20 运行 / 12 成功 / 70.6% / 21.8s / 路由 9-6-5 / 阶段首项 11400ms）。
跑一次对话就会往 `agent_runs` 里加记录，这些数字必然漂移；所以每次验收
都从空库重新播种。**这会删除开发库里的数据**（backend/data/ecom.db 与
backend/data/chroma），仅用于验收，不要指向生产库。

退出码：0 = 全部通过；1 = 有失败；2 = 服务启动失败。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "backend" / "data"


def wipe_db(verbose: bool = True) -> None:
    """删除 SQLite 库与向量库，让服务下次启动重新播种。"""
    targets = [
        DATA / "ecom.db",
        DATA / "ecom.db-wal",
        DATA / "ecom.db-shm",
    ]
    for t in targets:
        try:
            t.unlink()
            if verbose:
                print(f"  已删除 {t.relative_to(ROOT)}")
        except FileNotFoundError:
            pass
    chroma = DATA / "chroma"
    if chroma.exists():
        shutil.rmtree(chroma, ignore_errors=True)
        if verbose:
            print(f"  已删除 {chroma.relative_to(ROOT)}/")


def wait_health(base: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="一键端到端验收")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=float, default=180.0, help="等待健康检查的秒数")
    ap.add_argument("--keep-server", action="store_true", help="跑完不停止服务")
    ap.add_argument("--skip-wipe", action="store_true",
                    help="不重建种子库（仅在库本来就是干净种子时可用）")
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    python = sys.executable

    print("=" * 70)
    print("步骤 1/3  重建干净种子库")
    if args.skip_wipe:
        print("  已跳过（--skip-wipe）")
    else:
        wipe_db()

    print("=" * 70)
    print(f"步骤 2/3  启动服务  {base}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = ROOT / ".e2e-server.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [python, str(ROOT / "backend" / "run.py"), "--port", str(args.port)],
        cwd=str(ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    print(f"  pid={proc.pid}  日志：{log_path.name}")

    if not wait_health(base, args.timeout):
        print(f"  服务在 {args.timeout:.0f}s 内未就绪，日志尾部：")
        log_file.flush()
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        for line in tail:
            print("   |", line)
        proc.terminate()
        return 2
    print("  健康检查通过")

    print("=" * 70)
    print("步骤 3/3  运行端到端检查")
    try:
        code = subprocess.call(
            [python, str(SCRIPTS / "e2e_check.py"), "--base", base], cwd=str(ROOT), env=env
        )
    finally:
        if args.keep_server:
            print(f"\n服务仍在运行：{base}（pid={proc.pid}，Ctrl+C 或手动结束）")
        else:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("\n服务已停止")
        log_file.close()

    print(f"完整服务日志：{log_path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
