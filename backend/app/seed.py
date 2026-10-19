"""确定性种子数据：一次生成参考站的全部演示数据。

数据规模与不变量（与 ``docs/api-contract.md``、首页/观测看板 KPI 严格对齐）：

* 用户 2 个：``admin`` / ``admin123``（管理员）、``analyst`` / ``analyst123``（数据分析师）
* 商品 20 个，评价 46 条，订单 49 笔，诊断报告 10 份（4 份高风险），站内通知 3 条（2 条未读）
* 运行记录 20 条 = 12 成功 + 5 失败 + 3 未完成（待确认）
* 评价星级 ``{5:10, 4:9, 3:9, 2:8, 1:10}`` → 合计 139 分、均分 **3.02**、≤2 星 **18** 条
* 订单：48 笔已支付合计 **708180 分（¥7081.80）** + 1 笔已取消 8900 分（¥89.00）
  → 全部 49 笔合计 **717080 分（¥7170.80）**
* 运行路由分布 structured 9 / semantic 6 / undetermined 5；
  12 条成功运行的平均耗时恰好 **21800ms**；反馈 赞 1 / 踩 0

设计约定：

* 所有时间都相对「今天零点」生成，保证首页近 30 天趋势、观测看板近 14 天柱状图始终有数据；
* 随机数用固定种子，因此同一自然日内多次 seed 结果完全一致；
* 各阶段（``RunStep``）耗时用**统一模板**，使观测看板「各阶段平均耗时」不随聚合口径漂移。
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from sqlalchemy import delete

from backend.app import constants
from backend.app.db import SessionLocal, init_db
from backend.app.models import (AgentRun, DiagnosisReport, Notification, Order, Product, Review,
                                RunStep, User)
from backend.app.security import hash_password

# ------------------------------------------------------------------ 固定规模

SEED = 20240601
TREND_DAYS = 30
RUN_DAYS = 14
REVIEW_DAYS = 60

#: 商品价格（分），顺序即商品 id 1..20
PRICES = [19900, 29900, 8900, 6900, 34900, 9900, 12990, 7900, 11900, 15900,
          6990, 5900, 4990, 22900, 17900, 13900, 5990, 8900, 19900, 6900]

#: 每个商品的订单条数（合计 48 笔已支付）
ORDER_COUNTS = [3, 3, 3, 3, 2, 3, 2, 3, 2, 2, 2, 3, 3, 2, 2, 2, 2, 2, 2, 2]

#: 每个商品订单里的购买数量，与 ORDER_COUNTS 一起把已支付总额精确凑到 708180 分
ORDER_QTYS = [1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 1, 1, 2]

#: 已支付订单总额（分）：708180
PAID_TOTAL_CENTS = 708180

#: 取消订单：商品 id 3，数量 1，金额 8900 分
CANCELLED_ORDER = {"product_id": 3, "quantity": 1, "amount_cents": 8900}

#: 12 条成功运行的耗时（ms），均值恰好 21800
SUCCESS_DURATIONS = [17600, 18900, 19500, 20300, 21000, 21800,
                     22300, 23000, 23800, 24600, 25300, 23500]

PRODUCTS = [
    ("静音蓝牙耳机", "数码配件", 128, "on_sale",
     "入耳式真无线蓝牙耳机，双麦克风通话降噪，单次续航 6 小时，配合充电仓可用 24 小时。"),
    ("轻量跑步鞋", "运动户外", 64, "on_sale",
     "单只约 220 克的轻量跑鞋，网面透气鞋面，中底为回弹 EVA，适合日常慢跑与通勤。"),
    ("纯棉基础款T恤", "服饰", 240, "on_sale",
     "100% 精梳棉短袖 T 恤，克重 190g，圆领直筒版型，可机洗。"),
    ("不锈钢保温杯", "家居日用", 310, "on_sale",
     "316 不锈钢内胆保温杯，容量 500ml，双层真空结构，杯盖带密封硅胶圈。"),
    ("无线降噪头戴耳机", "数码配件", 42, "on_sale",
     "头戴式主动降噪蓝牙耳机，40mm 动圈单元，支持有线模式，可折叠收纳。"),
    ("天然乳胶枕", "家居家纺", 96, "on_sale",
     "93% 天然乳胶含量，波浪形护颈设计，透气孔结构，外套可拆洗。"),
    ("快干运动短裤", "运动户外", 88, "on_sale",
     "双层速干运动短裤，外层锦纶弹力面料，内衬网眼，带隐形拉链口袋。"),
    ("陶瓷马克杯", "家居日用", 150, "on_sale",
     "哑光釉面陶瓷马克杯，容量 350ml，可进微波炉与洗碗机。"),
    ("加厚法兰绒睡衣", "服饰", 73, "on_sale",
     "加厚法兰绒两件套家居服，双面起绒，松紧腰头，秋冬保暖款。"),
    ("便携榨汁杯", "厨房小电", 55, "on_sale",
     "无线便携榨汁杯，六叶刀头，USB-C 充电，容量 400ml，可随身携带。"),
    ("真丝眼罩", "家居家纺", 180, "off_sale",
     "19 姆米桑蚕丝眼罩，可调节绑带，遮光设计，附赠收纳袋。"),
    ("帆布托特包", "箱包", 210, "on_sale",
     "16 安加厚帆布托特包，大容量单肩手提两用，内置一个小隔袋。"),
    ("竹纤维洗碗巾", "家居日用", 400, "on_sale",
     "竹纤维不沾油洗碗巾，吸水速干，可反复水洗，三条装。"),
    ("智能手环", "数码配件", 66, "on_sale",
     "智能运动手环，支持心率与血氧监测、睡眠分析、50 米防水，续航约 7 天。"),
    ("越野跑鞋", "运动户外", 38, "on_sale",
     "越野跑鞋，深齿橡胶大底，防泼水鞋面，鞋头带防撞包边。"),
    ("磨砂玻璃水壶", "厨房小电", 84, "on_sale",
     "高硼硅磨砂玻璃水壶，容量 1.2L，耐冷热温差，壶口带滤网。"),
    ("硅胶折叠水杯", "家居日用", 260, "on_sale",
     "食品级硅胶折叠水杯，折叠后仅 3 厘米厚，带挂扣，适合出差旅行。"),
    ("记忆棉坐垫", "家居家纺", 120, "on_sale",
     "慢回弹记忆棉坐垫，人体工学凹槽设计，外套可拆洗，适合久坐办公。"),
    ("蓝牙音箱", "数码配件", 59, "on_sale",
     "便携蓝牙音箱，10W 双单元，IPX6 防水，续航 12 小时，支持免提通话。"),
    ("针织开衫", "服饰", 140, "on_sale",
     "宽松版型针织开衫，混纺纱线，落肩设计，春秋薄款外套。"),
]

#: 评价表：(商品 id, 星级, 正文, 买家, 是否有图, 图片描述)
REVIEWS = [
    # 1 静音蓝牙耳机（3 条差评，全站差评最多）
    (1, 1, "戴了两天左耳就没声音了，找客服也一直推脱，很不满意。", "林**", False, None),
    (1, 2, "降噪效果一般，地铁里还是能听到报站声，续航也虚标。", "周**", False, None),
    (1, 2, "连接经常断，手机放口袋里走两步就掉线，只能退货。", "吴**", False, None),
    # 2 轻量跑步鞋
    (2, 1, "跑了两次鞋底就开胶了，质量太差，退了。", "赵**", True,
     "照片里鞋底前掌与鞋面明显脱开，胶线开裂约 3 厘米，鞋头还有明显磨损。"),
    (2, 2, "尺码偏小半码，鞋面偏硬，磨合期磨脚踝。", "钱**", False, None),
    (2, 3, "轻是挺轻的，但缓震一般，跑十公里后脚掌发酸。", "孙**", False, None),
    # 3 纯棉基础款T恤
    (3, 2, "洗了一次就缩水，短了一截，版型也不对了。", "李**", True,
     "照片里同一件 T 恤洗前洗后平铺对比，衣长缩短约 4 厘米，下摆明显起皱。"),
    (3, 2, "领口洗两次就松了，颜色也掉得厉害。", "周**", False, None),
    (3, 3, "面料还行，就是偏薄，白色有点透。", "郑**", False, None),
    # 4 不锈钢保温杯
    (4, 2, "保温效果不如宣传，四小时后水就温了。", "王**", False, None),
    (4, 4, "颜值不错，密封也可以，就是杯口偏小不好洗。", "冯**", False, None),
    (4, 5, "早上装的开水到下午还是烫的，很满意。", "陈**", False, None),
    # 5 无线降噪头戴耳机
    (5, 1, "耳机有杂音，左耳一直滋滋响，降噪也一般。", "褚**", True,
     "照片里耳机左耳罩边缘有细微裂纹，外包装内衬有明显压痕。"),
    (5, 2, "戴久了夹头，耳罩闷热，夏天基本没法用。", "卫**", False, None),
    (5, 4, "降噪确实可以，通勤够用，就是重了点。", "蒋**", False, None),
    # 6 天然乳胶枕
    (6, 2, "味道有点大，晾了一周还有橡胶味。", "沈**", False, None),
    (6, 5, "颈椎舒服多了，回弹很好。", "韩**", False, None),
    (6, 5, "第二次回购，家里人都用这个。", "杨**", False, None),
    # 7 快干运动短裤
    (7, 1, "第一次穿就开线，裤腿缝线全崩了。", "朱**", False, None),
    (7, 3, "速干还行，但口袋太浅，手机容易掉。", "秦**", False, None),
    # 8 陶瓷马克杯
    (8, 1, "收到时包装破损，杯口有磕碰。", "尤**", True,
     "照片里外箱一角塌陷，杯口边缘缺了一小块釉面，内包装泡沫已碎裂。"),
    (8, 3, "杯子挺好看的，就是容量比想象中小。", "许**", False, None),
    # 9 加厚法兰绒睡衣
    (9, 1, "洗了缩水严重，裤子直接短了一截。", "何**", False, None),
    (9, 3, "绒挺软的，但掉毛，黑色内搭粘了一层。", "吕**", False, None),
    # 10 便携榨汁杯
    (10, 1, "用了三次电机就不转了，客服只让寄回。", "施**", False, None),
    (10, 3, "打果汁可以，打冰块不行，声音也大。", "张**", False, None),
    # 11 真丝眼罩
    (11, 1, "戴上还是漏光，鼻梁位置有缝。", "孔**", False, None),
    (11, 3, "丝质手感不错，就是洗过之后有点变形。", "曹**", False, None),
    # 12 帆布托特包
    (12, 1, "背带缝线开裂，装两本书就断了。", "严**", False, None),
    (12, 3, "容量够大，但内衬没有隔层，东西全混在一起。", "华**", False, None),
    # 13 竹纤维洗碗巾
    (13, 1, "用一周就发霉，还有异味。", "金**", False, None),
    (13, 3, "吸水还行，就是尺寸偏小。", "魏**", False, None),
    # 14 智能手环
    (14, 4, "心率挺准的，续航一周左右。", "陶**", False, None),
    (14, 5, "性价比很高，睡眠监测意外地准。", "姜**", False, None),
    # 15 越野跑鞋
    (15, 4, "抓地力不错，碎石路也稳。", "戚**", False, None),
    (15, 5, "越野跑了两趟，包裹性好，脚不晃。", "谢**", False, None),
    # 16 磨砂玻璃水壶
    (16, 4, "磨砂手感好，不漏水。", "邹**", False, None),
    (16, 5, "装热水也没异味，很喜欢。", "喻**", False, None),
    # 17 硅胶折叠水杯
    (17, 4, "出差带着方便，折叠后很薄。", "柏**", False, None),
    (17, 5, "便宜好用，准备再买两个。", "水**", False, None),
    # 18 记忆棉坐垫
    (18, 4, "坐久了腰没那么累。", "窦**", False, None),
    (18, 5, "回弹很好，套子可拆洗，加分。", "章**", False, None),
    # 19 蓝牙音箱
    (19, 4, "声音比预期大，低音一般。", "云**", False, None),
    (19, 5, "露营带着很合适，续航够。", "苏**", False, None),
    # 20 针织开衫
    (20, 4, "版型宽松，颜色和图片一致。", "潘**", False, None),
    (20, 5, "很软，秋天穿刚好。", "葛**", False, None),
]

BUYERS = ["林**", "周**", "吴**", "赵**", "钱**", "孙**", "李**", "郑**", "王**", "冯**",
          "陈**", "褚**", "卫**", "蒋**", "沈**", "韩**", "杨**", "朱**", "秦**", "尤**",
          "许**", "何**", "吕**", "施**", "张**", "孔**", "曹**", "严**", "华**", "金**",
          "魏**", "陶**", "姜**", "戚**", "谢**", "邹**", "喻**", "柏**", "水**", "窦**",
          "章**", "云**", "苏**", "潘**", "葛**", "范**"]

# ------------------------------------------------------------------ 阶段模板

ROUTE_DETAIL = {
    constants.ROUTE_STRUCTURED: "命中结构化查询（含商品、订单号或统计口径）",
    constants.ROUTE_SEMANTIC: "命中语义检索（模糊评价主题）",
    constants.ROUTE_UNDETERMINED: "规则未命中，按兜底流程继续",
}


def _stage(key: str, actor: str, detail: str, ms: int) -> tuple[str, str, str, int]:
    return (key, actor, detail, ms)


STARTING = _stage(constants.STAGE_STARTING, constants.ACTOR_SYSTEM,
                  "已接入 LangGraph 分析流程", 8)
SCHEDULING = _stage(constants.STAGE_SCHEDULING, constants.ACTOR_SCHEDULER,
                    "读取运行状态，决定下一步交给谁", 12)
ANALYSIS = _stage(constants.STAGE_AGENT_ANALYSIS, constants.ACTOR_ANALYSIS,
                  "承接任务，拆分查询与检索需求", 2800)
QUERYING = _stage(constants.STAGE_QUERYING, constants.ACTOR_ANALYSIS,
                  "经 MCP 调用 MySQL 工具完成统计查询", 11400)
DEEPENING = _stage(constants.STAGE_DEEPENING, constants.ACTOR_ANALYSIS,
                   "经 MCP 深挖 Chroma 完成，补充语义证据", 469)
EVIDENCE = _stage(constants.STAGE_PREPARING_EVIDENCE, constants.ACTOR_ANALYSIS,
                  "正在汇总结构化结果与语义片段", 36)
REPORTING = _stage(constants.STAGE_AGENT_REPORT, constants.ACTOR_REPORT,
                   "读取证据，规划回答结构与动作", 10)
DECIDING = _stage(constants.STAGE_DECIDING, constants.ACTOR_REPORT,
                  "差评信号判定完成，风险={risk}", 24)
ACTION_DECISION = _stage(constants.STAGE_ACTION_DECISION, constants.ACTOR_REPORT,
                         "规划动作 {n} 个", 20)
EXECUTING = _stage(constants.STAGE_EXECUTING, constants.ACTOR_SYSTEM,
                   "已执行 {n} 个动作", 25)
FINISHED = _stage(constants.STAGE_FINISHED, constants.ACTOR_SYSTEM, "运行结束", 6)
PENDING = _stage(constants.STAGE_ACTION_PENDING, constants.ACTOR_REPORT,
                 "检测到高风险，请确认是否执行以下动作", 18)


def _route_step(route: str) -> tuple[str, str, str, int]:
    return (constants.STAGE_ROUTING, constants.ACTOR_SCHEDULER,
            ROUTE_DETAIL.get(route, ROUTE_DETAIL[constants.ROUTE_UNDETERMINED]), 27)


def _steps_success(route: str, risk: str, action_count: int) -> list[tuple]:
    """成功运行的完整步骤模板（阶段耗时固定，保证观测看板均值稳定）。"""
    steps = [STARTING, SCHEDULING, _route_step(route), ANALYSIS, QUERYING]
    if route == constants.ROUTE_SEMANTIC:
        steps.append(DEEPENING)
    steps.extend([
        EVIDENCE,
        REPORTING,
        (DECIDING[0], DECIDING[1], DECIDING[2].format(risk=risk), DECIDING[3]),
        (ACTION_DECISION[0], ACTION_DECISION[1],
         ACTION_DECISION[2].format(n=action_count), ACTION_DECISION[3]),
        (EXECUTING[0], EXECUTING[1], EXECUTING[2].format(n=action_count), EXECUTING[3]),
        FINISHED,
    ])
    return steps


def _steps_failed(route: str, where: str) -> list[tuple]:
    """失败运行：只保留失败前已经跑过的步骤，末步记录异常原因。"""
    steps = [STARTING, SCHEDULING, _route_step(route)]
    if where in ("analysis", "querying", "deciding"):
        steps.append(ANALYSIS)
    if where in ("querying", "deciding"):
        steps.append(QUERYING)
        if route == constants.ROUTE_SEMANTIC:
            steps.append(DEEPENING)
    if where == "deciding":
        steps.extend([EVIDENCE, REPORTING])
    reason = {
        "routing": "规则路由异常，运行终止",
        "analysis": "查询分析 Agent 超时未返回，运行终止",
        "querying": "SQL 执行超时且重试失败，无法得出统计结论，运行终止",
        "deciding": "动作规划异常，运行终止",
    }[where]
    steps.append((constants.STAGE_FINISHED, constants.ACTOR_SYSTEM, reason, 6))
    return steps


def _steps_pending(route: str) -> list[tuple]:
    """未完成运行：停在等待人工确认。"""
    return [STARTING, SCHEDULING, _route_step(route), ANALYSIS, PENDING]


# ------------------------------------------------------------------ 运行记录

RUNS = [
    # (问题, 路由, 状态, 耗时ms, 反馈, 失败位置, 风险, 动作数, 回答)
    ("最近 30 天一共成交多少单？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     17600, "up", None, constants.RISK_LOW, 1,
     "## 近 30 天订单概览\n\n- **已支付订单 48 笔**，另有 1 笔已取消\n"
     "- 成交金额合计 **¥7,081.80**（不含已取消）\n- 日均约 1.6 单，周末略高于工作日\n\n"
     "> 结构化查询 · orders"),
    ("本月累计成交金额是多少？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     18900, None, None, constants.RISK_LOW, 1,
     "## 本月成交金额\n\n- 本月已支付订单合计 **¥7,081.80**\n"
     "- 客单价约 **¥147.54**\n- 金额最高的商品是「无线降噪头戴耳机」（¥349.00/件）\n\n"
     "> 结构化查询 · orders"),
    ("用户最近都在吐槽什么？", constants.ROUTE_SEMANTIC, constants.STATUS_SUCCESS,
     19500, None, None, constants.RISK_LOW, 1,
     "## 近期负向反馈主题\n\n- **脱胶 / 开胶**：跑鞋类共 2 条，集中在鞋底前掌\n"
     "- **杂音**：头戴降噪耳机左耳滋滋响\n- **缩水**：纯棉 T 恤与法兰绒睡衣洗后变短\n"
     "- **包装破损**：马克杯到货磕碰\n\n> 语义检索 · reviews"),
    ("哪个商品卖得最好？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     20300, None, None, constants.RISK_LOW, 1,
     "## 销量排名\n\n1. **轻量跑步鞋** 3 笔\n2. **静音蓝牙耳机** 3 笔\n"
     "3. **纯棉基础款T恤** 3 笔\n\n按件数看，跑步鞋与保温杯的加购件数最高（各含 2 件单）。\n\n"
     "> 结构化查询 · orders"),
    ("差评主要集中在哪些方面？", constants.ROUTE_SEMANTIC, constants.STATUS_SUCCESS,
     21000, None, None, constants.RISK_HIGH, 3,
     "## 差评集中度\n\n- 全站 ≤2 星评价 **18 条**，占 39.1%\n"
     "- **静音蓝牙耳机** 3 条，为差评最集中的商品\n- 轻量跑步鞋、纯棉T恤、无线降噪头戴耳机各 2 条\n\n"
     "已为 4 个高风险商品生成诊断报告，并触发站内预警与人工复核。\n\n> 语义检索 · reviews"),
    ("静音蓝牙耳机卖了多少单？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     21800, None, None, constants.RISK_HIGH, 3,
     "## 静音蓝牙耳机销售情况\n\n- 已支付 **3 笔**，合计 **¥597.00**\n"
     "- 平均评分 **1.67 分**（3 条评价全部为 ≤2 星）\n- 主要问题：单耳无声、连接断开、降噪不达预期\n\n"
     "> 结构化查询 · orders + reviews"),
    ("有没有提到脱胶或开胶的评价？", constants.ROUTE_SEMANTIC, constants.STATUS_SUCCESS,
     22300, None, None, constants.RISK_HIGH, 3,
     "## 脱胶相关评价\n\n- 命中 **2 条**，均来自跑鞋类商品\n"
     "- 「跑了两次鞋底就开胶了，质量太差」——轻量跑步鞋，1 星，附图\n"
     "- 图片描述显示鞋底前掌与鞋面胶线开裂约 3 厘米\n\n"
     "> 语义检索 · reviews（距离 0.21 / 0.34）"),
    ("近 7 天日均订单量是多少？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     23000, None, None, constants.RISK_LOW, 1,
     "## 近 7 天订单量\n\n- 合计 **11 笔**，日均 **1.6 笔**\n"
     "- 峰值出现在周末，单日最高 3 笔\n- 与近 30 天整体节奏一致\n\n> 结构化查询 · orders"),
    ("用户对包装和物流的反馈怎么样？", constants.ROUTE_SEMANTIC, constants.STATUS_SUCCESS,
     23800, None, None, constants.RISK_LOW, 1,
     "## 包装与物流反馈\n\n- 明确提到包装问题的评价 **1 条**：陶瓷马克杯到货外箱塌陷、杯口磕碰\n"
     "- 其余评价未提及物流时效问题\n- 建议对易碎品类增加内衬缓冲\n\n> 语义检索 · reviews"),
    ("评价平均分是多少？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     24600, None, None, constants.RISK_LOW, 1,
     "## 评价评分\n\n- 共 **46 条**评价，平均 **3.02 分**（满分 5 分）\n"
     "- 5 星 10 条、4 星 9 条、3 星 9 条、2 星 8 条、1 星 10 条\n"
     "- ≤2 星合计 18 条，占 39.1%\n\n> 结构化查询 · reviews"),
    ("耳机类商品的评价里提到最多的问题是什么？", constants.ROUTE_SEMANTIC,
     constants.STATUS_SUCCESS, 25300, None, None, constants.RISK_HIGH, 3,
     "## 耳机类负向反馈\n\n- **杂音 / 电流声**：1 条（左耳滋滋响）\n"
     "- **连接不稳定**：1 条（走两步即掉线）\n- **佩戴夹头、闷热**：1 条\n"
     "- **续航虚标**：1 条\n\n静音蓝牙耳机与无线降噪头戴耳机均已进入高风险名单。\n\n"
     "> 语义检索 · reviews"),
    ("待复核商品有几个？", constants.ROUTE_STRUCTURED, constants.STATUS_SUCCESS,
     23500, None, None, constants.RISK_LOW, 1,
     "## 待人工复核\n\n- 当前 **1 个**商品处于待复核状态：静音蓝牙耳机\n"
     "- 该商品差评 3 条，均分 1.67，已生成高风险诊断报告\n\n> 结构化查询 · products"),
    # ---- 失败 5 条 ----
    ("各品类的成交金额占比是多少？", constants.ROUTE_STRUCTURED, constants.STATUS_FAILED,
     None, None, "routing", constants.RISK_LOW, 0,
     "运行失败：规则路由异常，未能进入查询阶段，无法得出统计结论。"),
    ("最近有没有异常退款？", constants.ROUTE_SEMANTIC, constants.STATUS_FAILED,
     None, None, "routing", constants.RISK_LOW, 0,
     "运行失败：规则路由异常，未能完成语义检索，无法给出结论。"),
    ("库存低于 50 的商品有哪些？", constants.ROUTE_STRUCTURED, constants.STATUS_FAILED,
     None, None, "analysis", constants.RISK_LOW, 0,
     "运行失败：查询分析 Agent 超时未返回，无法得出库存结论。"),
    ("帮我分析一下最近的情况", constants.ROUTE_UNDETERMINED, constants.STATUS_FAILED,
     None, None, "analysis", constants.RISK_LOW, 0,
     "运行失败：未能判定查询意图，查询分析 Agent 超时。"),
    ("随便看看数据", constants.ROUTE_UNDETERMINED, constants.STATUS_FAILED,
     None, None, "routing", constants.RISK_LOW, 0,
     "运行失败：问题过于模糊，规则路由未命中，运行终止。"),
    # ---- 未完成 3 条（待确认）----
    ("帮我看看店铺整体健康度", constants.ROUTE_UNDETERMINED, constants.STATUS_PENDING,
     None, None, None, constants.RISK_HIGH, 3,
     "已生成初步结论，涉及发送站内预警与标记人工复核，等待人工确认后执行。"),
    ("分析一下最近的差评趋势", constants.ROUTE_UNDETERMINED, constants.STATUS_PENDING,
     None, None, None, constants.RISK_HIGH, 2,
     "已定位 4 个高风险商品，动作执行需人工确认。"),
    ("看看有没有需要处理的问题", constants.ROUTE_UNDETERMINED, constants.STATUS_PENDING,
     None, None, None, constants.RISK_LOW, 1,
     "证据仍在汇总中，运行尚未结束。"),
]

#: 诊断报告：(商品 id, 风险, 问题, 证据数, 摘要)
REPORTS = [
    (1, constants.RISK_HIGH, "静音蓝牙耳机最近的差评都说了什么？", 3,
     "静音蓝牙耳机近 60 天新增 3 条评价全部为 ≤2 星，均分 1.67，集中在单耳无声、频繁断连与降噪不达预期，建议立即人工复核。"),
    (2, constants.RISK_HIGH, "轻量跑步鞋脱胶问题严重吗？", 2,
     "轻量跑步鞋出现 2 条差评，其中 1 条附图为鞋底前掌脱胶，胶线开裂约 3 厘米，疑似批次胶合工艺问题。"),
    (3, constants.RISK_HIGH, "纯棉T恤缩水反馈集中吗？", 2,
     "纯棉T恤有 2 条差评均指向洗后缩水与领口变形，附图对比显示衣长缩短约 4 厘米，需核对面料预缩工艺。"),
    (5, constants.RISK_HIGH, "无线降噪头戴耳机的杂音问题需要处理吗？", 2,
     "无线降噪头戴耳机 1 条差评明确描述左耳滋滋杂音并附图显示耳罩裂纹，另有 1 条反馈夹头闷热，均分 2.33。"),
    (4, constants.RISK_LOW, "保温杯保温效果被投诉了吗？", 1,
     "不锈钢保温杯仅 1 条差评提到四小时后水温下降，整体均分 3.67，属个别反馈，暂不需升级处理。"),
    (6, constants.RISK_LOW, "乳胶枕气味问题有多少反馈？", 1,
     "天然乳胶枕 1 条差评反馈橡胶味持续一周，均分 4.00，其余 2 条为 5 星好评，建议补充晾置说明。"),
    (7, constants.RISK_LOW, "运动短裤开线问题", 1,
     "快干运动短裤 1 条差评反馈首次穿着即开线，均分 2.00，建议抽检缝线工艺。"),
    (9, constants.RISK_LOW, "睡衣缩水情况", 1,
     "加厚法兰绒睡衣 1 条差评反馈洗后缩水明显，均分 2.00，与 T 恤缩水问题可能同源。"),
    (15, constants.RISK_LOW, "越野跑鞋整体口碑如何？", 2,
     "越野跑鞋 2 条评价均为 4~5 星，抓地力与包裹性获好评，无负向信号。"),
    (8, constants.RISK_LOW, "马克杯包装破损投诉", 1,
     "陶瓷马克杯 1 条差评反馈到货包装破损、杯口磕碰并附图，建议对易碎品类增加内衬缓冲。"),
]

#: 站内通知：(标题, 内容, 级别, 是否已读, 关联商品)
NOTIFICATIONS = [
    ("高风险商品预警", "静音蓝牙耳机 近 60 天差评 3 条，均分 1.67，请尽快人工复核。",
     constants.RISK_HIGH, False, 1),
    ("高风险商品预警", "轻量跑步鞋 近 60 天差评 2 条，均分 2.00，疑似批次脱胶，请尽快人工复核。",
     constants.RISK_HIGH, False, 2),
    ("诊断报告已生成", "纯棉T恤 的缩水反馈已生成诊断报告，可在诊断报告页查看。",
     "info", True, 3),
]

#: 需要标记待人工复核的商品
PENDING_REVIEW_PRODUCT = 1


# ------------------------------------------------------------------ 工具

def _anchor() -> datetime:
    """今天零点：所有相对时间都以它为基准，保证图表窗口内始终有数据。"""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _at(anchor: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    return anchor - timedelta(days=day_offset) + timedelta(hours=hour, minutes=minute)


def _clear(db) -> None:
    """按外键依赖顺序清空业务表（先子后父）。"""
    for model in (RunStep, AgentRun, DiagnosisReport, Notification, Review, Order, Product, User):
        db.execute(delete(model))
    db.commit()


# ------------------------------------------------------------------ 主流程

def seed_all(drop: bool = True) -> dict:
    """建表（可选重建）并写入全部种子数据，返回各表条数摘要。"""
    init_db(drop=drop)
    anchor = _anchor()
    rnd = random.Random(SEED)

    db = SessionLocal()
    try:
        _clear(db)

        # ---------------- 用户 ----------------
        db.add_all([
            User(username="admin", password_hash=hash_password("admin123"),
                 display_name="管理员", role="admin", created_at=_at(anchor, 45, 9)),
            User(username="analyst", password_hash=hash_password("analyst123"),
                 display_name="数据分析师", role="analyst", created_at=_at(anchor, 30, 14)),
        ])

        # ---------------- 商品 ----------------
        products: list[Product] = []
        for idx, (name, category, stock, status, description) in enumerate(PRODUCTS):
            product = Product(
                name=name,
                category=category,
                price_cents=PRICES[idx],
                stock=stock,
                status=status,
                description=description,
                image_desc=None,
                pending_review=(idx + 1 == PENDING_REVIEW_PRODUCT),
                created_at=_at(anchor, 50 - idx, 10, 15),
            )
            products.append(product)
        db.add_all(products)
        db.flush()

        # ---------------- 评价 ----------------
        reviews: list[Review] = []
        for idx, (pid, rating, content, buyer, has_image, image_desc) in enumerate(REVIEWS):
            reviews.append(Review(
                product_id=pid,
                rating=rating,
                content=content,
                buyer=buyer,
                has_image=has_image,
                image_desc=image_desc,
                created_at=_at(anchor, rnd.randrange(REVIEW_DAYS), 9 + rnd.randrange(13),
                               rnd.randrange(60)),
            ))
        db.add_all(reviews)
        db.flush()

        reviews_by_product: dict[int, list[Review]] = {}
        for review in reviews:
            reviews_by_product.setdefault(review.product_id, []).append(review)

        # ---------------- 订单 ----------------
        orders: list[Order] = []
        seq = 0
        for pid, count in enumerate(ORDER_COUNTS, start=1):
            qty = ORDER_QTYS[pid - 1]
            amount = PRICES[pid - 1] * qty
            for _ in range(count):
                seq += 1
                created = _at(anchor, rnd.randrange(TREND_DAYS), 9 + rnd.randrange(13),
                              rnd.randrange(60))
                orders.append(Order(
                    order_no=f"SO{created:%Y%m%d}{seq:03d}",
                    product_id=pid,
                    quantity=qty,
                    amount_cents=amount,
                    status="paid",
                    buyer=BUYERS[seq % len(BUYERS)],
                    created_at=created,
                ))
        # 1 笔已取消订单（不计入已支付合计）
        seq += 1
        cancelled_at = _at(anchor, rnd.randrange(TREND_DAYS), 11, 20)
        orders.append(Order(
            order_no=f"SO{cancelled_at:%Y%m%d}{seq:03d}",
            product_id=CANCELLED_ORDER["product_id"],
            quantity=CANCELLED_ORDER["quantity"],
            amount_cents=CANCELLED_ORDER["amount_cents"],
            status="cancelled",
            buyer=BUYERS[seq % len(BUYERS)],
            created_at=cancelled_at,
        ))
        db.add_all(orders)
        db.flush()

        paid_total = sum(o.amount_cents for o in orders if o.status == "paid")
        assert paid_total == PAID_TOTAL_CENTS, f"已支付合计 {paid_total} != {PAID_TOTAL_CENTS}"
        assert len(orders) == 49, f"订单条数 {len(orders)} != 49"

        # ---------------- 运行记录 ----------------
        runs: list[AgentRun] = []
        for idx, (question, route, status, duration, feedback,
                  fail_where, risk, action_count, answer) in enumerate(RUNS):
            if status == constants.STATUS_SUCCESS:
                steps = _steps_success(route, risk, action_count)
                duration_ms = duration or SUCCESS_DURATIONS[idx % len(SUCCESS_DURATIONS)]
            elif status == constants.STATUS_FAILED:
                steps = _steps_failed(route, fail_where or "routing")
                duration_ms = sum(step[3] for step in steps) + 420
            else:
                steps = _steps_pending(route)
                duration_ms = 0
            runs.append(AgentRun(
                question=question,
                route=route,
                status=status,
                duration_ms=duration_ms,
                step_count=len(steps),
                answer=answer,
                feedback=feedback,
                created_at=_at(anchor, rnd.randrange(RUN_DAYS), 10 + rnd.randrange(12),
                               rnd.randrange(60)),
            ))
            runs[-1]._seed_steps = steps  # type: ignore[attr-defined]
        db.add_all(runs)
        db.flush()

        for run in runs:
            for seq_no, (stage, actor, detail, ms) in enumerate(run._seed_steps, start=1):  # type: ignore[attr-defined]
                db.add(RunStep(run_id=run.id, seq=seq_no, stage=stage, actor=actor,
                               detail=detail, duration_ms=ms))

        # ---------------- 诊断报告 ----------------
        for idx, (pid, risk, question, evidence_count, summary) in enumerate(REPORTS):
            product_reviews = sorted(reviews_by_product.get(pid, []),
                                     key=lambda r: (r.rating, r.id))
            evidence = [
                {
                    "source": "review",
                    "ref_id": review.id,
                    "rating": review.rating,
                    "snippet": (review.content or "")[:80],
                    "distance": round(0.21 + 0.05 * pos, 3),
                }
                for pos, review in enumerate(product_reviews[:evidence_count])
            ]
            actions = [{
                "type": constants.ACTION_SAVE_REPORT,
                "label": constants.ACTION_LABELS[constants.ACTION_SAVE_REPORT],
                "status": "executed",
            }]
            if risk == constants.RISK_HIGH:
                actions.append({
                    "type": constants.ACTION_SEND_ALERT,
                    "label": constants.ACTION_LABELS[constants.ACTION_SEND_ALERT],
                    "status": "executed",
                })
                if pid == PENDING_REVIEW_PRODUCT:
                    actions.append({
                        "type": constants.ACTION_MARK_PENDING,
                        "label": constants.ACTION_LABELS[constants.ACTION_MARK_PENDING],
                        "status": "executed",
                    })
            db.add(DiagnosisReport(
                question=question,
                product_id=pid,
                risk=risk,
                evidence_count=len(evidence),
                summary=summary,
                evidence_json=json.dumps(evidence, ensure_ascii=False),
                actions_json=json.dumps(actions, ensure_ascii=False),
                run_id=runs[idx].id if idx < len(runs) else None,
                created_at=_at(anchor, 20 - idx, 11 + (idx % 8), 5),
            ))

        # ---------------- 站内通知 ----------------
        for idx, (title, content, level, read, pid) in enumerate(NOTIFICATIONS):
            db.add(Notification(
                title=title,
                content=content,
                level=level,
                read=read,
                product_id=pid,
                created_at=_at(anchor, idx, 9 + idx, 30),
            ))

        db.commit()

        summary = {
            "users": 2,
            "products": len(products),
            "reviews": len(reviews),
            "orders": len(orders),
            "paid_total_yuan": round(paid_total / 100, 2),
            "all_total_yuan": round(sum(o.amount_cents for o in orders) / 100, 2),
            "runs": len(runs),
            "reports": len(REPORTS),
            "notifications": len(NOTIFICATIONS),
        }
        return summary
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover - 手工执行入口
    print(seed_all())
