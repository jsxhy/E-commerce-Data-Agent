"""生成 config.local.yaml：填入随机 JWT 密钥。

**不会写入任何真实 API 密钥。**
`llm.api_key` 保持为空，运行时由 `backend/config.py` 从环境变量读取
（`ECOM_LLM_API_KEY`，其次 `DEEPSEEK_API_KEY`）。这样密钥永远不落盘到项目文件里，
避免误提交或误分享。

用法：
    python backend/scripts/make_local_config.py            # 生成（已存在则拒绝覆盖）
    python backend/scripts/make_local_config.py --force    # 覆盖重建

配置好密钥后启动服务：
    # PowerShell
    $env:DEEPSEEK_API_KEY = "sk-..."
    python backend/run.py

    # bash
    export DEEPSEEK_API_KEY="sk-..."
    python backend/run.py
"""
from __future__ import annotations

import argparse
import pathlib
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "config.example.yaml"
TARGET = ROOT / "config.local.yaml"

PLACEHOLDER_SECRET = "jwt_secret: change-me-in-config-local"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 config.local.yaml")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 config.local.yaml")
    args = ap.parse_args()

    if not TEMPLATE.exists():
        print(f"找不到模板：{TEMPLATE}")
        return 1
    if TARGET.exists() and not args.force:
        print(f"{TARGET.name} 已存在，未覆盖（要重建请加 --force）")
        return 0

    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace(PLACEHOLDER_SECRET, "jwt_secret: " + uuid.uuid4().hex)
    TARGET.write_text(text, encoding="utf-8")

    print(f"已生成 {TARGET.relative_to(ROOT)}")
    print("  jwt_secret：已随机生成")
    print("  llm.api_key：留空（从环境变量 ECOM_LLM_API_KEY / DEEPSEEK_API_KEY 读取）")
    print()
    print("设置密钥后启动：")
    print('  PowerShell:  $env:DEEPSEEK_API_KEY = "sk-..."')
    print("  bash:        export DEEPSEEK_API_KEY='sk-...'")
    print("  然后：       python backend/run.py")
    print()
    print("不设密钥也能启动：对话链路会走降级回答，其余功能不受影响。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
