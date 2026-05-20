"""
智能选课排课系统 - FastAPI 后端
"""
import os, sys, json, uuid
import time
from typing import Optional, Dict
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from database import DB_NAME
from data_adapter import find_course_ids, fetch_course_context
from session_manager import SessionManager
from scheduler import CourseScheduler
from llm_agent import run_agent, _get_constraints, ALIAS_MAP

app = FastAPI(title="未小羊 · 智能选课系统")
app.mount("/static", StaticFiles(directory="static"), name="static")

sessions: Dict[str, dict] = {}

SESSION_TTL_SECONDS = 60 * 30
MAX_SESSIONS = 500
MAX_TURNS_PER_SESSION = 200
MAX_HISTORY_MESSAGES = 40

def format_solution(schedule_result: dict) -> str:
    dn = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    tag_map = {"COMPACT": "课表更紧凑", "高学分保障": "高学分保障"}

    schedule = (schedule_result or {}).get("schedule") or []
    if not schedule:
        return "当前没有可展示的课表。"

    courses = {}
    credits_by_course = {}
    for c in schedule:
        cid = c.get("course_id") or ""
        key = (cid, c.get("section_id") or "")
        if key not in courses:
            courses[key] = {"course_name": c.get("course_name") or cid, "teacher": c.get("teacher") or "", "location": c.get("location") or "", "slots": {}}
        w = c.get("weekday")
        pls = c.get("period_list") or []
        if isinstance(w, int) and 1 <= w <= 7 and isinstance(pls, list):
            s = courses[key]["slots"].setdefault(w, set())
            for p in pls:
                try:
                    s.add(int(p))
                except (TypeError, ValueError):
                    pass
        try:
            credits_by_course[cid] = float(c.get("credits") or 0)
        except (TypeError, ValueError):
            credits_by_course[cid] = 0

    tags = (schedule_result or {}).get("tags") or []
    tags_cn = [tag_map.get(t, t) for t in tags]
    total_credits = schedule_result.get("total_credits")
    if total_credits is None:
        total_credits = sum(credits_by_course.values())

    lines = []
    head = "排课成功"
    if tags_cn:
        head += " · " + " / ".join(tags_cn)
    head += f"\n总学分：{total_credits}"
    lines.append(head)

    extra_notes = []
    for c in (schedule_result or {}).get("conflict_log") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("SECTION_FILTER_RELAXED", "SECTION_FILTER_NO_MATCH", "CONSTRAINT_RELAXED", "PARTIAL_SOLVE_SUMMARY"):
            d = (c.get("description") or "").strip()
            if d:
                extra_notes.append(d)
    if extra_notes:
        lines.append("\n".join(extra_notes))

    for (_, _), info in sorted(courses.items(), key=lambda kv: kv[1].get("course_name", "")):
        parts = []
        is_early = False
        for w in sorted(info["slots"].keys()):
            ps = sorted(info["slots"][w])
            if ps:
                if any(x in (1, 2) for x in ps):
                    is_early = True
                parts.append(f"{dn[w]}第{','.join(str(x) for x in ps)}节")
        time_str = "；".join(parts) if parts else "时间未知"
        who_where = " · ".join([x for x in [info["teacher"], info["location"]] if x])
        if who_where:
            lines.append(f"{info['course_name']}（{who_where}）：{time_str}" + ("（早八）" if is_early else ""))
        else:
            lines.append(f"{info['course_name']}：{time_str}" + ("（早八）" if is_early else ""))

    return "\n".join(lines)

def _schedule_snapshot_payload(schedule_result: dict) -> dict:
    schedule = (schedule_result or {}).get("schedule") or []
    courses = {}
    for c in schedule:
        cid = c.get("course_id") or ""
        sid = c.get("section_id") or ""
        key = (cid, sid)
        if key not in courses:
            courses[key] = {
                "course_id": cid,
                "course_name": c.get("course_name") or cid,
                "section_id": sid,
                "teacher": c.get("teacher") or "",
                "location": c.get("location") or "",
                "time_slots": [],
            }
        w = c.get("weekday")
        pls = c.get("period_list") or []
        if isinstance(w, int) and isinstance(pls, list):
            courses[key]["time_slots"].append({"weekday": w, "period_list": [int(p) for p in pls if isinstance(p, int)]})
    return {
        "success": bool((schedule_result or {}).get("success")),
        "tags": (schedule_result or {}).get("tags") or [],
        "total_credits": (schedule_result or {}).get("total_credits"),
        "courses": list(courses.values()),
    }

def _set_schedule_memory(sess: dict, schedule_result: dict | None):
    history = sess.get("history")
    if not isinstance(history, list):
        history = []
    history = [m for m in history if not (isinstance(m, dict) and m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith("【课表快照】"))]
    sess["history"] = history
    sess["schedule_memory"] = None
    if not schedule_result or not schedule_result.get("success"):
        return
    payload = _schedule_snapshot_payload(schedule_result)
    content = "【课表快照】以下是系统权威课表数据（只能使用这里出现的weekday与period_list生成时间描述，禁止编造任何时间信息）：\n" + json.dumps(payload, ensure_ascii=False)
    sess["schedule_memory"] = content
    sess["history"].append({"role": "system", "content": content})

def _apply_user_availability_override(message: str, sess: dict):
    import re
    text = (message or "").strip()
    m = re.search(r"周([一二三四五六日天]).{0,6}(有空|可以|能上|能上课|没问题|空闲|都行)", text)
    if not m:
        return
    ch = m.group(1)
    wd_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    wd = wd_map.get(ch)
    if not wd:
        return
    sm = sess.get("sm")
    if not sm:
        return
    before = list(sm.requirements.get("forbidden_slots") or [])
    sm.requirements["forbidden_slots"] = [s for s in before if s.get("weekday") != wd]
    _invalidate_session_cache(sess)

def _compute_schedule_for_session(sess: dict) -> dict | None:
    sm = sess.get("sm")
    if not sm or not sm.requirements.get("must_have"):
        return None
    sm.conflict_log = []
    solver = CourseScheduler(sm)
    analyzed = solver.solve(max_solutions=3)
    if not analyzed:
        return None

    def format_schedule(details):
        out = []
        credit_map = {}
        for item in details:
            credit_map[item["course_id"]] = float(item.get("credits") or 0)
            cr = float(item.get("credits") or 0)
            for ts in item.get("time_slots", []):
                out.append({
                    "course_id": item["course_id"],
                    "course_name": item["course_name"],
                    "teacher": item.get("teacher", ""),
                    "location": item.get("location", ""),
                    "credits": cr,
                    "weekday": ts.get("weekday"),
                    "period_list": ts.get("period_list") or [],
                    "weeks": ts.get("weeks", ""),
                    "constraint_relaxed": item.get("constraint_relaxed", False),
                    "section_id": item.get("section_id", ""),
                })
        return out, sum(credit_map.values())

    solutions = []
    if isinstance(analyzed, list) and analyzed and isinstance(analyzed[0], dict) and "details" in analyzed[0]:
        for sol in analyzed:
            sched, total = format_schedule(sol.get("details") or [])
            solutions.append({"rank": sol.get("rank", len(solutions) + 1), "tags": sol.get("tags", []) or [], "total_credits": total, "schedule": sched})
        best_details = analyzed[0].get("details") or []
        best_tags = analyzed[0].get("tags", []) or []
    else:
        best_details = analyzed
        best_tags = []
        sched, total = format_schedule(best_details)
        solutions = [{"rank": 1, "tags": [], "total_credits": total, "schedule": sched}]

    best_sched, best_total = format_schedule(best_details)
    result = {
        "success": True,
        "schedule": best_sched,
        "solutions": solutions,
        "tags": best_tags,
        "total_credits": best_total,
        "conflict_log": [{"type": c["type"], "description": c["description"]} for c in sm.conflict_log],
    }
    sess["last_schedule"] = result
    sess["last_solution_details"] = best_details
    _set_schedule_memory(sess, result)
    return result

def _now_ts() -> float:
    return time.time()

def _touch_session(sess: dict, is_chat: bool = False):
    sess["last_active_ts"] = _now_ts()
    if is_chat:
        sess["turn_count"] = int(sess.get("turn_count") or 0) + 1

def _invalidate_session_cache(sess: dict, clear_history: bool = False):
    sm = sess.get("sm")
    if sm:
        sm.invalidate_cache()
        sm.conflict_log = []
    sess["last_schedule"] = None
    sess["last_solution_details"] = None
    sess["schedule_memory"] = None
    if isinstance(sess.get("history"), list):
        sess["history"] = [m for m in sess["history"] if not (isinstance(m, dict) and m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith("【课表快照】"))]
    if clear_history:
        sess["history"] = []

def _cleanup_sessions():
    if not sessions:
        return
    now = _now_ts()
    expired = []
    for sid, sess in sessions.items():
        last_active = float(sess.get("last_active_ts") or 0)
        if last_active and (now - last_active) > SESSION_TTL_SECONDS:
            expired.append(sid)
            continue
        if int(sess.get("turn_count") or 0) > MAX_TURNS_PER_SESSION:
            expired.append(sid)
    for sid in expired:
        sessions.pop(sid, None)
    if len(sessions) > MAX_SESSIONS:
        victims = sorted(sessions.items(), key=lambda kv: float(kv[1].get("last_active_ts") or 0))
        for sid, _ in victims[: max(0, len(sessions) - MAX_SESSIONS)]:
            sessions.pop(sid, None)

def get_or_create_session(sid=None, user_info=None):
    if not sid: sid = str(uuid.uuid4())[:8]
    if sid not in sessions:
        sessions[sid] = {"sm": SessionManager(sid), "history": [], "last_schedule": None, "last_solution_details": None, "user_info": user_info or {}, "last_active_ts": _now_ts(), "turn_count": 0}
    elif user_info:
        sessions[sid]["user_info"].update(user_info)
    return sid, sessions[sid]

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_info: Optional[dict] = None

class ConstraintUpdateRequest(BaseModel):
    session_id: str
    action: str
    data: dict = {}

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/health")
async def health():
    return {"status": "ok", "db_ready": os.path.exists(DB_NAME)}

@app.get("/api/courses")
async def get_courses(keyword: str = "", limit: int = 50):
    """课程搜索（弹窗用）— 不显示余量(#10)"""
    import sqlite3
    keyword = ALIAS_MAP.get(keyword, keyword)
    conn = sqlite3.connect(DB_NAME); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    if keyword:
        like = f"%{keyword}%"
        cur.execute('SELECT "课程号","课程名","教师名1","学分","开课单位" FROM course_details WHERE "课程号" LIKE ? OR "课程名" LIKE ? OR "教师名1" LIKE ? GROUP BY "课程号" LIMIT ?', (like,like,like,limit))
    else:
        cur.execute('SELECT "课程号","课程名","教师名1","学分","开课单位" FROM course_details GROUP BY "课程号" LIMIT ?', (limit,))
    rows = cur.fetchall(); conn.close()
    return {"courses": [{"course_id":r["课程号"],"course_name":r["课程名"],"teacher":r["教师名1"] or "","credits":r["学分"] or 0,"department":r["开课单位"] or ""} for r in rows]}

@app.get("/api/course_detail/{course_id}")
async def course_detail(course_id: str):
    bundles = fetch_course_context([course_id])
    if not bundles: return {"found": False}
    b = bundles[0]; dn = ["","周一","周二","周三","周四","周五","周六","周日"]
    secs = []
    for sec in b.get("sections",[]):
        ts_out = [{"weekday":ts["weekday"],"day":dn[ts["weekday"]] if 1<=ts["weekday"]<=7 else f"周{ts['weekday']}","period_list":ts["period_list"]} for ts in sec.get("time_slots",[])]
        secs.append({"section_id":sec["section_id"],"teacher":sec["teacher"],"location":sec["location"],"time_slots":ts_out,"credits":sec.get("credits",0)})
    return {"found":True,"course_id":b["course_id"],"course_name":b["course_name"],"sections":secs}

@app.post("/api/chat")
async def chat(req: ChatRequest):
    _cleanup_sessions()
    sid, sess = get_or_create_session(req.session_id, req.user_info)
    _touch_session(sess, is_chat=True)
    _apply_user_availability_override(req.message, sess)
    result = run_agent(req.message, sess)
    try:
        fs = sess.get("sm").requirements.get("forbidden_slots") if sess.get("sm") else None
        print(f"[Chat] sid={sid} forbidden_slots={fs}")
    except Exception:
        pass
    if isinstance(sess.get("history"), list) and len(sess["history"]) > MAX_HISTORY_MESSAGES:
        sess["history"] = sess["history"][-MAX_HISTORY_MESSAGES:]
    sched = result.get("schedule")
    tool_calls = result.get("tool_calls") or []
    did_solve = any(tc.get("tool") in ("solve_schedule", "resolve_conflict_by_user") for tc in tool_calls if isinstance(tc, dict))
    did_update = any(tc.get("tool") in ("update_requirements", "switch_course_section", "reset_selection") for tc in tool_calls if isinstance(tc, dict))
    if did_update and not did_solve:
        new_sched = _compute_schedule_for_session(sess)
        if new_sched:
            sched = new_sched
            result["schedule"] = new_sched
    if isinstance(sched, dict) and sched.get("success") and isinstance(sched.get("schedule"), list):
        reply = format_solution(sched)
        result["reply"] = reply
        try:
            if isinstance(sess.get("history"), list) and sess["history"] and sess["history"][-1].get("role") == "assistant":
                sess["history"][-1]["content"] = reply
        except Exception:
            pass
    return {"session_id": sid, "reply": result.get("reply",""), "schedule": result.get("schedule"), "constraints": result.get("constraints",{}), "tool_calls": result.get("tool_calls",[])}

@app.post("/api/constraints/update")
async def update_constraints(req: ConstraintUpdateRequest):
    _cleanup_sessions()
    if req.session_id not in sessions: return {"success":False,"message":"会话不存在"}
    sess = sessions[req.session_id]; sm = sess["sm"]
    _touch_session(sess, is_chat=False)
    if req.action == "add_course":
        cid = req.data.get("course_id","")
        if cid and cid not in sm.requirements["must_have"]:
            sm.update_requirements({"must_have":[cid]})
            _invalidate_session_cache(sess)
    elif req.action == "remove_course":
        cid = req.data.get("course_id","")
        sm.requirements["must_have"] = [c for c in sm.requirements["must_have"] if c != cid]
        _invalidate_session_cache(sess)
    elif req.action == "add_forbidden":
        slot = req.data.get("slot"); 
        if slot:
            sm.update_requirements({"forbidden_slots":[slot]})
            _invalidate_session_cache(sess)
    elif req.action == "remove_forbidden":
        wd = req.data.get("weekday")
        sm.requirements["forbidden_slots"] = [s for s in sm.requirements["forbidden_slots"] if s["weekday"] != wd]
        _invalidate_session_cache(sess)
    elif req.action == "remove_forbidden_periods":
        try:
            wd = int(req.data.get("weekday"))
        except (TypeError, ValueError):
            wd = None
        periods = req.data.get("period") or []
        if wd and periods:
            try:
                remove_set = {int(p) for p in periods}
            except (TypeError, ValueError):
                remove_set = set()
            next_slots = []
            for s in sm.requirements.get("forbidden_slots", []):
                if s.get("weekday") != wd:
                    next_slots.append(s)
                    continue
                kept = [p for p in (s.get("period") or []) if p not in remove_set]
                if kept:
                    next_slots.append({"weekday": wd, "period": kept})
            sm.requirements["forbidden_slots"] = next_slots
            _invalidate_session_cache(sess)
    elif req.action == "remove_preference":
        key = req.data.get("key")
        if key and isinstance(sm.requirements.get("preferences"), dict):
            sm.requirements["preferences"].pop(key, None)
            _invalidate_session_cache(sess)
    elif req.action == "remove_section_constraint":
        cid = req.data.get("course_id", "")
        if cid and isinstance(sm.requirements.get("section_constraints"), dict):
            sm.requirements["section_constraints"].pop(cid, None)
            _invalidate_session_cache(sess)
    elif req.action == "clear_all":
        sm.reset_all()
        _invalidate_session_cache(sess, clear_history=True)
    schedule = None
    if req.action != "clear_all" and sm.requirements.get("must_have"):
        schedule = _compute_schedule_for_session(sess)
    return {"success":True,"constraints":_get_constraints(sess), "schedule": schedule}

@app.post("/api/schedule/solve")
async def solve_now(session_id: str = ""):
    _cleanup_sessions()
    if session_id not in sessions: return {"success":False,"message":"会话不存在"}
    sess = sessions[session_id]; sm = sess["sm"]
    _touch_session(sess, is_chat=False)
    if not sm.requirements["must_have"]: return {"success":False,"message":"还没有添加课程"}
    result = _compute_schedule_for_session(sess)
    if not result:
        return {"success":False,"conflict_log":[{"type":c["type"],"description":c["description"]} for c in sm.conflict_log]}
    return result

@app.post("/api/session/reset")
async def reset_session(session_id: str = ""):
    _cleanup_sessions()
    if session_id in sessions:
        ui = sessions[session_id].get("user_info",{})
        sessions[session_id] = {"sm":SessionManager(session_id),"history":[],"last_schedule":None,"last_solution_details":None,"user_info":ui, "last_active_ts": _now_ts(), "turn_count": 0}
    return {"success":True}

if __name__ == "__main__":
    import uvicorn; uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
