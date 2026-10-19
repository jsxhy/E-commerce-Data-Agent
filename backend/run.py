"""开发/生产启动入口：``python backend/run.py``。

等价于 ``uvicorn backend.app.main:app``，额外做三件事：

1. 把项目根加入 ``sys.path``，因此可以在任意目录下执行；
2. 支持 ``--host/--port/--reload`` 覆盖配置；
3. ``--skip-bootstrap`` 跳过种子与向量入库（只跑接口联调时更快）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文日志会炸；这里提前固定成 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    from backend.config import get_config

    cfg = get_config()
    parser = argparse.ArgumentParser(description="电商数据分析 Agent 后端")
    parser.add_argument("--host", default=cfg["app"]["host"])
    parser.add_argument("--port", type=int, default=int(cfg["app"]["port"]))
    parser.add_argument("--reload", action="store_true", help="代码变更自动重载")
    parser.add_argument("--skip-bootstrap", action="store_true",
                        help="跳过种子数据与向量入库")
    args = parser.parse_args()

    if args.skip_bootstrap:
        os.environ["ECOM_SKIP_BOOTSTRAP"] = "1"

    import uvicorn

    print(f"→ 后端启动：http://{args.host}:{args.port}")
    print(f"→ 接口文档：http://{args.host}:{args.port}/api/docs")
    print(f"→ 管理端  ：http://{args.host}:{args.port}/")
    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(ROOT / "backend")] if args.reload else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()
