"""跨模块共享的常量：阶段键、阶段中文名、路由/状态枚举、动作白名单。

Agent 图、SSE 推送、观测看板、种子数据都从这里取，避免各处写死字符串。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 运行阶段
# 与 docs/api-contract.md §4 的 step.stage 集合一致（新增阶段须同步契约）
STAGE_STARTING = "starting"
STAGE_SCHEDULING = "scheduling"
STAGE_ROUTING = "routing"
STAGE_AGENT_ANALYSIS = "agent.analysis"
STAGE_QUERYING = "querying"
STAGE_DEEPENING = "deepening"
STAGE_PREPARING_EVIDENCE = "preparing_evidence"
STAGE_AGENT_REPORT = "agent.report"
STAGE_DECIDING = "deciding"
STAGE_ACTION_DECISION = "action_decision"
STAGE_ACTION_PENDING = "action_pending"
STAGE_EXECUTING = "executing"
STAGE_FINISHED = "finished"

STAGE_ORDER: list[str] = [
    STAGE_STARTING, STAGE_SCHEDULING, STAGE_ROUTING, STAGE_AGENT_ANALYSIS,
    STAGE_QUERYING, STAGE_DEEPENING, STAGE_PREPARING_EVIDENCE, STAGE_AGENT_REPORT,
    STAGE_DECIDING, STAGE_ACTION_DECISION, STAGE_ACTION_PENDING, STAGE_EXECUTING,
    STAGE_FINISHED,
]

# 观测看板「各阶段平均耗时」用的中文名
STAGE_LABELS: dict[str, str] = {
    STAGE_STARTING: "流程启动",
    STAGE_SCHEDULING: "调度",
    STAGE_ROUTING: "意图路由",
    STAGE_AGENT_ANALYSIS: "多角色分析",
    STAGE_QUERYING: "数据查询",
    STAGE_DEEPENING: "语义深挖",
    STAGE_PREPARING_EVIDENCE: "证据整合",
    STAGE_AGENT_REPORT: "报告决策",
    STAGE_DECIDING: "风险判断",
    STAGE_ACTION_DECISION: "动作决策",
    STAGE_ACTION_PENDING: "等待确认",
    STAGE_EXECUTING: "动作执行",
    STAGE_FINISHED: "收尾",
}

# ---------------------------------------------------------------- Agent 角色
ACTOR_SYSTEM = ""
ACTOR_SCHEDULER = "调度"
ACTOR_ANALYSIS = "查询分析 Agent"
ACTOR_REPORT = "报告决策 Agent"

# ---------------------------------------------------------------- 路由
ROUTE_STRUCTURED = "structured"
ROUTE_SEMANTIC = "semantic"
ROUTE_UNDETERMINED = "undetermined"

ROUTE_LABELS: dict[str, str] = {
    ROUTE_STRUCTURED: "结构化",
    ROUTE_SEMANTIC: "语义检索",
    ROUTE_UNDETERMINED: "未判定",
}

# ---------------------------------------------------------------- 运行状态
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_CANCELLED = "cancelled"

# ---------------------------------------------------------------- 风险
RISK_HIGH = "high"
RISK_LOW = "low"

# ---------------------------------------------------------------- 动作白名单（仅这三类可写）
ACTION_SAVE_REPORT = "save_diagnosis_report"
ACTION_SEND_ALERT = "send_alert_notification"
ACTION_MARK_PENDING = "mark_product_pending_review"

ACTION_LABELS: dict[str, str] = {
    ACTION_SAVE_REPORT: "保存诊断报告",
    ACTION_SEND_ALERT: "发送站内预警",
    ACTION_MARK_PENDING: "标记商品待人工复核",
}

ALLOWED_ACTIONS: tuple[str, ...] = (ACTION_SAVE_REPORT, ACTION_SEND_ALERT, ACTION_MARK_PENDING)

# 只读边界：这些字段/操作任何情况下都不允许 Agent 写入
READ_ONLY_RESOURCES: tuple[str, ...] = (
    "products.price", "products.stock", "products.status", "orders.status",
)
