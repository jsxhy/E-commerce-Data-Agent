# 前后端接口契约（冻结版 v1）

> 本文件是前后端并行开发的唯一依据。字段名、层级、类型一经写定，双方都不得单方面修改；
> 需要变更时先改本文件再改代码。

## 0. 通用约定

- 基址：`/api`。前端由 FastAPI 单端口托管，开发/生产都用相对路径 `/api/...`。
- 鉴权：除 `POST /api/auth/login` 外，全部接口需 `Authorization: Bearer <token>`。
  未登录返回 HTTP 401，body `{"code":401,"message":"未登录或登录已过期","data":null}`。
- 统一响应包裹：

```json
{ "code": 0, "message": "ok", "data": <payload> }
```

  `code=0` 表示成功；非 0 为业务错误（HTTP 状态码同步为 400/401/403/404/500）。
- 分页 payload 统一：

```json
{ "items": [ ... ], "total": 49, "page": 1, "size": 20 }
```

- 时间：ISO 8601 本地时间字符串，精确到秒，如 `"2026-10-19 11:38:26"`（前端直接显示，不再解析）。
  时间戳型字段（毫秒）单独命名为 `*_ms`。
- 金额：接口出口一律是**元**（浮点，两位小数），如 `7081.8`；数据库内部存分。
- 本文档中的**样例数值仅作字段形状示意**，真实值由种子数据与实时查询决定，
- 枚举值统一小写下划线：`route` ∈ `structured | semantic | undetermined`；
  `status` ∈ `success | failed | pending | cancelled`；`risk` ∈ `high | low`；
  `source` ∈ `review | product`。

---

## 1. 鉴权

### POST /api/auth/login
请求：`{ "username": "admin", "password": "admin123" }`
响应 data：
```json
{ "token": "eyJ...", "user": { "id": 1, "username": "admin", "display_name": "管理员", "role": "admin" } }
```
失败：`{"code":401,"message":"用户名或密码错误","data":null}`

### GET /api/auth/me
响应 data：`{ "id": 1, "username": "admin", "display_name": "管理员", "role": "admin" }`

---

## 2. 系统首页

### GET /api/dashboard/overview
响应 data：
```json
{
  "updated_at": "2026-10-19 11:38:26",
  "greeting": "你好，管理员",
  "subtitle": "这是你的电商数据与 Agent 运行概览，可一键巡检商品、订单、评价的健康度。",
  "kpis": {
    "product_count":      { "value": 20,       "label": "全部商品" },
    "order_count":        { "value": 49,       "label": "累计订单" },
    "review_count":       { "value": 46,       "label": "累计评价" },
    "paid_amount":        { "value": 7081.8,   "label": "订单实付合计（不含已取消）" },
    "high_risk_reports":  { "value": 4,        "label": "Agent 判定需人工确认" },
    "unread_alerts":      { "value": 2,        "label": "站内通知" },
    "pending_review":     { "value": 1,        "label": "人工复核状态" }
  },
  "order_trend": {
    "total_orders": 49,
    "total_amount": 7170.8,
    "points": [ { "date": "2026-09-20", "count": 1, "amount": 128.0 } ]
  },
  "rating_dist": {
    "avg": 3.02,
    "buckets": [ { "star": 1, "count": 10 }, { "star": 2, "count": 8 },
                 { "star": 3, "count": 9 }, { "star": 4, "count": 9 },
                 { "star": 5, "count": 10 } ]
  },
  "attention_products": [
    { "product_id": 3, "name": "静音蓝牙耳机", "category": "数码配件",
  ]
}
```
说明：
- `kpis` 的 8 个键名固定，前端按此顺序渲染卡片；每项都有 `value` 与 `label`。
- `order_trend.points` 固定 30 项（近 30 天，含 0 单的日子），`date` 为 `YYYY-MM-DD`。
- `rating_dist.buckets` 固定 5 项，`star` 从 1 到 5。
- `attention_products` 按 `bad_review_count` 降序，最多 5 条；`bad_review_count` = 星级 ≤2 的评价数。

### POST /api/dashboard/inspect
「一键巡检」：触发一次系统级巡检，写入运行记录与诊断报告（高风险项生成预警）。
响应 data：`{ "run_id": 21, "report_count": 2, "alert_count": 1, "message": "巡检完成" }`

---

## 3. 基础数据（商品 / 订单 / 评价 / 用户）

### GET /api/products?page=1&size=20&q=&category=
响应 data（分页）items 每项：
```json
{ "id": 3, "name": "静音蓝牙耳机", "category": "数码配件", "price": 299.0,
  "bad_review_count": 3, "pending_review": false, "created_at": "2026-08-01 10:00:00" }
```
`status` ∈ `on_sale | off_sale`。

### GET /api/orders?page=1&size=20&q=&status=
items 每项：
```json
{ "id": 12, "order_no": "SO20260912001", "product_id": 3, "product_name": "静音蓝牙耳机",
  "quantity": 2, "amount": 598.0, "status": "paid", "buyer": "王**",
  "created_at": "2026-09-12 14:22:03" }
```
`status` ∈ `paid | cancelled | refunded`。

### GET /api/reviews?page=1&size=20&q=&rating=
items 每项：
```json
{ "id": 7, "product_id": 3, "product_name": "静音蓝牙耳机", "rating": 1,
  "content": "戴半小时耳朵疼，降噪也一般。", "buyer": "李**",
  "has_image": true, "image_desc": "耳机与包装盒实拍，充电仓有划痕",
  "created_at": "2026-09-13 09:10:00" }
```

### GET /api/users?page=1&size=20
items 每项：`{ "id":1, "username":"admin", "display_name":"管理员", "role":"admin", "created_at":"..." }`

---

## 4. 数据分析对话（核心）

### POST /api/chat/stream
`Content-Type: application/json`，请求：
```json
{ "question": "静音蓝牙耳机最近差评集中在哪些问题？", "session_id": null }
```
响应：`text/event-stream`，事件序列：

| event | data 结构 | 说明 |
|---|---|---|
| `meta` | `{"run_id": 21, "question": "...", "created_at": "...", "session_id": "s-..."}` | 首帧 |
| `step` | `{"seq":1,"stage":"starting","actor":"","detail":"已接入 LangGraph 分析流程","duration_ms":12}` | 多次，`seq` 从 1 递增 |
| `token` | `{"delta":"文本片段"}` | 多次，拼成最终回答 |
| `interrupt` | `{"actions":[{"type":"save_diagnosis_report","label":"保存诊断报告","summary":"..."}],"risk":"high","message":"检测到高风险，请确认是否执行以下动作"}` | 高风险时出现一次，之后流暂停等待确认（见下） |
| `done` | `{"run_id":21,"answer":"...","route":"structured","status":"success","duration_ms":30019,"awaiting_confirm":false}` | 结束帧（含 `interrupt` 时 `awaiting_confirm=true`） |
| `error` | `{"message":"LLM 调用失败：...","run_id":21}` | 异常帧，其后流结束 |

`step.stage` 取值（固定集合，前端按此配色）：
`starting | scheduling | routing | agent.analysis | querying | deepening | preparing_evidence |
agent.report | deciding | action_pending | executing | finished`

`step.actor` 取值：`""`（系统）｜`查询分析 Agent`｜`报告决策 Agent`｜`调度`。

meta.session_id 为本次会话标识：请求未带 session_id 时服务端生成并回传，
后续请求带上它即可维持多轮上下文（不带也能跑，每轮独立）。

前端渲染规则：过程面板标题 `已处理 · {step 数} 步 · {duration/1000 保留 1 位} 秒`，
正文每行 `<actor> <stage> <detail>`（`actor` 为空时只显示 `stage detail`）。

### POST /api/chat/confirm
```json
{ "run_id": 21, "decision": "approve" }   // approve | reject
```
响应 data：
```json
{ "run_id": 21, "decision": "approve",
  "executed": [ { "type": "save_diagnosis_report", "ok": true, "detail": "已保存诊断报告 #11" } ],
  "skipped": [], "message": "已执行 3 个动作" }
```
错误：
- 该 run 不在等待确认：`{"code":409,"message":"该运行不在等待确认状态","data":null}`
- 重复确认：`{"code":409,"message":"该运行已处理过确认","data":null}`
- 确认检查点重启后失效：`{"code":410,"message":"确认检查点已失效，请重新发起分析","data":null}`

### GET /api/chat/history?limit=20
返回最近对话（用于进入页面时回显）：
```json
{ "items": [ { "run_id": 21, "question": "...", "answer": "...", "route": "structured",
               "created_at": "..." } ] }
```

---

## 5. 运行历史

### GET /api/runs?page=1&size=20&route=&status=
items 每项：
```json
{ "id": 21, "question": "静音蓝牙耳机最近差评集中在哪些问题？", "route": "structured",
  "status": "success", "duration_ms": 30019, "step_count": 16,
  "created_at": "2026-10-19 11:08:07" }
```

### GET /api/runs/{id}
```json
{ "id": 21, "question": "...", "route": "structured", "status": "success",
  "duration_ms": 30019, "created_at": "...",
  "answer": "完整回答（前端只展示前 500 字并提示）",
  "feedback": "up",
  "steps": [ { "seq": 1, "stage": "starting", "actor": "", "detail": "已接入LangGraph分析流程",
               "duration_ms": 12 } ] }
```

### POST /api/runs/{id}/feedback
请求 `{ "value": "up" }`（`up | down`，传 `null` 取消）。
响应 data：`{ "run_id": 21, "feedback": "up" }`

---

## 6. 诊断报告 & 预警通知

### GET /api/reports?page=1&size=10&q=
items 每项：
```json
{ "id": 11, "question": "静音蓝牙耳机最近差评集中在哪些问题？", "product_id": 3,
  "product_name": "静音蓝牙耳机", "risk": "high", "evidence_count": 5,
  "summary": "差评集中在佩戴舒适度与降噪效果，建议人工复核。",
  "created_at": "2026-10-19 11:08:37" }
```

### GET /api/reports/{id}
在上面的字段外增加：
```json
{ "evidence": [ { "source": "review", "ref_id": 7, "rating": 1, "snippet": "...",
                  "distance": 0.213 } ],
  "actions": [ { "type": "send_alert_notification", "label": "发送站内预警",
                 "status": "executed" } ] }
```

### GET /api/reports/notifications?unread_only=false
```json
{ "items": [ { "id": 1, "title": "高风险商品预警", "content": "静音蓝牙耳机 近 7 天差评 3 条",
               "level": "high", "read": false, "product_id": 3,
               "created_at": "2026-10-19 11:08:37" } ],
  "unread_count": 2 }
```
`level` ∈ `high | info`。

### POST /api/reports/notifications/{id}/read
响应 data：`{ "id": 1, "read": true, "unread_count": 1 }`

---

## 7. 观测看板

### GET /api/observability/overview?only_mine=false
```json
{
  "updated_at": "2026-10-19 11:38:26",
  "kpis": {
    "total_runs":     { "value": 20,   "label": "含进行中与已取消" },
    "success_runs":   { "value": 12,   "label": "正常跑完" },
    "failed_runs":    { "value": 5,    "label": "异常终止" },
    "cancelled_runs": { "value": 0,    "label": "用户放弃或中断" },
    "unfinished_runs":{ "value": 3,    "label": "进行中/待确认" },
    "success_rate":   { "value": 70.6, "label": "已结束的运行里成功的比例", "unit": "%" },
    "avg_duration":   { "value": 21.8, "label": "仅统计成功运行", "unit": "s" },
    "like_rate":      { "value": 100,  "label": "赞1 / 踩0", "unit": "%" }
  },
  "route_dist": [ { "route": "structured", "name": "结构化", "count": 9 },
                  { "route": "semantic",   "name": "语义检索", "count": 6 },
                  { "route": "undetermined","name": "未判定",  "count": 5 } ],
  "stage_avg": [ { "stage": "数据查询", "avg_ms": 11400 },
                 { "stage": "多角色分析", "avg_ms": 2800 } ],
  "daily_runs": { "total": 20, "points": [ { "date": "2026-10-06", "count": 1 } ] }
}
```
- `stage_avg` 按 `avg_ms` 降序，最多 8 条，中文阶段名。
- `daily_runs.points` 固定 14 项。
- `kpis.*.value` 是数字；`unit` 缺省表示无单位。

---

## 8. 语义检索

### GET /api/semantic/search?q=跑鞋脱胶&top_k=5
```json
{
  "query": "跑鞋脱胶",
  "top_k": 5,
  "hit_count": 5,
  "collection_size": 66,
  "embedding_mode": "bge-small-zh-onnx",
  "threshold": 0.5,
  "items": [
    { "rank": 1, "source": "review", "ref_id": 7, "product_id": 3,
      "product_name": "轻量缓震跑鞋", "rating": 1,
      "snippet": "跑了两次鞋底就开胶了……", "distance": 0.213, "relevant": true }
  ]
}
```
- 空 `q` 返回 `{"code":400,"message":"请输入检索内容","data":null}`。
- `distance` 为余弦距离（0~2），保留 3 位小数；`relevant = distance <= threshold`。
- `embedding_mode` ∈ `bge-small-zh-onnx | chroma-default | unavailable`。
  取 `unavailable` 表示本机 embedding 模型或向量库不可用（降级态）：此时
  **HTTP 200 + `code=0`**，`hit_count=0`、`collection_size=0`、`items=[]`，
  界面提示「向量检索不可用」而不是报错。

---

## 9. 健康检查

### GET /api/health
`{ "status": "ok", "db": "ok", "vector": "ok", "llm": "configured", "version": "1.0.0" }`

---

## 10. 前端路由与页面

| 路径 | 页面 | 依赖接口 |
|---|---|---|
| `/login` | 登录 | auth/login |
| `/` | 系统首页 | dashboard/overview, dashboard/inspect |
| `/chat` | 数据分析对话 | chat/stream, chat/confirm, chat/history |
| `/runs` | 运行历史 | runs, runs/{id}, runs/{id}/feedback |
| `/reports` | 诊断报告 | reports, reports/{id}, reports/notifications |
| `/observability` | 观测看板 | observability/overview |
| `/products` | 商品数据 | products |
| `/orders` | 订单数据 | orders |
| `/reviews` | 评价数据 | reviews |
| `/semantic` | 语义检索 | semantic/search |
| `/users` | 用户管理 | users |

侧边栏顺序即上表顺序（登录除外）。
