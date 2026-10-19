# 电商数据分析 Agent

一套可运行的**电商数据分析智能体系统**：管理端看板 + 自然语言分析对话 + 多智能体
（LangGraph）推理 + MCP 工具调用 + 中文语义检索 + 人工确认的动作执行。


---

## 功能一览

| 模块 | 说明 |
|---|---|
| **系统首页** | 8 个实时 KPI、近 30 天订单趋势、星级分布、需重点关注商品、一键巡检 |
| **数据分析对话** | SSE 流式回答；可折叠「处理过程」面板逐条展示 Agent 交接与阶段耗时 |
| **运行历史** | 全部 Agent 运行记录、右侧抽屉展示逐步耗时、👍/👎 反馈 |
| **诊断报告** | 报告列表（风险分级）+ 预警通知（未读角标）+ 详情 |
| **观测看板** | 8 个运行 KPI、路由分布环形图、各阶段平均耗时条形图、近 14 天运行量 |
| **语义检索** | 直接把一句话送进向量库，展示命中片段、余弦距离与相关性判定 |
| **数据管理** | 商品 / 订单 / 评价 / 用户 四张表的查询与增删改 |
| **Agent 能力** | 规则路由 → 查询分析 Agent → 报告决策 Agent；高风险动作**人工确认**后才执行 |

---

## 技术栈

**后端**：Python 3.12+ · FastAPI · SQLAlchemy 2 · LangGraph · MCP（FastMCP）·
ChromaDB · onnxruntime（bge-small-zh-v1.5）· SSE

**前端**：Vue 3 · Vue Router · Pinia · Element Plus · ECharts · Axios · Rollup

**数据库**：默认 SQLite（开箱即用）；可切 MySQL 8（见下文）

---

## 快速开始

### 0. 环境要求

* Python **3.12+**（本机实测 3.14.3）
* Node.js **22.18+ / 24.12+**（本机实测 24.13.1）+ pnpm
* 约 300MB 磁盘（含 90MB 中文 embedding 模型）

### 1. 安装后端依赖

```bash
# 推荐用 uv（pip 在部分受限环境下无法写入临时目录）
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r backend/requirements.txt

# 或者直接用 pip
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
```

> ⚠️ **`mcp` 必须 `<2`**（已钉 `mcp==1.30.0`）。mcp 2.x 把 `FastMCP` 改名为
> `MCPServer`，`from mcp.server.fastmcp import FastMCP` 会直接 `ModuleNotFoundError`。

### 2. 下载中文 embedding 模型

```bash
python backend/scripts/fetch_model.py
```

从 `hf-mirror.com` 下载 `BAAI/bge-small-zh-v1.5` 的 ONNX 版到
`backend/models/bge-small-zh/`（`model.onnx` 90.5MB / `tokenizer.json` / `config.json`）。
脚本自带大小校验与镜像回退，可重复执行（已存在则跳过）。

### 3. 生成配置

```bash
python backend/scripts/make_local_config.py
```

会从 `config.example.yaml` 生成 `config.local.yaml`，并填入一个随机 `jwt_secret`。

> **密钥不写进配置文件。** `llm.api_key` 保持为空，运行时从环境变量读取
> （优先 `ECOM_LLM_API_KEY`，其次 `DEEPSEEK_API_KEY`）。这样密钥永远不会落到
> 项目文件里，避免误提交、误打包或误分享。
>
> ```bash
> # PowerShell
> $env:DEEPSEEK_API_KEY = "sk-..."
> # bash
> export DEEPSEEK_API_KEY="sk-..."
> ```
>
> 不设密钥也能启动：对话链路走降级回答，其余功能（看板/检索/报告/CRUD）不受影响。

也可以手动复制后编辑：

```bash
cp config.example.yaml config.local.yaml
```

`config.local.yaml` 与 `*.local.yaml` 已在 `.gitignore` 中，不会进版本库。

### 4. 构建前端

```bash
cd frontend
pnpm install --ignore-scripts        # 必须加 --ignore-scripts（见下方说明）
pnpm run build                       # 产物落到 backend/app/static/
```

> 若 ESM 解析报 `Cannot find package 'picomatch'`，请设
> `export npm_config_node_linker=hoisted` 后重装（pnpm 默认 symlink 布局的已知问题）。

### 5. 启动

```bash
python backend/run.py
```

* 管理端：<http://127.0.0.1:8000/>
* 接口文档：<http://127.0.0.1:8000/api/docs>
* 健康检查：<http://127.0.0.1:8000/api/health>

首次启动会自动建表、灌入种子数据（20 商品 / 46 评价 / 49 订单 / 20 运行记录 …）
并把 66 条文档灌进向量库。**整个过程约 10–20 秒**（含 ONNX 模型加载）。

默认账号：

| 账号 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 管理员 |
| `analyst` | `analyst123` | 数据分析师 |

---

## 配置说明

配置文件查找顺序：`config.local.yaml` → `config.example.yaml`，
所有键都可用环境变量覆盖（优先级最高）：

| 环境变量 | 对应配置 |
|---|---|
| `ECOM_DB_URL` | `database.url` |
| `ECOM_LLM_API_KEY` / `ECOM_LLM_BASE_URL` / `ECOM_LLM_MODEL` | `llm.*` |
| `ECOM_JWT_SECRET` | `auth.jwt_secret` |
| `ECOM_HOST` / `ECOM_PORT` | `app.host` / `app.port` |
| `ECOM_STATIC_DIR` | `app.static_dir` |
| `ECOM_RAG_PERSIST_DIR` / `ECOM_RAG_COLLECTION` | `rag.*` |

**密钥用环境变量，不要写进配置文件。** `config.local.yaml` 里的 `llm.api_key`
默认是空字符串；填了也不会被读取以外的特殊处理，但文件本身容易泄漏。
推荐只在启动时通过 `DEEPSEEK_API_KEY` 或 `ECOM_LLM_API_KEY` 注入。

**默认账号密码是演示用途**（`admin/admin123`、`analyst/analyst123`），
部署到任何可被他人访问的环境前请先改掉；`jwt_secret` 也请重新生成。

**切换到 MySQL 8**：改 `database.url` 一行即可，代码无需改动。

```yaml
database:
  url: mysql+pymysql://root:password@127.0.0.1:3306/ecom?charset=utf8mb4
```

> 注意：`mysqld` 的数据目录**不能放在含中文的路径**下（会启动即退出且无日志）。

**用本地其他 OpenAI 兼容服务**：

```yaml
llm:
  base_url: http://127.0.0.1:11434/v1
  model: qwen2.5:7b
  api_key: not-needed
```

---

## 项目结构

```
backend/
  run.py                    启动入口
  requirements.txt          依赖（mcp 必须 <2）
  config.py                 配置加载（yaml → env 覆盖）
  app/
    main.py                 应用装配：/api → /mcp → 静态 + SPA
    db.py models.py schemas.py security.py deps.py utils.py constants.py
    seed.py                 确定性种子数据（含不变量 assert）
    routers/                11 个业务路由模块
    agent/                  LangGraph 状态图、SQL 守卫、意图路由、动作白名单、确认存储
    rag/                    embedding / store / ingest
    mcp_server/server.py    FastMCP + 3 个工具（含进程内降级）
    static/                 前端构建产物（build 生成）
  models/bge-small-zh/      ONNX 模型（不入库）
  scripts/                  fetch_model / make_local_config / check_embedding
  tests/                    pytest
frontend/
  rollup.config.mjs         Rollup + vue + 自写 css-inject
  scripts/build.mjs         同进程打包 + 拷 index.html
  scripts/smoke.mjs         jsdom 渲染断言
  src/                      main.js App.vue router api stores components views styles
docs/
  api-contract.md           前后端接口契约（冻结）
  design-notes.md           设计取舍与踩坑记录
  reference/                参考站截图
```

---

## Agent 工作流

```
question
   │
   ▼
┌─────────┐   规则（非 LLM）判断
│  route  │───┬── 明确商品/订单号/统计口径 ──► structured
└─────────┘   ├── 模糊评价主题 ─────────────► semantic
              └── 都没命中 ─────────────────► undetermined
   │
   ▼
┌──────────────┐   MCP 工具调用
│   analysis   │──► query_mysql（SQL 守卫 + 白名单 + LIMIT + 超时 + 重试）
│ 查询分析 Agent│──► search_reviews（Chroma 余弦检索）
└──────────────┘──► list_recent_runs
   │
   ▼
┌──────────────┐   风险判定（差评数 / 均分）
│    report    │──► 低风险：自动保存报告
│ 报告决策 Agent│──► 高风险：规划 3 类动作 ──► interrupt() 等人工确认
└──────────────┘
   │
   ▼
┌──────────────┐
│   confirm    │   approve → 执行白名单动作；reject → 全部跳过
└──────────────┘
```

**动作白名单（只有这 3 类可写）**：

1. `save_diagnosis_report` 保存诊断报告
2. `send_alert_notification` 发送站内预警
3. `mark_product_pending_review` 标记商品待人工复核

商品价格 / 库存 / 上下架状态、订单状态**一律只读**。

---

## 接口

完整契约见 [`docs/api-contract.md`](docs/api-contract.md)。核心端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET | `/api/dashboard/overview` | 首页 8 KPI + 趋势 + 分布 |
| POST | `/api/dashboard/inspect` | 一键巡检 |
| POST | `/api/chat/stream` | **SSE** 对话（`meta/step/token/interrupt/done`） |
| POST | `/api/chat/confirm` | 确认/取消挂起的动作 |
| GET | `/api/runs` `/api/runs/{id}` | 运行历史与逐步耗时 |
| POST | `/api/runs/{id}/feedback` | 👍/👎 |
| GET | `/api/reports` `/api/reports/{id}` | 诊断报告 |
| GET | `/api/reports/notifications` | 站内预警 |
| GET | `/api/observability/overview` | 观测看板 |
| GET | `/api/semantic/search?q=` | 语义检索 |
| GET | `/api/products` `/orders` `/reviews` `/users` | 数据管理 |
| GET | `/api/health` | 健康检查 |

MCP 服务挂在 `/mcp`（streamable-http），工具：`query_mysql` / `search_reviews` / `list_recent_runs`。

---

## 测试

```bash
# 后端单元/接口测试（209 项：种子不变量 + CRUD + SQL 守卫 + 意图路由 + 动作白名单 + SSE）
.venv/Scripts/python -m pytest backend/tests

# 前端渲染冒烟（jsdom 加载构建产物并在假后端上断言页面内容）
cd frontend && node scripts/smoke.mjs

# 端到端验收（96 项断言，真实 HTTP + 真实 LLM + 真实向量库）
python scripts/e2e_all.py
```

> 跑 pytest **不要再加 `-q`**：`backend/pytest.ini` 的 `addopts` 里已经有 `-q`，
> 再叠一个会变成 `-qq` 而把 `N passed` 汇总行一起吞掉，只剩进度点。

测试用的 SQLite **和向量库都是临时的**（`conftest.py` 设 `ECOM_DB_URL` 与
`ECOM_RAG_PERSIST_DIR`，并在播种后自己灌 66 条文档），不会读写
`backend/data/`。这一点很关键：早先只隔离了 SQLite，语义检索类用例会隐式依赖
开发目录里的向量数据，清库后就会莫名失败（见 `docs/design-notes.md` §10）。

`scripts/e2e_all.py` 会**重建种子库 → 起服务 → 跑检查 → 停服务**，覆盖 11 个段落：
登录、首页 8 KPI、四表 CRUD、观测看板、语义检索四快捷词、对话 SSE 全链路、
运行历史与反馈、诊断报告与通知、高风险确认流（approve / reject）、静态资源与 SPA。

> 它断言的是**种子库不变量**（20 运行 / 12 成功 / 70.6% / 21.8s / 路由 9-6-5）。
> 跑一次对话就会往 `agent_runs` 加记录而破坏这些数字，所以必须先重建库——
> 这也是脚本自己会删 `backend/data/ecom.db*` 与 `backend/data/chroma/` 的原因。
> **不要把它指向正式库。** 只想单独跑检查（服务已起）时用
> `python scripts/e2e_check.py --base http://127.0.0.1:8000`。

---

## 已知限制

* **MySQL 未作为默认库**：`mysqld` 的数据目录不能放在含中文的路径下，本机受限。
  代码按 MySQL 写，改配置一行即可切换。
* **MCP 用 streamable-http 而非 stdio**：Windows 沙箱下 asyncio 无法创建子进程管道
  （`WinError 5`）。stdio 客户端不可用，已提供进程内降级。
* **`only_mine` 参数被忽略**：`agent_runs` 表没有 `user_id` 字段，接口接受参数但不做过滤。
* **确认检查点在内存**：这是课程明确要求的行为，进程重启后挂起的确认会失效（返回 409/410）。
* **前端构建较慢**（45–70s）且产物较大（约 6.7MB）：Rollup 全量打包 Element Plus + ECharts，
  未做代码分割。
* **受限环境下会自动改临时目录**：Chroma 的 Rust 绑定依赖 `std::env::temp_dir()`，
  而本机 `%LOCALAPPDATA%\Temp` 存在却不可写（`icacls` 显示正常，实际 `WinError 5`），
  会让它报 `(code: 14) unable to open database file`。`backend/config.py` 的
  `ensure_writable_tempdir()` 会探测并改指 `backend/data/tmp`，无需手工处理；
  机制细节见 [`docs/design-notes.md` §3](docs/design-notes.md)。

更多设计取舍见 [`docs/design-notes.md`](docs/design-notes.md)。

