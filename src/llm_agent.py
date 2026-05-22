"""
LLM Agent：基于 OpenAI Function Calling 的工具调用 Agent
角色：未小羊 🐏 —— 清华大学未央书院吉祥物
"""
import os, json, traceback
from openai import OpenAI
from dotenv import load_dotenv

# 尝试多个位置加载 .env（兼容 Windows 路径和不同启动目录）
_env_candidates = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),  # src/../.env
    os.path.join(os.getcwd(), ".env"),  # 当前工作目录
    ".env",
]
for _p in _env_candidates:
    if os.path.exists(_p):
        load_dotenv(dotenv_path=_p, override=False)
        break

_api_key = os.getenv("LLM_API_KEY", "")
if not _api_key or "请填入" in _api_key:
    print("[WARNING] LLM_API_KEY 未设置！请在 .env 文件中填入你的 API 密钥。")

client = OpenAI(
    api_key=_api_key,
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
)
MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

ALIAS_MAP = {
    "高数": "微积分A(2)", "高等数学": "微积分A(2)",
    "大英": "大学英语(2)", "大学英语": "大学英语(2)",
    "线代": "线性代数", "概率论": "概率论与数理统计",
    "概统": "概率论与数理统计",
    "马原": "马克思主义基本原理概论",
    "毛概": "毛泽东思想和中国特色社会主义理论体系概论",
    "体育": "体育(2)",
}

# ─── 系统提示词（含 few-shot、冲突定义、多任务、软约束） ───
SYSTEM_PROMPT = r"""你是"未小羊"🐏，清华大学未央书院的吉祥物，专业的智能选课助手。

## 性格
热情友好，语气活泼，偶尔用"咩~"。专业高效，耐心引导。

## 工具
1. search_courses(query) — 搜索课程
2. update_requirements(...) — 添加/移除课程、设置/移除禁用时间和偏好
3. solve_schedule() — 排课引擎生成课表
4. get_course_detail(course_id) — 查看课程详情
5. reset_selection() — 清空所有选课需求
6. switch_course_section(...) — 更换已选课程的班级
7. list_conflicts() — 列出当前所选课程之间的所有时间冲突
8. resolve_conflict_by_user(keep_course_id, remove_course_id) — 用户选择保留/移除冲突课程并自动重排

## 工作流
用户说话 → 你理解意图 → 调用一个或多个工具 → 根据结果生成回复

## 多任务处理（重要！）
用户经常在一句话中表达多个需求，你必须全部处理。例如：
- "选建筑与城市美学和风景之道，不想上早八" → 搜索两门课 → 更新需求 → 排课

## 冲突处理（核心！不要自作主张剔除课程！）
"冲突"指的是用户已选的课程之间存在时间重叠。
当排课引擎因冲突失败时，你必须：
1) 调用 list_conflicts() 获取具体冲突对
2) 向用户清楚列出哪些课程冲突、在什么时间冲突
3) 让用户选择保留哪门、去掉哪门，绝不自作主张自动剔除
4) 用户回复后，调用 resolve_conflict_by_user(keep_course_id, remove_course_id)

## 硬约束 vs 软约束
- 硬约束（forbidden_slots）：用户明确说"不要""不上""禁用"的时间。违反则方案无效。
- 软约束（preferences）：用户说"尽量""最好""希望""偏好"。违反不影响方案有效性。

## 约束修改与覆盖（以最新对话为准！）
用户可以随时修改或取消之前的约束，你必须以最新指令为准：
- 取消禁用时间："其实周一也可以上课""取消周一的限制" → update_requirements(remove_forbidden_slots=[{"weekday":1}])
- 取消偏好："不用管早八了""早八也行" → update_requirements(remove_preferences=["no_morning"])
- 修改偏好："改成希望在五教上课" → update_requirements(preferences={"preferred_building":"五教"})
- 删除课程："去掉XX""不选XX了" → update_requirements(remove_course_ids=[...])
每次修改约束后系统会自动重新排课，你不需要再手动调用 solve_schedule。

## 更换班级
当用户说"把某门课换一个班级/换老师/换到某个时间"时：
调用 switch_course_section，可传入 teacher_contains/location_contains/time_filter 筛选。

## 时间解析规则（few-shot）
课表节次映射关系：原始第1-2节 = 第1大节(08:00-09:35)，第3-4节 = 第2大节(09:55-11:30)，第5-6节 = 第3大节(13:30-15:05)，第7-8节 = 第4大节(15:25-17:00)，第9-10节 = 第5大节(18:00-19:35)，第11-12节 = 第6大节(19:55-21:30)。
"早八" = 原始第1-2节（每天），"上午" = 原始第1-4节，"下午" = 原始第5-8节，"晚上" = 原始第9-12节
"不想上早八" = 周一到周五的 period:[1,2]
"周三下午" = weekday:3, period:[5,6,7,8]

## 别名
高数→微积分A(2)，大英→大学英语(2)，线代→线性代数，概统→概率论与数理统计

## 严禁提及的内容
- 绝不提及"余量""剩余容量""名额"等概念，用户不关心这些
- 绝不提及"周五课更少"等无关推荐理由
- 只关注课程本身：时间、教师、地点、学分

## 非选课问题处理
对于与选课排课无关的问题（如天气、闲聊、知识问答等）：
- 简单常识问题可以简短回答，保持未小羊的活泼人设
- 然后友好地引导回选课话题："不过我最擅长的还是帮你选课排课哦～有需要随时告诉我咩 🐏"
- 不要生硬地拒绝或报错

## 回复规范
1. 确认理解 → 2. 执行操作 → 3. 展示结果/解释冲突
不要在同一条回复里继续推进"下一步"对话；仅回答当前用户这一次输入涉及的内容。
不要使用 Markdown 格式标记（如 ** # - 等），用纯文本回复，可以用 emoji 和换行让内容清晰。

## 开场白
首次交流："你好呀！我是未小羊 🐏，你的专属选课小助手～我可以帮你搜课、排课、解决冲突，有什么选课问题尽管问我咩～"
"""

# ─── 工具定义 ───
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_courses",
            "description": "根据关键词搜索课程（课程名、教师名、课程号均可）。返回匹配的课程列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_requirements",
            "description": "更新选课需求。可同时添加课程、设置禁用时间、配置偏好。所有字段都是可选的，只传需要更新的部分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "要添加的课程号列表"
                    },
                    "remove_course_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "要移除的课程号列表"
                    },
                    "remove_forbidden_slots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "weekday": {"type": "integer", "description": "1=周一...7=周日"},
                                "period": {"type": "array", "items": {"type": "integer"}, "description": "要移除的节次列表；不传表示移除该weekday的全部禁用"}
                            },
                            "required": ["weekday"]
                        },
                        "description": "删除硬约束：从禁用时间槽中移除指定节次或整天"
                    },
                    "forbidden_slots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "weekday": {"type": "integer", "description": "1=周一...7=周日"},
                                "period": {"type": "array", "items": {"type": "integer"}, "description": "节次列表"}
                            }
                        },
                        "description": "硬约束：禁用时间槽"
                    },
                    "preferences": {
                        "type": "object",
                        "description": "软约束偏好，如 {\"no_morning\": true, \"preferred_building\": \"六教\", \"max_days\": 3}"
                    },
                    "remove_preferences": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要移除的软约束偏好 key 列表（如 no_morning, preferred_building, max_days）"
                    },
                    "blacklist_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要加入黑名单的原始行索引列表（用于换班、排除某个班级）"
                    },
                    "section_constraints": {
                        "type": "object",
                        "description": "按老师锁定班级：{ \"课程号\": {\"teacher_contains\":\"王辉\",\"strict\":true} }。strict=true 表示必须匹配该老师，否则排课失败；strict=false 表示匹配不到则自动改用同课程号其他老师，并记录原因。",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "teacher_contains": {"type": "string"},
                                "strict": {"type": "boolean"}
                            }
                        }
                    },
                    "remove_section_constraints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "移除某些课程的老师锁定（传课程号列表）"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "solve_schedule",
            "description": "调用排课引擎生成课表方案。引擎采用三阶段策略：完美匹配→约束松弛→贪心舍弃。需要先通过update_requirements添加课程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_solutions": {"type": "integer", "description": "最多返回方案数，默认3"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_course_detail",
            "description": "获取某门课程的详细信息，包括所有班级的教师、时间、地点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string", "description": "课程号"}
                },
                "required": ["course_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reset_selection",
            "description": "清空所有选课需求和约束，重新开始。当用户说'重新来'、'清空'、'从头开始'时调用。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "switch_course_section",
            "description": "更换已选课程的班级（老师/时间/地点可筛选）。优先在不影响其他课程的前提下更换；必要时会触发重新排课。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {"type": "string", "description": "课程号（已知则传）"},
                    "course_name": {"type": "string", "description": "课程名（不知道课程号时传）"},
                    "teacher_contains": {"type": "string", "description": "教师名包含（可选）"},
                    "location_contains": {"type": "string", "description": "上课地点包含（可选）"},
                    "time_filter": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "weekday": {"type": "integer", "description": "1=周一...7=周日"},
                                "period": {"type": "array", "items": {"type": "integer"}, "description": "期望包含的节次列表（至少包含其中一个即可）"}
                            },
                            "required": ["weekday", "period"]
                        },
                        "description": "时间筛选（可选）：候选班级需至少有一个 time_slot 命中"
                    },
                    "strict": {"type": "boolean", "description": "严格模式：有筛选条件时，只允许选择满足条件的班级（默认false）"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_conflicts",
            "description": "列出当前已选课程之间的所有时间冲突对。返回哪些课程在什么时间冲突，以及建议。当排课失败或用户问'为什么排不了'时调用。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_conflict_by_user",
            "description": "根据用户的选择解决冲突：保留指定课程，移除另一门冲突课程，然后自动重新排课。当用户明确说'保留A去掉B'或'选A不选B'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keep_course_id": {"type": "string", "description": "要保留的课程号"},
                    "remove_course_id": {"type": "string", "description": "要移除的课程号"}
                },
                "required": ["keep_course_id", "remove_course_id"]
            }
        }
    },
]


# ─── 工具执行 ───
def execute_tool(tool_name: str, arguments: dict, session_state: dict) -> str:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from data_adapter import find_course_ids, fetch_course_context
    from session_manager import SessionManager
    from scheduler import CourseScheduler

    sm: SessionManager = session_state["sm"]

    try:
        if tool_name == "search_courses":
            query = ALIAS_MAP.get(arguments.get("query", ""), arguments.get("query", ""))
            course_ids = find_course_ids(query)
            if not course_ids:
                return json.dumps({"found": False, "message": f"未找到与'{query}'相关的课程，请检查名称。"}, ensure_ascii=False)
            results = []
            for cid in course_ids[:8]:
                bundles = fetch_course_context([cid])
                for b in bundles:
                    secs = []
                    for sec in b.get("sections", []):
                        day_names = ["","周一","周二","周三","周四","周五","周六","周日"]
                        times = []
                        for ts in sec.get("time_slots", []):
                            d = day_names[ts["weekday"]] if 1<=ts["weekday"]<=7 else f"周{ts['weekday']}"
                            times.append(f"{d}第{ts['period_list']}节")
                        secs.append({
                            "section_id": sec["section_id"],
                            "teacher": sec["teacher"],
                            "location": sec["location"],
                            "time": ", ".join(times),
                            "credits": sec.get("credits", 0),
                        })
                    results.append({"course_id": b["course_id"], "course_name": b["course_name"], "sections": secs})
            return json.dumps({"found": True, "courses": results, "total": len(course_ids)}, ensure_ascii=False)

        elif tool_name == "update_requirements":
            # 移除
            for cid in arguments.get("remove_course_ids", []):
                sm.requirements["must_have"] = [c for c in sm.requirements["must_have"] if c != cid]
                sm.invalidate_cache()
                session_state["last_schedule"] = None
                session_state["last_solution_details"] = None
            for rm in arguments.get("remove_forbidden_slots", []) or []:
                try:
                    wd = int(rm.get("weekday"))
                except (TypeError, ValueError):
                    continue
                periods = rm.get("period", None)
                if not periods:
                    sm.requirements["forbidden_slots"] = [s for s in sm.requirements["forbidden_slots"] if s.get("weekday") != wd]
                    sm.invalidate_cache()
                    session_state["last_schedule"] = None
                    session_state["last_solution_details"] = None
                    continue
                try:
                    remove_set = {int(p) for p in periods}
                except (TypeError, ValueError):
                    continue
                next_slots = []
                for s in sm.requirements["forbidden_slots"]:
                    if s.get("weekday") != wd:
                        next_slots.append(s)
                        continue
                    kept = [p for p in (s.get("period") or []) if p not in remove_set]
                    if kept:
                        next_slots.append({"weekday": wd, "period": kept})
                sm.requirements["forbidden_slots"] = next_slots
                sm.invalidate_cache()
                session_state["last_schedule"] = None
                session_state["last_solution_details"] = None
            for k in arguments.get("remove_preferences", []) or []:
                if k in sm.requirements.get("preferences", {}):
                    sm.requirements["preferences"].pop(k, None)
                    sm.invalidate_cache()
                    session_state["last_schedule"] = None
                    session_state["last_solution_details"] = None
            # 添加
            update = {}
            if arguments.get("course_ids"):
                from database import get_slots_by_course
                valid = []
                invalid = []
                for cid in arguments["course_ids"]:
                    if not isinstance(cid, str) or not cid.strip():
                        continue
                    slots = get_slots_by_course(cid)
                    if slots:
                        valid.append(cid)
                    else:
                        invalid.append(cid)
                if valid:
                    update["must_have"] = valid
            if arguments.get("forbidden_slots"):
                update["forbidden_slots"] = arguments["forbidden_slots"]
            if arguments.get("preferences"):
                update["preferences"] = arguments["preferences"]
            if arguments.get("blacklist_indices"):
                update["blacklist_indices"] = arguments["blacklist_indices"]
            if arguments.get("section_constraints"):
                update["section_constraints"] = arguments["section_constraints"]
            if arguments.get("remove_section_constraints"):
                update["remove_section_constraints"] = arguments["remove_section_constraints"]
            if update:
                sm.update_requirements(update)
                sm.invalidate_cache()
                session_state["last_schedule"] = None
                session_state["last_solution_details"] = None
            # 获取课程名
            names = {}
            for cid in sm.requirements["must_have"]:
                bs = fetch_course_context([cid])
                names[cid] = bs[0]["course_name"] if bs else cid
            return json.dumps({
                "must_have": [{"id": cid, "name": names.get(cid, cid)} for cid in sm.requirements["must_have"]],
                "forbidden_slots": sm.requirements["forbidden_slots"],
                "preferences": sm.requirements["preferences"],
                "message": "需求已更新。" + (f"（已忽略不存在的课程号：{','.join(invalid)}）" if arguments.get("course_ids") and 'invalid' in locals() and invalid else "")
            }, ensure_ascii=False)

        elif tool_name == "solve_schedule":
            if not sm.requirements["must_have"]:
                return json.dumps({"success": False, "message": "还没有添加课程，请先选课。"}, ensure_ascii=False)
            sm.conflict_log = []
            solver = CourseScheduler(sm)
            raw = solver.solve(max_solutions=arguments.get("max_solutions", 3))
            if not raw:
                return json.dumps({
                    "success": False, "message": "排课失败。",
                    "conflict_log": [{"type": c["type"], "description": c["description"]} for c in sm.conflict_log],
                }, ensure_ascii=False)
            def _format_schedule(details: list):
                courses_out = []
                credit_map = {}
                for item in details:
                    credit_map[item["course_id"]] = float(item.get("credits") or 0)
                    for ts in item.get("time_slots", []):
                        courses_out.append({
                            "course_id": item["course_id"], "course_name": item["course_name"],
                            "teacher": item.get("teacher", ""), "location": item.get("location", ""),
                            "credits": float(item.get("credits") or 0),
                            "weekday": ts.get("weekday"), "period_list": ts.get("period_list") or [],
                            "weeks": ts.get("weeks", ""),
                            "constraint_relaxed": item.get("constraint_relaxed", False),
                            "section_id": item.get("section_id", ""),
                        })
                return courses_out, sum(credit_map.values())

            solutions = []
            if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "details" in raw[0]:
                for sol in raw:
                    details = sol.get("details") or []
                    schedule_out, total_credits = _format_schedule(details)
                    solutions.append({
                        "rank": sol.get("rank", len(solutions) + 1),
                        "tags": sol.get("tags", []) or [],
                        "total_credits": total_credits,
                        "schedule": schedule_out,
                    })
                best = raw[0]
                best_details = best.get("details") or []
                best_schedule, best_total = _format_schedule(best_details)
                best_tags = best.get("tags", []) or []
            else:
                best_details = raw
                best_schedule, best_total = _format_schedule(best_details)
                best_tags = best_details[0].get("solution_tags", []) if best_details else []
                solutions = [{"rank": 1, "tags": best_tags, "total_credits": best_total, "schedule": best_schedule}]

            result = {
                "success": True,
                "schedule": best_schedule,
                "solutions": solutions,
                "tags": best_tags,
                "total_credits": best_total,
                "conflict_log": [{"type": c["type"], "description": c["description"]} for c in sm.conflict_log],
                "message": f"排课成功！共{len(set(c['course_id'] for c in best_schedule))}门课，{best_total}学分。"
            }
            session_state["last_schedule"] = result
            session_state["last_solution_details"] = best_details
            return json.dumps(result, ensure_ascii=False)

        elif tool_name == "get_course_detail":
            cid = arguments.get("course_id", "")
            bundles = fetch_course_context([cid])
            if not bundles:
                return json.dumps({"found": False, "message": f"未找到课程 {cid}"}, ensure_ascii=False)
            b = bundles[0]
            day_names = ["","周一","周二","周三","周四","周五","周六","周日"]
            secs = []
            for sec in b.get("sections", []):
                times = []
                for ts in sec.get("time_slots", []):
                    d = day_names[ts["weekday"]] if 1<=ts["weekday"]<=7 else f"周{ts['weekday']}"
                    times.append(f"{d}第{ts['period_list']}节")
                secs.append({
                    "section_id": sec["section_id"], "teacher": sec["teacher"],
                    "location": sec["location"], "time": ", ".join(times),
                    "credits": sec.get("credits", 0),
                })
            return json.dumps({"found": True, "course_id": cid, "course_name": b["course_name"], "sections": secs}, ensure_ascii=False)

        elif tool_name == "reset_selection":
            sm.reset_all()
            session_state["last_schedule"] = None
            session_state["last_solution_details"] = None
            return json.dumps({"message": "已清空所有选课需求，可以重新开始。"}, ensure_ascii=False)

        elif tool_name == "switch_course_section":
            def _normalize(s: str) -> str:
                return (s or "").strip()

            def _matches_section(sec: dict) -> bool:
                t = _normalize(arguments.get("teacher_contains"))
                if t and t not in (sec.get("teacher") or ""):
                    return False
                loc = _normalize(arguments.get("location_contains"))
                if loc and loc not in (sec.get("location") or ""):
                    return False
                tf = arguments.get("time_filter") or []
                if not tf:
                    return True
                for want in tf:
                    try:
                        wd = int(want.get("weekday"))
                        periods = {int(p) for p in (want.get("period") or [])}
                    except (TypeError, ValueError):
                        continue
                    for ts in sec.get("time_slots", []) or []:
                        if int(ts.get("weekday") or 0) != wd:
                            continue
                        got = {int(p) for p in (ts.get("period_list") or []) if isinstance(p, (int, float, str))}
                        if got & periods:
                            return True
                return False

            def _build_fixed_masks(details: list, skip_course_id: str):
                from data_adapter import periods_to_bitmask, check_collision
                masks = {i: 0 for i in range(1, 8)}
                for fs in sm.requirements.get("forbidden_slots", []) or []:
                    try:
                        w = int(fs.get("weekday"))
                    except (TypeError, ValueError):
                        continue
                    if not (1 <= w <= 7):
                        continue
                    masks[w] |= periods_to_bitmask(fs.get("period") or [])
                for item in details:
                    if item.get("course_id") == skip_course_id:
                        continue
                    for ts in item.get("time_slots", []) or []:
                        try:
                            w = int(ts.get("weekday"))
                        except (TypeError, ValueError):
                            return None
                        if not (1 <= w <= 7):
                            return None
                        m = periods_to_bitmask(ts.get("period_list") or [])
                        if m == 0:
                            return None
                        if check_collision(masks[w], m):
                            return None
                        masks[w] |= m
                return masks

            def _section_conflicts(sec: dict, masks: dict) -> bool:
                from data_adapter import periods_to_bitmask, check_collision
                for ts in sec.get("time_slots", []) or []:
                    try:
                        w = int(ts.get("weekday"))
                    except (TypeError, ValueError):
                        return True
                    if not (1 <= w <= 7):
                        return True
                    m = periods_to_bitmask(ts.get("period_list") or [])
                    if m == 0:
                        return True
                    if check_collision(masks.get(w, 0), m):
                        return True
                return False

            def _format_schedule(details: list):
                courses_out = []
                for item in details:
                    for ts in item.get("time_slots", []) or []:
                        courses_out.append({
                            "course_id": item["course_id"], "course_name": item["course_name"],
                            "teacher": item.get("teacher", ""), "location": item.get("location", ""),
                            "credits": float(item.get("credits") or 0),
                            "weekday": ts.get("weekday"), "period_list": ts.get("period_list") or [],
                            "weeks": ts.get("weeks", ""),
                            "constraint_relaxed": item.get("constraint_relaxed", False),
                            "section_id": item.get("section_id", ""),
                        })
                credit_map = {}
                for item in details:
                    credit_map[item["course_id"]] = float(item.get("credits") or 0)
                return courses_out, sum(credit_map.values())

            last_details = session_state.get("last_solution_details")
            if not last_details:
                return json.dumps({"success": False, "message": "还没有课表方案，请先让我排一次课再换班级。"}, ensure_ascii=False)

            cid = _normalize(arguments.get("course_id"))
            if not cid:
                q = _normalize(arguments.get("course_name"))
                if not q:
                    return json.dumps({"success": False, "message": "请告诉我要换班的是哪门课（课程号或课程名）。"}, ensure_ascii=False)
                q = q.strip('"').strip("'")
                found_ids = find_course_ids(ALIAS_MAP.get(q, q))
                if not found_ids:
                    return json.dumps({"success": False, "message": f"未找到与“{q}”匹配的课程。"}, ensure_ascii=False)
                must = set(sm.requirements.get("must_have") or [])
                cid = next((x for x in found_ids if x in must), found_ids[0])

            current = next((x for x in last_details if x.get("course_id") == cid), None)
            if not current:
                return json.dumps({"success": False, "message": "当前课表里没有找到这门课，请确认课程已在课表中。"}, ensure_ascii=False)

            bundles = fetch_course_context([cid])
            if not bundles:
                return json.dumps({"success": False, "message": "课程数据缺失，暂时无法换班。"}, ensure_ascii=False)
            bundle = bundles[0]

            strict = bool(arguments.get("strict", False))
            candidates = []
            for sec in bundle.get("sections", []) or []:
                if str(sec.get("section_id")) == str(current.get("section_id")):
                    continue
                if not _matches_section(sec):
                    continue
                candidates.append(sec)

            if not candidates and not strict and any(k in arguments for k in ("teacher_contains", "location_contains", "time_filter")):
                for sec in bundle.get("sections", []) or []:
                    if str(sec.get("section_id")) == str(current.get("section_id")):
                        continue
                    candidates.append(sec)

            if not candidates:
                return json.dumps({"success": False, "message": "没有找到满足条件的其他班级。你可以换个筛选条件试试，比如指定老师/时间/地点。"}, ensure_ascii=False)

            fixed_masks = _build_fixed_masks(last_details, cid)
            if fixed_masks is None:
                fixed_masks = None

            picked = None
            for sec in sorted(candidates, key=lambda x: (-(x.get("remains") or 0), str(x.get("section_id") or ""))):
                if fixed_masks is not None and _section_conflicts(sec, fixed_masks):
                    continue
                picked = sec
                break

            if picked:
                old_rows = current.get("row_indices") or []
                if old_rows:
                    sm.update_requirements({"blacklist_indices": [int(i) for i in old_rows if isinstance(i, (int, float, str))]})

                next_details = []
                for item in last_details:
                    if item.get("course_id") != cid:
                        next_details.append(item)
                        continue
                    next_details.append({
                        **item,
                        "section_id": picked.get("section_id", ""),
                        "teacher": picked.get("teacher", ""),
                        "location": picked.get("location", ""),
                        "remains": picked.get("remains", 0),
                        "credits": picked.get("credits", item.get("credits", 0)),
                        "hours": picked.get("hours", item.get("hours", 0)),
                        "score": picked.get("score", item.get("score", 0)),
                        "is_heavy": picked.get("is_heavy", item.get("is_heavy", False)),
                        "priority_tag": picked.get("priority_tag", item.get("priority_tag", "NORMAL")),
                        "time_slots": picked.get("time_slots", []),
                        "row_indices": picked.get("row_indices", []),
                    })

                courses_out, total_credits = _format_schedule(next_details)
                result = {
                    "success": True,
                    "schedule": courses_out,
                    "tags": session_state.get("last_schedule", {}).get("tags", []),
                    "total_credits": total_credits,
                    "conflict_log": [],
                    "message": f"已为“{bundle.get('course_name', cid)}”更换班级：{picked.get('teacher','')} · {picked.get('location','')}"
                }
                session_state["last_solution_details"] = next_details
                session_state["last_schedule"] = result
                return json.dumps(result, ensure_ascii=False)

            old_rows = current.get("row_indices") or []
            if old_rows:
                sm.update_requirements({"blacklist_indices": [int(i) for i in old_rows if isinstance(i, (int, float, str))]})
            solver = CourseScheduler(sm)
            analyzed = solver.solve(max_solutions=3)
            if not analyzed:
                return json.dumps({"success": False, "message": "尝试换班失败（与其他课程/禁用时间冲突）。你可以放宽筛选条件或调整禁用时间。"}, ensure_ascii=False)
            raw_details = analyzed[0].get("details") if isinstance(analyzed, list) and analyzed and isinstance(analyzed[0], dict) else analyzed
            courses_out, total_credits = _format_schedule(raw_details)
            result = {
                "success": True,
                "schedule": courses_out,
                "tags": (analyzed[0].get("tags", []) if isinstance(analyzed, list) and analyzed and isinstance(analyzed[0], dict) else []),
                "total_credits": total_credits,
                "conflict_log": [{"type": c["type"], "description": c["description"]} for c in sm.conflict_log],
                "message": f"已尝试更换班级并重新排课：{bundle.get('course_name', cid)}"
            }
            session_state["last_solution_details"] = raw_details
            session_state["last_schedule"] = result
            return json.dumps(result, ensure_ascii=False)

        elif tool_name == "list_conflicts":
            if not sm.requirements["must_have"]:
                return json.dumps({"conflicts": [], "message": "还没有选课，无冲突可查。"}, ensure_ascii=False)
            solver = CourseScheduler(sm)
            context_bundles = fetch_course_context(sm.requirements["must_have"])
            if not context_bundles:
                return json.dumps({"conflicts": [], "message": "未找到课程数据。"}, ensure_ascii=False)
            # 从当前课表提取已选班级，使冲突检测结果与实际课表一致
            selected_section_ids = {
                item["course_id"]: item["section_id"]
                for item in (session_state.get("last_solution_details") or [])
                if item.get("course_id") and item.get("section_id")
            }
            conflicts = solver.detect_conflicts(context_bundles, selected_section_ids=selected_section_ids)
            if not conflicts:
                return json.dumps({"conflicts": [], "message": "当前所选课程之间没有时间冲突。"}, ensure_ascii=False)
            return json.dumps({
                "conflicts": conflicts,
                "message": f"检测到{len(conflicts)}个冲突，请选择保留哪门课。"
            }, ensure_ascii=False)

        elif tool_name == "resolve_conflict_by_user":
            keep_id = arguments.get("keep_course_id", "")
            remove_id = arguments.get("remove_course_id", "")
            if not keep_id or not remove_id:
                return json.dumps({"success": False, "message": "请指定要保留和要移除的课程号。"}, ensure_ascii=False)
            # 移除课程
            sm.requirements["must_have"] = [c for c in sm.requirements["must_have"] if c != remove_id]
            sm.invalidate_cache()
            session_state["last_schedule"] = None
            session_state["last_solution_details"] = None
            # 获取被移除课程名
            rm_bundles = fetch_course_context([remove_id])
            rm_name = rm_bundles[0]["course_name"] if rm_bundles else remove_id
            keep_bundles = fetch_course_context([keep_id])
            keep_name = keep_bundles[0]["course_name"] if keep_bundles else keep_id
            # 自动重新排课
            if sm.requirements["must_have"]:
                sm.conflict_log = []
                solver = CourseScheduler(sm)
                raw = solver.solve(max_solutions=3)
                if raw:
                    def _fmt(details):
                        out = []
                        cm = {}
                        for item in details:
                            cm[item["course_id"]] = float(item.get("credits") or 0)
                            for ts in item.get("time_slots", []):
                                out.append({
                                    "course_id": item["course_id"], "course_name": item["course_name"],
                                    "teacher": item.get("teacher", ""), "location": item.get("location", ""),
                                    "credits": float(item.get("credits") or 0),
                                    "weekday": ts.get("weekday"), "period_list": ts.get("period_list") or [],
                                    "weeks": ts.get("weeks", ""), "constraint_relaxed": item.get("constraint_relaxed", False),
                                    "section_id": item.get("section_id", ""),
                                })
                        return out, sum(cm.values())
                    best = raw[0] if isinstance(raw[0], dict) and "details" in raw[0] else None
                    details = best.get("details") if best else raw
                    sched, total = _fmt(details)
                    tags = (best.get("tags") if best else []) or []
                    result = {"success": True, "schedule": sched, "tags": tags, "total_credits": total,
                              "conflict_log": [{"type": c["type"], "description": c["description"]} for c in sm.conflict_log],
                              "message": f'已移除"{rm_name}"，保留"{keep_name}"，重新排课成功。'}
                    session_state["last_schedule"] = result
                    session_state["last_solution_details"] = details
                    return json.dumps(result, ensure_ascii=False)
            return json.dumps({"success": True, "message": f'已移除"{rm_name}"，保留"{keep_name}"。'}, ensure_ascii=False)

        else:
            return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"工具执行出错: {str(e)}"}, ensure_ascii=False)


# ─── 快捷指令检测 ───
SHORTCUT_PATTERNS = {
    "换一个方案": "solve",
    "重新排课": "solve",
    "再排一次": "solve",
    "重新来": "reset",
    "清空": "reset",
    "从头开始": "reset",
}

def try_shortcut(user_message: str, session_state: dict) -> dict | None:
    """检测简单指令，跳过 LLM 直接执行"""
    msg = user_message.strip()
    for pattern, action in SHORTCUT_PATTERNS.items():
        if pattern in msg:
            if action == "solve":
                result_str = execute_tool("solve_schedule", {"max_solutions": 3}, session_state)
                result = json.loads(result_str)
                if result.get("success"):
                    reply = f"好的，已重新排课！共{len(set(c['course_id'] for c in result['schedule']))}门课，{result['total_credits']}学分 🐏"
                else:
                    reply = f"排课失败：{result.get('message', '未知原因')}"
                    if result.get("conflict_log"):
                        reply += "\n" + "\n".join(f"⚡ {c['description']}" for c in result["conflict_log"])
                return {"reply": reply, "schedule": session_state.get("last_schedule"), "constraints": _get_constraints(session_state)}
            elif action == "reset":
                execute_tool("reset_selection", {}, session_state)
                session_state["history"] = []
                return {"reply": "好的，已清空所有选课，重新开始吧～ 🐏", "schedule": None, "constraints": _get_constraints(session_state)}
    return None


# ─── Agent 主循环 ───
def run_agent(user_message: str, session_state: dict) -> dict:
    history = session_state.get("history", [])

    # 快捷指令检测
    shortcut = try_shortcut(user_message, session_state)
    if shortcut:
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": shortcut["reply"]})
        session_state["history"] = history
        shortcut["tool_calls"] = []
        return shortcut

    # 构建消息
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    sm = session_state.get("sm")
    if sm:
        try:
            snapshot = {
                "must_have": list(sm.requirements.get("must_have", [])),
                "forbidden_slots": sm.requirements.get("forbidden_slots", []),
                "preferences": sm.requirements.get("preferences", {}),
            }
            messages.append({"role": "system", "content": f"当前已保存的选课需求（请严格以此为准，不要遗忘）：{json.dumps(snapshot, ensure_ascii=False)}"})
        except Exception:
            pass

    # 保留最近12条完整消息，更早的压缩为摘要
    RECENT_KEEP = 12
    if len(history) > RECENT_KEEP:
        older = history[:-RECENT_KEEP]
        # 提取早期对话中的关键用户请求作为摘要
        summary_parts = []
        for msg in older:
            if msg.get("role") == "user":
                summary_parts.append(f"用户曾说: {msg['content'][:60]}")
        if summary_parts:
            summary_text = "以下是较早的对话摘要（供参考）：\n" + "\n".join(summary_parts[-6:])  # 最多保留6条摘要
            messages.append({"role": "system", "content": summary_text})
        messages.extend(history[-RECENT_KEEP:])
    else:
        messages.extend(history)

    messages.append({"role": "user", "content": user_message})

    tool_calls_made = []

    # 预处理 tools 为纯 dict（兼容旧版 openai SDK 的 pydantic 序列化问题）
    import copy
    _tools_plain = json.loads(json.dumps(TOOLS))

    for iteration in range(15):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=_tools_plain, temperature=0.7,
            )
        except Exception as e:
            err_msg = str(e)
            if "api_key" in err_msg.lower() or "auth" in err_msg.lower() or "401" in err_msg:
                hint = "API Key 无效或未设置，请检查项目根目录的 .env 文件中的 LLM_API_KEY 是否正确填写。"
            elif "connect" in err_msg.lower() or "timeout" in err_msg.lower():
                hint = "无法连接 API 服务器，请检查网络和 .env 中的 LLM_BASE_URL 是否正确。"
            else:
                hint = f"错误详情：{err_msg}，请检查 .env 配置。"
            return {"reply": f"API 调用失败 🐏\n{hint}", "schedule": None, "constraints": _get_constraints(session_state), "tool_calls": []}

        msg = response.choices[0].message

        if not msg.tool_calls:
            final_reply = msg.content or "未小羊暂时不知道该说什么了 🐏"
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": final_reply})
            session_state["history"] = history
            return {
                "reply": final_reply,
                "schedule": session_state.get("last_schedule"),
                "constraints": _get_constraints(session_state),
                "tool_calls": tool_calls_made,
            }

        messages.append(msg)
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}
            print(f"[Agent] 工具调用: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")
            tool_result = execute_tool(fn_name, fn_args, session_state)
            tool_calls_made.append({"tool": fn_name, "args": fn_args})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

    fallback = "处理有点复杂，请重新描述一下需求～ 🐏"
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": fallback})
    session_state["history"] = history
    return {"reply": fallback, "schedule": session_state.get("last_schedule"), "constraints": _get_constraints(session_state), "tool_calls": tool_calls_made}


def _get_constraints(session_state: dict) -> dict:
    sm = session_state.get("sm")
    if not sm:
        return {"must_have": [], "forbidden_slots": [], "preferences": {}, "section_constraints": []}
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from data_adapter import fetch_course_context
    names = {}
    for cid in sm.requirements.get("must_have", []):
        bs = fetch_course_context([cid])
        names[cid] = bs[0]["course_name"] if bs else cid
    sc_out = []
    sc = sm.requirements.get("section_constraints", {}) or {}
    if isinstance(sc, dict):
        for cid, rule in sc.items():
            if not isinstance(rule, dict):
                continue
            sc_out.append({
                "course_id": cid,
                "course_name": names.get(cid, cid),
                "teacher_contains": (rule.get("teacher_contains") or ""),
                "strict": bool(rule.get("strict", True)),
            })
    return {
        "must_have": [{"course_id": cid, "course_name": names.get(cid, cid)} for cid in sm.requirements.get("must_have", [])],
        "forbidden_slots": sm.requirements.get("forbidden_slots", []),
        "preferences": sm.requirements.get("preferences", {}),
        "section_constraints": sc_out,
    }
