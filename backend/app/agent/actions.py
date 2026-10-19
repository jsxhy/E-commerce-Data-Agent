"""动作规划与执行。

**只允许三类写操作**（见 ``constants.ALLOWED_ACTIONS``）：

- ``save_diagnosis_report``    → 写 ``diagnosis_reports`` 一行
- ``send_alert_notification``  → 写 ``notifications`` 一行（level=high）
- ``mark_product_pending_review`` → 把 ``products.pending_review`` 置 True

绝对不碰：商品价格 / 库存 / 上下架状态、订单状态。``execute_actions`` 对每个 action
的 type 做白名单断言，非白名单直接抛 ``ValueError``（不是返回错误结果——这是编程错误，
不是业务失败）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from backend.app.constants import (
    ACTION_LABELS,
    ACTION_MARK_PENDING,
    ACTION_SAVE_REPORT,
    ACTION_SEND_ALERT,
    ALLOWED_ACTIONS,
    READ_ONLY_RESOURCES,
    RISK_HIGH,
    RISK_LOW,
)
from backend.app.models import DiagnosisReport, Notification, Product
from backend.config import get_config

logger = logging.getLogger(__name__)


class ActionNotAllowed(ValueError):
    """动作不在白名单内（编程错误，必须抛出而不是静默跳过）。"""


def _allowed_actions() -> tuple[str, ...]:
    """配置与常量取交集：配置只能收紧，不能放宽。"""
    configured = get_config()["agent"].get("allowed_actions") or list(ALLOWED_ACTIONS)
    allowed = tuple(a for a in configured if a in ALLOWED_ACTIONS)
    return allowed or ALLOWED_ACTIONS


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {
        "source": getattr(item, "source", None),
        "ref_id": getattr(item, "ref_id", None),
        "product_id": getattr(item, "product_id", None),
        "product_name": getattr(item, "product_name", None),
        "rating": getattr(item, "rating", None),
        "snippet": getattr(item, "snippet", None),
        "distance": getattr(item, "distance", None),
    }


def _evidence_list(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = analysis.get("evidence") or analysis.get("merged_evidence") or []
    if isinstance(evidence, (str, dict)):
        evidence = [evidence]
    return [_as_dict(item) for item in evidence if item is not None]


def _bad_review_count(evidence: Sequence[dict[str, Any]]) -> int:
    count = 0
    for item in evidence:
        try:
            if item.get("rating") is not None and int(item["rating"]) <= 2:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def _pick_product_id(analysis: dict[str, Any], evidence: Sequence[dict[str, Any]]) -> int | None:
    for key in ("product_id",):
        value = analysis.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    for item in evidence:
        value = item.get("product_id")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _pick_product_name(analysis: dict[str, Any], evidence: Sequence[dict[str, Any]]) -> str:
    name = analysis.get("product_name") or ""
    if name:
        return str(name)
    for item in evidence:
        if item.get("product_name"):
            return str(item["product_name"])
    return ""


def build_summary(analysis: dict[str, Any], evidence: Sequence[dict[str, Any]]) -> str:
    """生成诊断报告摘要（中文，一句话 + 建议）。"""
    explicit = analysis.get("summary")
    if explicit:
        return str(explicit)
    name = _pick_product_name(analysis, evidence) or "相关商品"
    bad = _bad_review_count(evidence)
    snippets: list[str] = []
    for item in evidence:
        text = (item.get("snippet") or "").strip()
        if text and text not in snippets:
            snippets.append(text)
        if len(snippets) >= 3:
            break
    if bad >= 2:
        head = f"{name} 近 7 天出现 {bad} 条差评，集中在："
    elif bad == 1:
        head = f"{name} 出现 1 条差评，涉及："
    else:
        head = f"{name} 暂未发现明显差评，证据："
    detail = "；".join(snippets) if snippets else "暂无文本证据"
    return f"{head}{detail}。建议人工复核评价与商品详情页。"


def plan_actions(db: Session, analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """依据差评信号规划动作。

    - 证据不足（<1 条）→ 不规划任何动作
    - 有差评信号 → 保存报告；risk=high 时追加预警 + 标记待复核
    - risk=low → 只保存报告

    返回 ``[{type, label, summary, risk, payload}]``，``payload`` 里带执行所需上下文。
    """
    evidence = _evidence_list(analysis)
    if not evidence:
        logger.info("证据不足（0 条），不规划任何动作")
        return []

    risk = str(analysis.get("risk") or RISK_LOW)
    bad_count = _bad_review_count(evidence)
    product_id = _pick_product_id(analysis, evidence)
    product_name = _pick_product_name(analysis, evidence)
    summary = build_summary(analysis, evidence)
    question = str(analysis.get("question") or "")
    run_id = analysis.get("run_id")
    try:
        run_id = int(run_id) if run_id is not None else None
    except (TypeError, ValueError):
        run_id = None

    actions: list[dict[str, Any]] = []

    def _add(action_type: str, extra_summary: str = "") -> None:
        actions.append({
            "type": action_type,
            "label": ACTION_LABELS[action_type],
            "summary": extra_summary or summary,
            "risk": risk,
            "payload": {
                "question": question,
                "product_id": product_id,
                "product_name": product_name,
                "risk": risk,
                "summary": summary,
                "evidence": evidence,
                "evidence_count": len(evidence),
                "bad_review_count": bad_count,
                "run_id": run_id,
            },
        })

    _add(ACTION_SAVE_REPORT)
    if risk == RISK_HIGH:
        _add(ACTION_SEND_ALERT,
             f"{product_name or '商品'} 近 7 天差评 {bad_count} 条，已生成高风险预警")
        if product_id is not None:
            _add(ACTION_MARK_PENDING, f"将 {product_name or product_id} 标记为待人工复核")
    return actions


def assert_allowed(action_type: str) -> str:
    """白名单断言。非白名单一律抛 ValueError。"""
    value = str(action_type or "")
    if value not in ALLOWED_ACTIONS:
        raise ActionNotAllowed(
            f"动作 {value!r} 不在白名单内（仅允许：{', '.join(ALLOWED_ACTIONS)}）")
    if value not in _allowed_actions():
        raise ActionNotAllowed(f"动作 {value!r} 已被配置禁用")
    return value


def _guard_read_only(action: dict[str, Any]) -> None:
    """防御性检查：动作描述里不允许出现价格/库存/上下架/订单状态的写意图。"""
    blob = json.dumps({k: v for k, v in action.items() if k != "payload"},
                      ensure_ascii=False) + json.dumps(action.get("payload") or {},
                                                       ensure_ascii=False, default=str)
    forbidden = ("set_price", "price =", "update products", "update orders", "stock =",
                 "set_stock", "on_sale", "off_sale", "order_status")
    lowered = blob.lower()
    for token in forbidden:
        if token in lowered:
            raise ActionNotAllowed(
                f"检测到越界写意图（{token}），已拒绝；只读资源：{', '.join(READ_ONLY_RESOURCES)}")


def _execute_one(db: Session, action: dict[str, Any]) -> dict[str, Any]:
    action_type = assert_allowed(action.get("type"))
    _guard_read_only(action)
    payload = action.get("payload") or {}
    summary = str(action.get("summary") or payload.get("summary") or "")

    if action_type == ACTION_SAVE_REPORT:
        evidence = payload.get("evidence") or []
        report = DiagnosisReport(
            question=str(payload.get("question") or ""),
            product_id=payload.get("product_id"),
            risk=str(payload.get("risk") or RISK_LOW),
            evidence_count=int(payload.get("evidence_count") or len(evidence)),
            summary=summary,
            evidence_json=json.dumps(evidence, ensure_ascii=False, default=str),
            actions_json=json.dumps([], ensure_ascii=False),
            run_id=payload.get("run_id"),
        )
        db.add(report)
        db.flush()
        return {"type": action_type, "ok": True, "detail": f"已保存诊断报告 #{report.id}",
                "report_id": int(report.id)}

    if action_type == ACTION_SEND_ALERT:
        name = str(payload.get("product_name") or "商品")
        bad = int(payload.get("bad_review_count") or 0)
        notification = Notification(
            title="高风险商品预警",
            content=f"{name} 近 7 天差评 {bad} 条：{summary}",
            level=RISK_HIGH,
            read=False,
            product_id=payload.get("product_id"),
        )
        db.add(notification)
        db.flush()
        return {"type": action_type, "ok": True, "detail": f"已发送站内预警 #{notification.id}",
                "notification_id": int(notification.id)}

    if action_type == ACTION_MARK_PENDING:
        product_id = payload.get("product_id")
        if product_id is None:
            return {"type": action_type, "ok": False, "detail": "缺少 product_id，未标记"}
        product = db.get(Product, int(product_id))
        if product is None:
            return {"type": action_type, "ok": False,
                    "detail": f"商品 #{product_id} 不存在，未标记"}
        if product.pending_review:
            return {"type": action_type, "ok": True,
                    "detail": f"商品「{product.name}」已处于待复核状态"}
        product.pending_review = True
        db.flush()
        return {"type": action_type, "ok": True,
                "detail": f"已将商品「{product.name}」标记为待人工复核"}

    raise ActionNotAllowed(f"未实现的动作：{action_type}")  # pragma: no cover


def execute_actions(db: Session, actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按顺序执行动作并提交。返回 ``[{type, ok, detail}]``。

    单个动作失败不影响其它动作（各自 try/except）；非白名单动作会抛 ``ValueError``，
    调用方不应捕获——那是编码错误。
    """
    planned = list(actions or [])
    # 先整体断言，避免执行了一半才发现有非法动作
    for action in planned:
        assert_allowed(action.get("type"))

    results: list[dict[str, Any]] = []
    for action in planned:
        try:
            results.append(_execute_one(db, action))
        except ActionNotAllowed:
            db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - 单动作失败降级为 ok=False
            logger.exception("动作执行失败：%s", action.get("type"))
            try:
                db.rollback()
            except Exception:  # pragma: no cover
                pass
            results.append({"type": str(action.get("type")), "ok": False, "detail": f"执行失败：{exc}"})

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("动作提交失败")
        db.rollback()
        for item in results:
            if item.get("ok"):
                item["ok"] = False
                item["detail"] = f"提交失败：{exc}"
    return results


def skip_actions(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """用户拒绝时返回的 skipped 列表（结构同 executed，便于前端统一渲染）。"""
    return [{"type": str(a.get("type")), "ok": False,
             "detail": f"用户拒绝执行：{ACTION_LABELS.get(str(a.get('type')), a.get('type'))}"}
            for a in (actions or [])]


def public_actions(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """给 SSE / 报告接口用的精简动作描述（不含 payload 大字段）。"""
    out: list[dict[str, Any]] = []
    for action in actions or []:
        out.append({
            "type": str(action.get("type")),
            "label": str(action.get("label") or ACTION_LABELS.get(str(action.get("type")), "")),
            "summary": str(action.get("summary") or ""),
        })
    return out


__all__ = [
    "ActionNotAllowed", "plan_actions", "execute_actions", "skip_actions",
    "public_actions", "assert_allowed", "build_summary",
]
