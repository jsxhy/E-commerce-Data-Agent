"""端到端验收脚本（真实 HTTP，非 TestClient）。

用法（需先启动服务）：

    python backend/run.py --port 8000          # 另开一个终端
    python scripts/e2e_check.py                # 默认 http://127.0.0.1:8000
    python scripts/e2e_check.py --base http://127.0.0.1:9000

或者用它自带的一键流程（清库 → 起服务 → 跑检查 → 收工）：

    python scripts/e2e_all.py

覆盖 11 个段落：
    1. 登录（含错误密码 / 未登录 401）
    2. 首页 8 KPI + 30 天趋势 + 星级分布 + 重点关注商品
    3. 商品 / 订单 / 评价 / 用户 四表（分页、筛选、金额单位）
    4. 观测看板 8 KPI + 路由分布 + 阶段耗时 + 近 14 天
    5. 语义检索四快捷词（距离阈值）与空 q 校验
    6. 对话 SSE 低风险全链路（meta → step* → token* → done）
    7. 运行历史 / 详情逐步耗时 / 反馈持久化
    8. 诊断报告详情 / 搜索 / 通知未读与标记已读
    9. 高风险确认流：interrupt → reject（含重复确认 409）
   10. 高风险确认流：interrupt → approve
   11. 静态资源与 SPA 深链 / 未知 API 404

注意：第 2 / 4 段断言的是**种子库不变量**（20 运行 / 12 成功 / 70.6% / 21.8s /
路由 9-6-5 / 阶段首项 11400ms）。一旦跑过对话就会新增 AgentRun 记录而破坏这些
数字，所以必须先重建种子库再跑——用 `scripts/e2e_all.py`，或手动删掉
`backend/data/ecom.db*` 与 `backend/data/chroma/` 后重启服务。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FAILS: list[str] = []
SKIPS: list[str] = []
OK = 0


def ok(cond, msg):
    global OK
    if cond:
        OK += 1
        print(f"  PASS  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL  {msg}")


def skip(msg):
    SKIPS.append(msg)
    print(f"  SKIP  {msg}")


def make_client(base: str):
    def req(method, path, body=None, token=None, raw=False):
        url = base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r, timeout=180) as resp:
                text = resp.read().decode("utf-8")
                if raw:
                    return resp.status, text
                return resp.status, json.loads(text)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if raw:
                return exc.code, text
            try:
                return exc.code, json.loads(text)
            except Exception:
                return exc.code, {"raw": text}

    def get(path, token, **kw):
        return req("GET", path, token=token, **kw)

    def post(path, body, token):
        return req("POST", path, body=body, token=token)

    return req, get, post


def parse_sse(text: str):
    """按 \\n\\n 分帧解析 SSE，返回 [(event, data), ...]。"""
    out = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block or block.startswith(":"):
            continue
        name, datas = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                datas.append(line[5:].strip())
        if name:
            raw = "".join(datas)
            out.append((name, json.loads(raw) if raw else None))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="电商数据分析 Agent 端到端验收")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    args = ap.parse_args()

    req, get, post = make_client(args.base.rstrip("/"))

    print("=" * 70)
    print(f"0. 健康检查  {args.base}")
    try:
        st, r = req("GET", "/api/health", raw=True)
    except Exception as exc:  # 服务没起来
        print(f"  无法连接服务：{exc}")
        print("  请先执行：python backend/run.py --port 8000")
        return 1
    ok(st == 200, "GET /api/health 200")
    print("   ", r[:200])

    print("=" * 70)
    print("1. 登录")
    st, r = post("/api/auth/login", {"username": "admin", "password": "admin123"}, None)
    ok(st == 200 and r["code"] == 0, "admin 登录 200")
    tok = r["data"]["token"]
    ok(bool(tok), "拿到 JWT")
    ok(r["data"]["user"]["display_name"] == "管理员", "display_name=管理员")
    st, r = post("/api/auth/login", {"username": "admin", "password": "wrong"}, None)
    ok(st == 401, "错误密码 401")
    st, r = req("GET", "/api/dashboard/overview")
    ok(st == 401, "未登录访问受保护接口 401")

    print("=" * 70)
    print("2. 首页 KPI")
    st, r = get("/api/dashboard/overview", tok)
    d = r["data"]
    k = {name: item["value"] for name, item in d["kpis"].items()}
    print("   kpis:", json.dumps(d["kpis"], ensure_ascii=False))
    ok(k["product_count"] == 20, "商品数 20")
    ok(k["order_count"] == 49, "订单数 49")
    ok(k["review_count"] == 46, "评价数 46")
    ok(abs(k["avg_rating"] - 3.02) < 0.01, "平均评分 3.02")
    ok(abs(k["paid_amount"] - 7081.8) < 0.01, "累计成交 7081.80")
    ok(k["high_risk_reports"] == 4, "高风险报告 4")
    ok(k["unread_alerts"] == 2, "未读预警 2")
    ok(k["pending_review"] == 1, "待复核 1")
    ok(len(d["order_trend"]["points"]) == 30, "近 30 天趋势")
    ok(d["order_trend"]["total_orders"] == 49, "趋势总单 49")
    ok(abs(d["order_trend"]["total_amount"] - 7170.8) < 0.01, "趋势总额 7170.80")
    ok(d["rating_dist"]["avg"] == 3.02, "评分分布均值 3.02")
    ok([b["count"] for b in d["rating_dist"]["buckets"]] == [10, 8, 9, 9, 10],
       "星级桶 [10,8,9,9,10]")
    ap_ = d["attention_products"]
    ok(ap_ and ap_[0]["name"] == "静音蓝牙耳机" and ap_[0]["bad_review_count"] == 3,
       f"重点关注首项=静音蓝牙耳机(3条差评) 实际={ap_[0] if ap_ else None}")

    print("=" * 70)
    print("3. 数据管理四表")
    st, r = get("/api/products?page=1&size=5", tok)
    ok(len(r["data"]["items"]) == 5 and r["data"]["total"] == 20, "商品分页 total=20")
    p0 = r["data"]["items"][0]
    print("   商品[0]:", json.dumps(p0, ensure_ascii=False))
    ok(p0["price"] == 199.0, "金额出口为元 (199.0)")
    st, r = get("/api/orders?page=1&size=5", tok)
    ok(r["data"]["total"] == 49, "订单 total=49")
    st, r = get("/api/orders?status=cancelled", tok)
    ok(r["data"]["total"] == 1, "已取消订单 1 笔")
    st, r = get("/api/reviews?page=1&size=5", tok)
    ok(r["data"]["total"] == 46, "评价 total=46")
    st, r = get("/api/users?page=1&size=20", tok)
    ok(r["data"]["total"] == 2, "用户 total=2")
    st, r = get("/api/products?q=%E8%80%B3%E6%9C%BA", tok)
    ok(r["data"]["total"] >= 2, f"商品搜索『耳机』命中 {r['data']['total']} 条")

    print("=" * 70)
    print("4. 观测看板")
    st, r = get("/api/observability/overview", tok)
    o = r["data"]
    obk = {name: item["value"] for name, item in o["kpis"].items()}
    print("   kpis:", json.dumps(o["kpis"], ensure_ascii=False))
    ok(obk["total_runs"] == 20, "总运行 20")
    ok(obk["success_runs"] == 12, "成功 12")
    ok(obk["failed_runs"] == 5, "失败 5")
    ok(obk["cancelled_runs"] == 0, "已取消 0")
    ok(obk["unfinished_runs"] == 3, "未完成 3")
    ok(abs(obk["success_rate"] - 70.6) < 0.1, "成功率 70.6%")
    ok(abs(obk["avg_duration"] - 21.8) < 0.1, "平均耗时 21.8s")
    ok(obk["like_rate"] == 100.0, "点赞率 100%")
    rd = {x["route"]: x["count"] for x in o["route_dist"]}
    ok(rd == {"structured": 9, "semantic": 6, "undetermined": 5}, f"路由分布 {rd}")
    sa = o["stage_avg"]
    print("   阶段耗时:", json.dumps([(x["stage"], x["avg_ms"]) for x in sa],
                                      ensure_ascii=False))
    ok(len(sa) == 8, "阶段耗时 8 项")
    ok(sa[0]["avg_ms"] == 11400, "首项数据查询 11400ms（降序）")
    ok(len(o["daily_runs"]["points"]) == 14, "近 14 天运行量")
    ok(o["daily_runs"]["total"] == 20, "14 天合计 20 次")

    print("=" * 70)
    print("5. 语义检索（四快捷词）")
    for q in ("跑鞋脱胶", "耳机有杂音", "洗了缩水", "包装破损"):
        st, r = get(f"/api/semantic/search?q={urllib.parse.quote(q)}&top_k=5", tok)
        d = r["data"]
        top = d["items"][0] if d["items"] else None
        print(f"   {q}: mode={d['embedding_mode']} hits={d['hit_count']} "
              f"top1={top['distance'] if top else None} "
              f"{top['product_name'] if top else ''}")
        ok(d["embedding_mode"] != "unavailable", f"{q} 向量库可用")
        ok(d["hit_count"] >= 1, f"{q} 至少命中 1 条")
        ok(top is not None and top["distance"] <= 0.5, f"{q} top1 距离<=0.5")
        ok(d["collection_size"] == 66, f"{q} 向量库 66 条")
    st, r = get("/api/semantic/search?q=&top_k=5", tok)
    ok(st == 400, "空 q 应 400")

    print("=" * 70)
    print("6. 对话 SSE（低风险）")
    t0 = time.time()
    st, text = req("POST", "/api/chat/stream",
                   {"question": "这个月销售额是多少"}, tok, raw=True)
    events = parse_sse(text)
    names = [n for n, _ in events]
    print(f"   耗时 {time.time() - t0:.1f}s，事件 {len(events)} 个："
          f"{names[:6]} ... {names[-3:]}")
    ok(st == 200, "SSE 200")
    ok(names and names[0] == "meta", "首帧 meta")
    ok(names and names[-1] == "done", "末帧 done")
    steps = [x for n, x in events if n == "step"]
    print(f"   步骤 {len(steps)} 条，示例：")
    for s in steps[:4]:
        print(f"     #{s['seq']} {s['actor']} {s['stage']} "
              f"{s['detail'][:40]} {s['duration_ms']}ms")
    ok(len(steps) >= 5, f"过程步骤 >=5（实际 {len(steps)}）")
    ok([s["seq"] for s in steps] == list(range(1, len(steps) + 1)), "seq 从 1 递增")
    done = [x for n, x in events if n == "done"][0]
    meta = [x for n, x in events if n == "meta"][0]
    print("   done:", json.dumps({k2: v for k2, v in done.items() if k2 != "answer"},
                                 ensure_ascii=False))
    print("   回答前 160 字:", (done["answer"] or "")[:160].replace("\n", " "))
    ok(done["run_id"] == meta["run_id"], "done.run_id 与 meta 一致")
    ok(bool(done["answer"]), "回答非空")
    ok(done["route"] in ("structured", "semantic", "undetermined"), f"route={done['route']}")
    ok(done["status"] in ("success", "failed"), f"status={done['status']}")
    ok(done["awaiting_confirm"] is False, "低风险不等待确认")
    run_id_low = done["run_id"]

    print("=" * 70)
    print("7. 运行历史 / 详情 / 反馈")
    st, r = get("/api/runs?page=1&size=5", tok)
    ok(r["data"]["total"] >= 21, f"运行 total>=21 实际 {r['data']['total']}")
    st, r = get(f"/api/runs/{run_id_low}", tok)
    d = r["data"]
    print("   详情:", json.dumps({k2: v for k2, v in d.items() if k2 != "steps"},
                                 ensure_ascii=False)[:300])
    ok(len(d["steps"]) >= 5, "详情含逐步耗时")
    ok(d["duration_ms"] > 0, "耗时 > 0")
    st, r = post(f"/api/runs/{run_id_low}/feedback", {"value": "up"}, tok)
    ok(r["code"] == 0, "点赞成功")
    ok(r["data"]["feedback"] == "up", "点赞响应回显 up")
    st, r = get(f"/api/runs/{run_id_low}", tok)
    ok(r["data"]["feedback"] == "up", "反馈已持久化")
    st, r = post(f"/api/runs/{run_id_low}/feedback", {"value": None}, tok)
    ok(r["data"]["feedback"] is None, "传 null 可取消反馈")
    st, r = post(f"/api/runs/{run_id_low}/feedback", {"value": "up"}, tok)
    ok(r["data"]["feedback"] == "up", "恢复点赞（保持种子点赞率 100%）")

    print("=" * 70)
    print("8. 诊断报告 / 通知")
    st, r = get("/api/reports?page=1&size=20", tok)
    ok(r["data"]["total"] >= 10, f"报告 total>=10 实际 {r['data']['total']}")
    rid = r["data"]["items"][0]["id"]
    st, r = get(f"/api/reports/{rid}", tok)
    print("   报告详情:", json.dumps({k2: v for k2, v in r["data"].items()
                                      if k2 != "evidence"}, ensure_ascii=False)[:300])
    ok("evidence" in r["data"] and isinstance(r["data"]["evidence"], list),
       "详情含 evidence 数组")
    ok("actions" in r["data"] and isinstance(r["data"]["actions"], list),
       "详情含 actions 数组")
    st, r = get("/api/reports?q=%E8%80%B3%E6%9C%BA", tok)
    ok(r["data"]["total"] >= 1, f"报告搜索命中 {r['data']['total']}")
    st, r = get("/api/reports/notifications", tok)
    notifs = r["data"]["items"]
    unread = [n for n in notifs if not n["read"]]
    print(f"   通知 {len(notifs)} 条，未读 {len(unread)}")
    ok(len(notifs) >= 3, "通知 >=3")
    ok(len(unread) >= 1, "存在未读通知")
    nid = unread[0]["id"]
    st, r = post(f"/api/reports/notifications/{nid}/read", {}, tok)
    print("   标记已读:", json.dumps(r["data"], ensure_ascii=False))
    ok(r["code"] == 0 and r["data"]["read"] is True, "标记已读成功")

    print("=" * 70)
    print("9. 高风险确认流（interrupt → reject）")
    st, text = req("POST", "/api/chat/stream",
                   {"question": "静音蓝牙耳机最近差评集中在哪些问题？"}, tok, raw=True)
    events = parse_sse(text)
    names = [n for n, _ in events]
    done = [x for n, x in events if n == "done"][0]
    print("   事件:", names)
    print("   done:", json.dumps({k2: v for k2, v in done.items() if k2 != "answer"},
                                 ensure_ascii=False))
    if "interrupt" in names:
        itr = [x for n, x in events if n == "interrupt"][0]
        print("   interrupt:", json.dumps(itr, ensure_ascii=False)[:400])
        ok(done["awaiting_confirm"] is True, "awaiting_confirm=True")
        ok(len(itr["actions"]) >= 1, "至少 1 个待确认动作")
        ok(all(a["type"] in ("save_diagnosis_report", "send_alert_notification",
                             "mark_product_pending_review") for a in itr["actions"]),
           "动作类型都在白名单内")
        st, r = post("/api/chat/confirm",
                     {"run_id": done["run_id"], "decision": "reject"}, tok)
        print("   reject 结果:", json.dumps(r["data"], ensure_ascii=False)[:300])
        ok(r["code"] == 0, "reject 成功")
        ok(r["data"]["executed"] == [], "reject 未执行任何动作")
        st, r = post("/api/chat/confirm",
                     {"run_id": done["run_id"], "decision": "approve"}, tok)
        ok(r["code"] == 409, "重复确认 409")
    else:
        skip("本轮未触发 interrupt（LLM 规划为低风险），确认流断言未覆盖")

    print("=" * 70)
    print("10. 高风险确认流（interrupt → approve）")
    st, text = req("POST", "/api/chat/stream",
                   {"question": "静音蓝牙耳机差评很多，帮我处理一下"}, tok, raw=True)
    events = parse_sse(text)
    names = [n for n, _ in events]
    done = [x for n, x in events if n == "done"][0]
    if "interrupt" in names:
        itr = [x for n, x in events if n == "interrupt"][0]
        print("   动作:", [a["label"] for a in itr["actions"]])
        before = get("/api/reports/notifications", tok)[1]["data"]["items"]
        st, r = post("/api/chat/confirm",
                     {"run_id": done["run_id"], "decision": "approve"}, tok)
        print("   approve 结果:", json.dumps(r["data"], ensure_ascii=False)[:400])
        ok(r["code"] == 0, "approve 成功")
        ok(len(r["data"]["executed"]) == len(itr["actions"]), "全部动作已执行")
        ok(all(x["ok"] for x in r["data"]["executed"]), "每个动作 ok=true")
        after = get("/api/reports/notifications", tok)[1]["data"]["items"]
        print(f"   通知数 {len(before)} → {len(after)}")
        ok(len(after) >= len(before), "执行后通知未减少")
    else:
        skip("本轮未触发 interrupt，确认流断言未覆盖")

    print("=" * 70)
    print("11. 静态资源 / SPA")
    st, text = req("GET", "/", raw=True)
    ok(st == 200 and 'id="app"' in text, "GET / 返回 index.html")
    st, text = req("GET", "/assets/app.js", raw=True)
    ok(st == 200 and len(text) > 1_000_000, f"app.js 可访问（{len(text) // 1024} KB）")
    st, text = req("GET", "/semantic", raw=True)
    ok(st == 200 and 'id="app"' in text, "SPA 深链 /semantic 回落 index.html")
    st, text = req("GET", "/api/not-exist", raw=True)
    ok(st == 404, "未知 API 404（未被 SPA 吞掉）")

    print("=" * 70)
    print(f"结果：{OK} passed, {len(FAILS)} failed, {len(SKIPS)} skipped")
    for f in FAILS:
        print("  - " + f)
    for s in SKIPS:
        print("  ~ " + s)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
