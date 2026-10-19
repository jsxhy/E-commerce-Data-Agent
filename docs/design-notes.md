# 设计说明（design-notes）

本文记录复刻这套「电商数据分析 Agent」时的关键取舍与踩坑，**每一条都有实测依据**，
方便后来者理解「为什么是这样实现」而不是另一种更常见的写法。

---

## 1. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 浏览器（Vue 3 + Element Plus + ECharts，Rollup 打包）          │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼──────────────────────────────────┐
│ FastAPI（backend/app/main.py）                                │
│  ├─ /api/auth /dashboard /products /orders /reviews /users    │
│  ├─ /api/chat（SSE）/runs /reports /observability /semantic    │
│  ├─ /mcp  ← MCP 服务（streamable-http）                        │
│  └─ /  + /assets  ← 前端静态产物                               │
└──────┬──────────────────┬──────────────────┬──────────────────┘
       │                  │                  │
  ┌────▼────┐      ┌──────▼──────┐    ┌──────▼──────┐
  │ SQLite  │      │   Chroma    │    │ LangGraph   │
  │ (SQLAlchemy) │  │ bge-small-zh│    │ 状态图       │
  └─────────┘      └─────────────┘    └──────┬──────┘
                                             │ MCP Client
                                        ┌────▼─────┐
                                        │ 3 个工具  │
                                        └──────────┘
```

三条主链路：

1. **对话链路**：`POST /api/chat/stream` → LangGraph 状态图（route → analysis → report → confirm）→
   SSE 推 `meta / step / token / interrupt / done`。
2. **工具链路**：LangGraph 的查询节点通过 **MCP Client** 调 3 个工具
   （`query_mysql` / `search_reviews` / `list_recent_runs`），而不是直接 import 业务代码。
3. **动作链路**：高风险时图在 `confirm` 节点 `interrupt()` 挂起，前端确认后
   `POST /api/chat/confirm` 用 `Command(resume=...)` 恢复，只执行白名单里的 3 类动作。

---

## 2. 数据库为什么是 SQLite 而不是 MySQL 8

课程要求 MySQL 8，本机也确实装了 MySQL 8.4.2，但**在这个工作区里跑不起来**，实测结论：

| 尝试 | 结果 |
|---|---|
| 启动系统服务 `MySQL84` | `无法打开计算机'.'上的 MySQL84 服务`（无管理员权限） |
| 用系统数据目录 | `C:\ProgramData\MySQL\MySQL Server 8.4\Data` 访问被拒绝 |
| `mysqld --initialize-insecure --datadir=<工作区>` | exit 0，但启动即退出，日志为空 |
| `mysqld ... --datadir=%TEMP%\mysqlprobe\data` | **成功**，`ready for connections ... port: 3307` |

结论：**mysqld 的数据目录不能放在含中文的路径下**（工作区是 `D:\code\ai\练习\大项目`），
而沙箱只允许写工作区，`%TEMP%` 里的实例既不可持久、也不属于交付物。

因此默认用 **SQLite**（WAL 模式），但**代码层完全按 MySQL 写**：

* SQLAlchemy 2.x ORM，不依赖任何 SQLite 方言特性；
* 金额一律用整数分（`price_cents` / `amount_cents`），出口才转元；
* 唯一索引、外键级联、`UniqueConstraint("run_id", "seq")` 都显式声明。

**切回 MySQL 只需改一行配置**：

```yaml
database:
  url: mysql+pymysql://root:password@127.0.0.1:3306/ecom?charset=utf8mb4
```

`backend/app/db.py::_prepare_url()` 会自动识别非 SQLite 的 URL，走
`pool_pre_ping=True, pool_recycle=1800` 的连接池参数。

---

## 3. Embedding 为什么自己写（不用 Chroma 默认）

Chroma 默认的 `ONNXMiniLM_L6_V2`（all-MiniLM-L6-v2）是**英文模型**，对中文几乎无效，实测：

| 查询 | 文档 | 距离 |
|---|---|---|
| 鞋子开胶 | 跑了两次鞋底就开胶了，质量太差 | **0.981**（比无关文档还远） |
| 鞋子开胶 | 音质不错，戴着舒服 | 0.595 |
| 耳机有杂音 | 耳机有杂音，左耳滋滋响 | 0.169 |

用 0.5 阈值的话，中文语义检索基本全军覆没。

改用 **`BAAI/bge-small-zh-v1.5` 的 ONNX 版**（94.8MB），自己用 `onnxruntime` + `tokenizers`
实现 `encode()`：

* 分词：`tokenizers` 加载 `tokenizer.json`，`padding` + `truncation` 到 128；
* 前向：输入 `input_ids / attention_mask / token_type_ids`，取 `last_hidden_state`；
* 池化：**CLS pooling**（取 `[0]`，bge 系列的标准做法），再 **L2 归一化**；
* 输出 512 维。

阈值校准实测（4 个业务查询 × 10 条文档）：

| | 最小值 | 均值 | 最大值 |
|---|---|---|---|
| 正样本（相关） | 0.238 | 0.322 | **0.427** |
| 负样本（不相关） | 0.115 | 0.511 | 0.723 |

→ **0.5 阈值语义成立**（正样本全部 ≤ 0.43，负样本均值 0.51）。
已知噪声：「洗了缩水」对「面料舒服」距离 0.403 属假阳性，可接受。

### 模型文件从哪来

`huggingface.co` 直连超时（10060），必须走 **`hf-mirror.com`**，且：

* **必须带浏览器 UA**，否则 403；
* `urllib.request.urlretrieve` 会 403，得自己 `Request` + 分块写盘；
* 官方 `HEAD` 请求不可用，探测要用 `GET`。

这些细节都固化在 `backend/scripts/fetch_model.py` 里（含大小校验、镜像回退）。

### 附带修掉的一个 Chroma 陷阱：`(code: 14) unable to open database file`

在本机受限环境里 Chroma **完全起不来**，报：

```
chromadb.errors.InternalError: error returned from database: (code: 14) unable to open database file
```

栈底在 `chromadb/api/rust.py:122` 的 `chromadb_rust_bindings.Bindings(...)`。
排查过程与结论：

* **不是磁盘/权限/路径问题**：同一个目录下 `sqlite3.connect()` 建表写库完全正常，
  `backend/data/chroma/chroma.sqlite3` 能写出 20480 字节。
* 读 Chroma 源码确认 Rust 绑定建库时用的是 Rust 的 `std::env::temp_dir()`
  （Windows 顺序 `TMP` → `TEMP` → `USERPROFILE`），与 `persist_dir` 无关。
* 决定性对照实验（同一库路径，只改 `TEMP`）：
  `TEMP=C:\Users\...\AppData\Local\Temp` → **FAIL**；
  `TEMP=<工作区内任意可写目录>` → **OK**。
* 根因：`%LOCALAPPDATA%\Temp` **存在但不可写**（Python 侧 `PermissionError [WinError 5]`），
  而 `icacls` 显示的却是 `(I)(OI)(CI)(F)` —— **ACL 会骗人**。Chroma 只看
  「temp_dir 存在」就往下走，于是以 code 14 失败；SQLite 自己写库却不受影响，
  所以这个错看起来完全不像是临时目录引起的。

修法（已落盘）：`backend/config.py` 增加 `_dir_is_writable()`（真写一个探针文件再删，
不信权限位）与 `ensure_writable_tempdir()`（`TMP`/`TEMP` 不可写时改指
`backend/data/tmp`）；`backend/app/rag/store.py` 在 **`import chromadb` 之前**调用它。
对应测试在 `backend/tests/test_config.py`（9 例）。

---

## 4. MCP 为什么是 streamable-http 而不是 stdio

课程要求「MCP Client（stdio）」，本机实测**stdio 不可用**：

* `asyncio` 在 Windows 上创建子进程管道时失败：
  `PermissionError: [WinError 5] 拒绝访问` in `asyncio/windows_utils.py:63 pipe()`；
* 沙箱禁止命名管道，`stdio_client` 起不来。

**streamable-http 完全可用**，且更贴近生产部署。挂载有三个必须同时满足的点
（少一个就报 `McpError: Session terminated` 或 307 重定向）：

```python
mcp = FastMCP("ecom-tools", streamable_http_path="/")   # ① path 必须是 "/"
app.mount("/mcp", mcp.streamable_http_app())            # ② 挂在 /mcp
# ③ lifespan 里必须跑 session manager
async with mcp.session_manager.run():
    yield
```

客户端连 `http://127.0.0.1:8000/mcp/`（注意结尾斜杠）。

`backend/app/mcp_server/server.py::call_tool()` 还实现了**进程内降级**：
MCP 调用异常时直接调用同名 Python 函数（`call_tool_inprocess`），
保证「工具链路挂了但对话还能用」，并在返回体里标 `transport: mcp | inprocess`。

---

## 5. LangGraph 的确认检查点为什么在内存

课程明确要求「确认检查点保存在内存，重启失效」。实现用 `MemorySaver()`：

```python
graph.compile(checkpointer=MemorySaver())
config = {"configurable": {"thread_id": f"run-{run_id}"}}
```

* `interrupt({...})` 挂起后，`ainvoke` 返回含 `__interrupt__`；
  `aget_state(cfg).next == ('confirm',)`；
* 恢复用 `ainvoke(Command(resume="approve" | "reject"), cfg)`；
* 恢复后 `next == ()`。

**重启即失效**是刻意行为，不是缺陷：`confirm_store` 在找不到 thread 时返回
`409/410`，前端提示「确认已过期，请重新提问」。

并发保护：`confirm_store` 用进程内锁 + 状态机（`pending → approved/rejected → executed`），
同一个 `run_id` 只允许被确认一次，重复提交返回 409。

---

## 6. 前端为什么用 Rollup 而不是 Vite

本机**所有 Node 子进程都被沙箱拒绝**：

* `vite` 依赖 esbuild，esbuild 启动 service 时 `spawn EPERM`；
* `pnpm install` 的 postinstall 也 `spawn EPERM`，必须 `--ignore-scripts`。

所以：

* 打包器用 **Rollup**（纯 JS，同进程运行，不 spawn）；
* `rollup.config.mjs` 里自己写了个 **css-inject 插件**，把 SFC `<style>` 与
  `.css` 模块转成运行时 `document.createElement('style')` 注入 ——
  这样就不需要 `rollup-plugin-postcss`（它的依赖链在本机装不全）；
* `package.json` 的 `build` 脚本调 `scripts/build.mjs`，**用 Rollup 的 JS API
  在同进程里完成打包 + 拷贝 index.html**，避免 `rollup -c && cp` 这种需要两次 spawn 的写法。

产物约 6.7MB（Element Plus 全量 + ECharts 全量内联 CSS），构建 45–70s。

**依赖装不上的两个坑**（npmmirror 上不存在对应版本）：
`@rollup/plugin-node-resolve@16.0.4`、`@rollup/plugin-replace@6.0.4`、`axios@1.14.2`
→ 分别钉到 `16.0.3` / `6.0.3` / `1.20.0`。
另外必须 `npm_config_node_linker=hoisted`（pnpm 默认 symlink 布局下 ESM 解析会失败）。

---

## 7. 视觉验收为什么用 jsdom 而不是截图

Chrome headless 在本沙箱**无法启动**：

```
FATAL:mojo...platform_channel.cc:112 Check failed: 拒绝访问 (0x5)
```

沙箱禁管道，Chromium 的 mojo IPC 起不来。

替代方案 `frontend/scripts/smoke.mjs`：用 **jsdom** 加载构建产物，
桥接 `window/document/navigator/SVGElement/...`，并给 `HTMLCanvasElement.getContext`
打一个只够 ECharts 跑完 `setOption` 的 Proxy 桩，然后断言：

* 无 token → 渲染登录页；
* 有 token → 渲染侧边栏 + 10 个菜单项 + 观测看板 KPI；
* 路由切到 `/semantic` → 渲染检索页与快捷词。

`node scripts/smoke.mjs` → `[smoke] PASS`。

---

## 8. 动作边界（只读 vs 可写）

课程要求「商品价格/库存/上下架、订单状态只读；只有 3 类动作可写」。
实现分两层防：

1. **白名单**：`constants.ALLOWED_ACTIONS` 只有
   `save_diagnosis_report` / `send_alert_notification` / `mark_product_pending_review`，
   `agent/actions.py::execute()` 对非白名单动作直接拒绝并记 `skipped`；
2. **SQL 守卫**：`agent/sql_guard.py` 解析 SQL 语句类型，只放行 `SELECT`；
   表白名单来自 `config.agent.allowed_tables`；强制加 `LIMIT`（`sql_max_rows`），
   超时 `sql_timeout_seconds`，执行错误反馈给模型重试 `sql_max_retries` 次。

`READ_ONLY_RESOURCES` 在 `constants.py` 里显式列出，供 `/api/dashboard/inspect`
与 Agent 共用同一份定义，避免两处漂移。

---

## 9. 种子数据为什么是「算出来的」

首页与观测看板的 KPI 是**实时聚合**的，但参考站的数字是固定的
（49 单 / ¥7081.80 / 均分 3.02 / 成功率 70.6% / 阶段均值 21.8s…）。
为了让两边同时成立，种子数据必须满足一组联立约束：

| 约束 | 解法 |
|---|---|
| 46 条评价，星级 `{5:10,4:9,3:9,2:8,1:10}` → 均分 3.02 | 直接给定分布（139 分 / 46 = 3.0217） |
| 48 笔已支付订单合计**恰好 708180 分** | 用 DP/局部搜索求每商品订单条数与件数组合，最终解：条数 `[3,3,3,3,2,3,2,3,2,2,2,3,3,2,2,2,2,2,2,2]`，件数 `[1,1,1,2,1,1,1,1,1,1,1,2,2,1,1,1,1,1,1,2]` |
| 12 条成功运行平均耗时**恰好 21800ms** | 给定 12 个值，均值精确等于 21800 |
| 阶段平均耗时固定（数据查询 11.4s、多角色分析 2.8s…） | 所有运行共用同一套 `RunStep` 模板，每阶段耗时写死 |
| 路由分布 9/6/5、状态 12/5/3、报告 4 high、通知 2 未读 | 直接按数量构造 |

`seed_all()` 里对「已支付合计」和「订单条数」有 `assert`，
改坏任何一处都会在灌数据时立刻失败，不会静默漂移。

时间全部相对「今天零点」生成，因此**任何时候启动都有近 30 天趋势与近 14 天柱状图**，
不会因为项目放久了变成一片空白。

---

## 10. 测试为什么必须自带向量库

`tests/conftest.py` 除了把 `ECOM_DB_URL` 指向临时 SQLite，还必须把
`ECOM_RAG_PERSIST_DIR` 指向临时 Chroma，**并在播种后自己灌一遍 `ingest_all()`**。

原因是一次真实的踩坑：早先 conftest 只隔离了 SQLite，`rag.persist_dir` 仍取配置里的
`backend/data/chroma`。在 Chroma 修好之前它以 `EmbeddingUnavailable` 降级，恰好因为
开发目录里留着服务先前灌入的 66 条文档，让 `route=semantic` 的问题仍能检索到证据，
用例看起来是绿的；等 Chroma 修好、开发库被重建清空，同一批用例立刻变成
「证据不足 → 不规划动作 → 没有 interrupt」，`test_confirm_reject_skips_everything`
以 `IndexError` 稳定失败 3/3。

**教训**：测试隐式依赖「开发目录里恰好有数据」是最难查的一类假绿 ——
它不报错，只在别人清库时炸。凡是「证据驱动」的逻辑（向量检索、文件缓存、
模型权重），测试都必须自建数据，不能借用运行时目录。

> 顺带：`pytest.ini` 里已有 `addopts = -q`，命令行再叠一个 `-q` 会变成 `-qq`
> 而**把汇总行一起吞掉**（只留进度点）。要看 `N passed` 就直接跑 `pytest tests`，
> 不要再加 `-q`。

---

## 11. 已知取舍与未实现项

| 项 | 说明 |
|---|---|
| `only_mine` 参数 | `AgentRun` 表没有 `user_id`，接口接受该参数但**忽略**，返回全量。要真正支持需加列并迁移。 |
| `embedding_mode` 的 `unavailable` | 契约原写两种枚举，实现补了第三种降级态（模型/向量库不可用）。已回写契约 §8。 |
| MySQL | 代码按 MySQL 写，但默认跑 SQLite（见 §2）。 |
| MCP stdio | 保留 `call_tool_inprocess` 降级；stdio 传输在本机不可用（见 §4）。 |
| 截图验收 | Chrome headless 不可用，改用 jsdom 断言（见 §7）。 |
| 真实 LLM 依赖 | 对话链路默认走 DeepSeek；无 key 时 `llm.py` 会返回明确的降级回答，不 500。 |

---

## 12. 目录速查

```
backend/
  run.py                    启动入口（uvicorn 包装）
  requirements.txt          钉版本依赖（mcp 必须 <2）
  pytest.ini
  config.py                 配置加载（yaml → env 覆盖）
  app/
    main.py                 create_app()：路由 → /mcp → 静态 + SPA
    db.py  models.py  schemas.py  security.py  deps.py  utils.py  constants.py
    seed.py                 确定性种子数据（含不变量 assert）
    routers/                auth dashboard products orders reviews users
                            chat runs reports observability semantic
    agent/                  state llm sql_guard intent actions confirm_store graph
    rag/                    embedding store ingest
    mcp_server/server.py    FastMCP + 3 个工具 + 进程内降级
    static/                 前端构建产物
  models/bge-small-zh/      ONNX 模型（90MB，不入库）
  scripts/                  fetch_model / make_local_config / check_embedding
  tests/                    pytest（不变量 + CRUD + SQL 守卫 + 意图 + 动作 + SSE）
frontend/
  rollup.config.mjs         Rollup + vue + 自写 css-inject
  scripts/build.mjs         同进程打包 + 拷 index.html
  scripts/smoke.mjs         jsdom 渲染断言
  src/                      main.js App.vue router api stores components views styles
docs/
  api-contract.md           前后端冻结契约
  design-notes.md           本文
  reference/                参考站截图
```
