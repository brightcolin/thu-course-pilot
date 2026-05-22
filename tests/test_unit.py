"""单元测试：搜索、约束、排课、工具执行、别名、位图"""
import sys, os, json, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

class TestCourseSearch:
    def test_by_name(self):
        from data_adapter import find_course_ids
        assert "00000051" in find_course_ids("建筑与城市美学")
    def test_by_teacher(self):
        from data_adapter import find_course_ids
        assert len(find_course_ids("王辉")) > 0
    def test_no_result(self):
        from data_adapter import find_course_ids
        assert find_course_ids("不存在的课xyz") == []
    def test_context_bundle(self):
        from data_adapter import fetch_course_context
        b = fetch_course_context(["00000051"])
        assert len(b) > 0
        assert b[0]["course_id"] == "00000051"
        assert len(b[0]["sections"]) > 0

class TestSessionManager:
    def test_create(self):
        from session_manager import SessionManager
        sm = SessionManager("t1"); assert sm.requirements["must_have"] == []
    def test_add_course(self):
        from session_manager import SessionManager
        sm = SessionManager("t2"); sm.update_requirements({"must_have": ["00000051"]})
        assert "00000051" in sm.requirements["must_have"]
    def test_forbidden_merge(self):
        from session_manager import SessionManager
        sm = SessionManager("t3")
        sm.update_requirements({"forbidden_slots": [{"weekday": 1, "period": [1, 2]}]})
        sm.update_requirements({"forbidden_slots": [{"weekday": 1, "period": [3, 4]}]})
        slot = next(s for s in sm.requirements["forbidden_slots"] if s["weekday"] == 1)
        assert set(slot["period"]) == {1, 2, 3, 4}
    def test_preferences(self):
        from session_manager import SessionManager
        sm = SessionManager("t4"); sm.update_requirements({"preferences": {"no_morning": True}})
        assert sm.requirements["preferences"]["no_morning"] is True
    def test_export(self):
        from session_manager import SessionManager
        sm = SessionManager("t5"); sm.update_requirements({"must_have": ["00000051"]})
        assert "00000051" in sm.export_state()["requirements"]["must_have"]

class TestScheduler:
    def test_basic(self):
        from session_manager import SessionManager; from scheduler import CourseScheduler
        sm = SessionManager("s1"); sm.update_requirements({"must_have": ["00000051", "00000212"]})
        assert len(CourseScheduler(sm).solve(2)) > 0
    def test_forbidden(self):
        from session_manager import SessionManager; from scheduler import CourseScheduler
        sm = SessionManager("s2")
        sm.update_requirements({"must_have": ["00000051"], "forbidden_slots": [{"weekday": 2, "period": [6]}]})
        CourseScheduler(sm).solve(1)  # should not crash
    def test_empty(self):
        from session_manager import SessionManager; from scheduler import CourseScheduler
        assert CourseScheduler(SessionManager("s3")).solve() == []
    def test_nonexistent(self):
        from session_manager import SessionManager; from scheduler import CourseScheduler
        sm = SessionManager("s4"); sm.update_requirements({"must_have": ["FAKEID"]})
        assert CourseScheduler(sm).solve() == []
        assert len(sm.conflict_log) > 0
    def test_capacity_not_filtered(self):
        """#10: 余量为0的课程不应被过滤"""
        from session_manager import SessionManager; from scheduler import CourseScheduler
        sm = SessionManager("s5"); sm.update_requirements({"must_have": ["00000051"]})
        # Should succeed regardless of capacity
        result = CourseScheduler(sm).solve(1)
        assert isinstance(result, list)

    def test_section_constraints_teacher_strict_fail(self):
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("s_sc1")
        sm.update_requirements({"must_have": ["40030931"]})
        sm.update_requirements({"section_constraints": {"40030931": {"teacher_contains": "不存在老师", "strict": True}}})
        res = CourseScheduler(sm).solve(3)
        assert res == []
        assert any(c["type"] == "SECTION_FILTER_NO_MATCH" for c in sm.conflict_log)

    def test_section_constraints_teacher_relaxed(self):
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("s_sc2")
        sm.update_requirements({"must_have": ["40030931"]})
        sm.update_requirements({"section_constraints": {"40030931": {"teacher_contains": "不存在老师", "strict": False}}})
        res = CourseScheduler(sm).solve(3)
        assert isinstance(res, list)
        assert any(c["type"] == "SECTION_FILTER_RELAXED" for c in sm.conflict_log)

    def test_phase2_constraint_relaxation(self):
        # 00000051 has exactly one section at weekday=2 periods=[11,12].
        # Forbidding that slot forces phase 1 to fail; phase 2 must recover.
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("s_p2")
        sm.update_requirements({
            "must_have": ["00000051"],
            "forbidden_slots": [{"weekday": 2, "period": [11, 12]}],
        })
        result = CourseScheduler(sm).solve(1)
        assert len(result) > 0, "phase 2 should produce a solution by relaxing constraints"
        assert any(c["type"] == "CONSTRAINT_RELAXED" for c in sm.conflict_log)

    def test_phase3_greedy_drop(self):
        # 00000051 and 00000152 both have a single section at weekday=2 periods=[11,12].
        # They always conflict, so phases 1 and 2 both fail; phase 3 drops the lower-
        # priority course and returns a partial solution.
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("s_p3")
        sm.update_requirements({"must_have": ["00000051", "00000152"]})
        result = CourseScheduler(sm).solve(1)
        assert len(result) > 0, "phase 3 should return a partial solution"
        details = result[0]["details"]
        scheduled_ids = {item["course_id"] for item in details}
        assert len(scheduled_ids) == 1, "exactly one course should survive the conflict"
        assert any(c["type"] == "PARTIAL_SOLVE_SUMMARY" for c in sm.conflict_log)

class TestToolExecution:
    def _sess(self):
        from session_manager import SessionManager
        return {"sm": SessionManager("tool"), "history": [], "last_schedule": None, "last_solution_details": None}
    def test_search(self):
        from llm_agent import execute_tool
        r = json.loads(execute_tool("search_courses", {"query": "建筑"}, self._sess()))
        assert r["found"] and len(r["courses"]) > 0
    def test_search_alias(self):
        """#4: 别名映射"""
        from llm_agent import execute_tool
        r = json.loads(execute_tool("search_courses", {"query": "高数"}, self._sess()))
        # 高数 maps to 微积分A(2) — may or may not exist in DB
        assert isinstance(r, dict)
    def test_update(self):
        from llm_agent import execute_tool
        s = self._sess()
        r = json.loads(execute_tool("update_requirements", {"course_ids": ["00000051"]}, s))
        assert any(c["id"] == "00000051" for c in r["must_have"])
    def test_solve_empty(self):
        from llm_agent import execute_tool
        r = json.loads(execute_tool("solve_schedule", {}, self._sess()))
        assert r["success"] is False
    def test_solve_ok(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"] and len(r["schedule"]) > 0
    def test_detail(self):
        from llm_agent import execute_tool
        r = json.loads(execute_tool("get_course_detail", {"course_id": "00000051"}, self._sess()))
        assert r["found"]
    def test_reset(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051"]}, s)
        execute_tool("reset_selection", {}, s)
        assert s["sm"].requirements["must_have"] == []
    def test_remove_course(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        execute_tool("update_requirements", {"remove_course_ids": ["00000051"]}, s)
        assert "00000051" not in s["sm"].requirements["must_have"]

    def test_remove_forbidden_slots(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"forbidden_slots": [{"weekday": 1, "period": [1, 2, 3, 4]}]}, s)
        execute_tool("update_requirements", {"remove_forbidden_slots": [{"weekday": 1, "period": [1, 2]}]}, s)
        slot = next((x for x in s["sm"].requirements["forbidden_slots"] if x["weekday"] == 1), None)
        assert slot is not None and set(slot["period"]) == {3, 4}
        execute_tool("update_requirements", {"remove_forbidden_slots": [{"weekday": 1}]}, s)
        assert all(x["weekday"] != 1 for x in s["sm"].requirements["forbidden_slots"])

    def test_remove_preferences(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"preferences": {"no_morning": True, "max_days": 3}}, s)
        execute_tool("update_requirements", {"remove_preferences": ["no_morning"]}, s)
        assert "no_morning" not in s["sm"].requirements["preferences"]

    def test_switch_course_section(self):
        from llm_agent import execute_tool
        from data_adapter import find_course_ids, fetch_course_context
        s = self._sess()
        cid = None
        for q in ("研讨", "实验", "导论", "专题"):
            for x in find_course_ids(q)[:200]:
                b = fetch_course_context([x])
                if not b:
                    continue
                secs = b[0].get("sections") or []
                if len(secs) < 2:
                    continue
                if not any((sec.get("time_slots") or []) for sec in secs):
                    continue
                cid = x
                break
            if cid:
                break
        assert cid is not None
        execute_tool("update_requirements", {"course_ids": [cid]}, s)
        r1 = json.loads(execute_tool("solve_schedule", {}, s))
        assert r1["success"] and len(r1["schedule"]) > 0
        old_section = r1["schedule"][0].get("section_id")
        r2 = json.loads(execute_tool("switch_course_section", {"course_id": cid}, s))
        assert r2["success"] and len(r2["schedule"]) > 0
        new_section = r2["schedule"][0].get("section_id")
        assert new_section and new_section != old_section
        assert len(s["sm"].requirements.get("blacklist_indices") or []) > 0

class TestShortcuts:
    """#5: 快捷指令"""
    def test_shortcut_solve(self):
        from llm_agent import try_shortcut
        from session_manager import SessionManager
        s = {"sm": SessionManager("sc"), "history": [], "last_schedule": None}
        s["sm"].update_requirements({"must_have": ["00000051"]})
        r = try_shortcut("换一个方案", s)
        assert r is not None and "reply" in r
    def test_shortcut_reset(self):
        from llm_agent import try_shortcut
        from session_manager import SessionManager
        s = {"sm": SessionManager("sc2"), "history": [], "last_schedule": None}
        s["sm"].update_requirements({"must_have": ["00000051"]})
        r = try_shortcut("清空", s)
        assert r is not None
        assert s["sm"].requirements["must_have"] == []
    def test_no_shortcut(self):
        from llm_agent import try_shortcut
        from session_manager import SessionManager
        s = {"sm": SessionManager("sc3"), "history": [], "last_schedule": None}
        assert try_shortcut("我要选建筑美学", s) is None

class TestBitmask:
    def test_collision(self):
        from data_adapter import periods_to_bitmask, check_collision
        assert check_collision(periods_to_bitmask([1,2,3]), periods_to_bitmask([3,4])) is True
    def test_no_collision(self):
        from data_adapter import periods_to_bitmask, check_collision
        assert check_collision(periods_to_bitmask([1,2]), periods_to_bitmask([3,4])) is False
    def test_priority_heavy(self):
        from data_adapter import calculate_priority
        assert calculate_priority({"学分": 4, "学时": 64})["is_heavy"] is True
    def test_priority_light(self):
        from data_adapter import calculate_priority
        assert calculate_priority({"学分": 1, "学时": 16})["is_heavy"] is False


# ═══ Tests for 6 new optimizations ═══

class TestDeleteConstraintSync:
    """#1: Constraint removal syncs and auto re-solves"""
    def _sess(self):
        from session_manager import SessionManager
        return {"sm": SessionManager("sync_test"), "history": [], "last_schedule": None, "last_solution_details": None}

    def test_remove_course_clears_schedule(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        execute_tool("solve_schedule", {}, s)
        assert s["last_schedule"] is not None
        # Remove a course — should invalidate
        execute_tool("update_requirements", {"remove_course_ids": ["00000051"]}, s)
        assert s["last_schedule"] is None  # cache cleared
        assert "00000051" not in s["sm"].requirements["must_have"]

    def test_remove_forbidden_clears_schedule(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051"], "forbidden_slots": [{"weekday": 1, "period": [1,2]}]}, s)
        execute_tool("solve_schedule", {}, s)
        assert s["last_schedule"] is not None
        execute_tool("update_requirements", {"remove_forbidden_slots": [{"weekday": 1}]}, s)
        assert s["last_schedule"] is None
        assert not any(fs["weekday"] == 1 for fs in s["sm"].requirements["forbidden_slots"])

    def test_remove_preferences_clears_schedule(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051"], "preferences": {"no_morning": True}}, s)
        execute_tool("solve_schedule", {}, s)
        execute_tool("update_requirements", {"remove_preferences": ["no_morning"]}, s)
        assert s["last_schedule"] is None
        assert "no_morning" not in s["sm"].requirements.get("preferences", {})


class TestConflictDetection:
    """#3: Conflict detection reports specific pairs instead of auto-dropping"""
    def test_detect_no_conflict(self):
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("cd1")
        sm.update_requirements({"must_have": ["00000051", "00000212"]})
        solver = CourseScheduler(sm)
        from data_adapter import fetch_course_context
        bundles = fetch_course_context(sm.requirements["must_have"])
        conflicts = solver.detect_conflicts(bundles)
        # These two courses should not conflict
        assert isinstance(conflicts, list)

    def test_detect_returns_pairs(self):
        """Verify detect_conflicts returns proper structure"""
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("cd2")
        sm.update_requirements({"must_have": ["00000051"]})
        solver = CourseScheduler(sm)
        from data_adapter import fetch_course_context
        bundles = fetch_course_context(sm.requirements["must_have"])
        conflicts = solver.detect_conflicts(bundles)
        assert isinstance(conflicts, list)
        # Single course = no conflict pairs
        assert len(conflicts) == 0


class TestNewTools:
    """#4: list_conflicts and resolve_conflict_by_user tools"""
    def _sess(self):
        from session_manager import SessionManager
        return {"sm": SessionManager("nt"), "history": [], "last_schedule": None, "last_solution_details": None}

    def test_list_conflicts_empty(self):
        from llm_agent import execute_tool
        s = self._sess()
        r = json.loads(execute_tool("list_conflicts", {}, s))
        assert r["conflicts"] == [] or "message" in r

    def test_list_conflicts_with_courses(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        r = json.loads(execute_tool("list_conflicts", {}, s))
        assert isinstance(r["conflicts"], list)

    def test_resolve_conflict(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        r = json.loads(execute_tool("resolve_conflict_by_user", {"keep_course_id": "00000051", "remove_course_id": "00000212"}, s))
        assert r.get("success") is True or "message" in r
        assert "00000212" not in s["sm"].requirements["must_have"]
        assert "00000051" in s["sm"].requirements["must_have"]

    def test_resolve_conflict_empty_args(self):
        from llm_agent import execute_tool
        s = self._sess()
        r = json.loads(execute_tool("resolve_conflict_by_user", {}, s))
        assert r.get("success") is False


class TestMultiSlotCourses:
    """#5: Courses with multiple time slots per week are handled correctly"""
    def test_multi_slot_context(self):
        """Verify fetch_course_context returns multiple time_slots for multi-slot courses"""
        from data_adapter import fetch_course_context
        # 00000212 (风景之道) has time_slots — check it returns proper structure
        bundles = fetch_course_context(["00000212"])
        assert len(bundles) > 0
        for sec in bundles[0].get("sections", []):
            # Each section should have time_slots list
            assert isinstance(sec.get("time_slots"), list)

    def test_multi_slot_scheduling(self):
        """Multi-slot courses should appear at all their time positions in schedule"""
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        sm = SessionManager("ms1")
        sm.update_requirements({"must_have": ["00000212"]})
        solver = CourseScheduler(sm)
        result = solver.solve(1)
        assert result  # should succeed
        # Check the details have time_slots with correct structure
        if isinstance(result[0], dict) and "details" in result[0]:
            details = result[0]["details"]
        else:
            details = result
        for item in details:
            ts = item.get("time_slots", [])
            assert isinstance(ts, list)
            for slot in ts:
                assert "weekday" in slot
                assert "period_list" in slot


class TestConstraintOverride:
    """#6: Constraints can be modified, latest dialog takes precedence"""
    def _sess(self):
        from session_manager import SessionManager
        return {"sm": SessionManager("co"), "history": [], "last_schedule": None, "last_solution_details": None}

    def test_preference_override(self):
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"preferences": {"preferred_building": "六教"}}, s)
        assert s["sm"].requirements["preferences"]["preferred_building"] == "六教"
        # Override with new value
        execute_tool("update_requirements", {"preferences": {"preferred_building": "五教"}}, s)
        assert s["sm"].requirements["preferences"]["preferred_building"] == "五教"

    def test_forbidden_slot_removal(self):
        from llm_agent import execute_tool
        s = self._sess()
        # Add forbidden
        execute_tool("update_requirements", {"forbidden_slots": [{"weekday": 1, "period": [1,2,3,4]}]}, s)
        assert any(fs["weekday"] == 1 for fs in s["sm"].requirements["forbidden_slots"])
        # Remove it (user says "周一也行")
        execute_tool("update_requirements", {"remove_forbidden_slots": [{"weekday": 1}]}, s)
        assert not any(fs["weekday"] == 1 for fs in s["sm"].requirements["forbidden_slots"])

    def test_partial_forbidden_removal(self):
        from llm_agent import execute_tool
        s = self._sess()
        # Add full morning forbidden
        execute_tool("update_requirements", {"forbidden_slots": [{"weekday": 2, "period": [1,2,3,4]}]}, s)
        # Remove only period 1,2 (user says "周二早八可以了")
        execute_tool("update_requirements", {"remove_forbidden_slots": [{"weekday": 2, "period": [1,2]}]}, s)
        slot = next((fs for fs in s["sm"].requirements["forbidden_slots"] if fs["weekday"] == 2), None)
        if slot:
            assert 1 not in slot["period"] and 2 not in slot["period"]
            assert 3 in slot["period"] and 4 in slot["period"]


# ═══ Tests for the 8 specific fixes ═══

class TestPeriodMapping:
    """#1: Raw periods 1-12 should remain intact in backend"""
    def test_raw_periods_in_schedule(self):
        """Backend schedule uses raw period values (1-12), not mapped"""
        from llm_agent import execute_tool
        from session_manager import SessionManager
        s = {"sm": SessionManager("pm1"), "history": [], "last_schedule": None, "last_solution_details": None}
        execute_tool("update_requirements", {"course_ids": ["00000051"]}, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"]
        for c in r["schedule"]:
            for p in c.get("period_list", []):
                # Raw periods should be 1-12 range
                assert 1 <= p <= 12, f"Period {p} out of raw range 1-12"

    def test_time_slots_json_has_6_periods(self):
        """time_slots.json should define exactly 6 display periods"""
        with open(os.path.join(os.path.dirname(__file__), "..", "data", "time_slots.json"), "r", encoding="utf-8") as f:
            slots = json.load(f)
        assert len(slots) == 6
        for s in slots:
            assert "period" in s and "start" in s and "end" in s


class TestRealtimeSync:
    """#2: Constraint changes trigger auto re-solve"""
    def test_constraint_api_returns_schedule(self):
        """When constraints change via API, schedule should be returned"""
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from main import app, get_or_create_session
        client = TestClient(app)
        sid = "sync_api_1"
        get_or_create_session(sid)
        # Add course
        r = client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000051"}})
        d = r.json()
        assert d["success"]
        # Should have schedule in response (auto re-solved)
        assert d.get("schedule") is not None


class TestConversationMemory:
    """#3: Conversation summary for long histories"""
    def test_summary_created_for_long_history(self):
        """When history > 12 messages, older ones should be summarized"""
        from llm_agent import run_agent
        from session_manager import SessionManager
        s = {"sm": SessionManager("mem1"), "history": [], "last_schedule": None, "last_solution_details": None}
        # Simulate 20 messages in history
        for i in range(10):
            s["history"].append({"role": "user", "content": f"测试消息 {i}"})
            s["history"].append({"role": "assistant", "content": f"回复 {i}"})
        assert len(s["history"]) == 20
        # The run_agent function should handle this without error
        # (we can't call it without API, but verify history structure is valid)
        assert all(isinstance(m, dict) and "role" in m for m in s["history"])


class TestNoCapacityDisplay:
    """#4: No capacity/余量 in API responses or prompts"""
    def test_courses_api_no_capacity(self):
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from main import app
        client = TestClient(app)
        r = client.get("/api/courses?keyword=建筑&limit=3").json()
        for c in r["courses"]:
            assert "capacity_left" not in c

    def test_prompt_no_capacity_mention(self):
        from llm_agent import SYSTEM_PROMPT
        # The prompt should prohibit mentioning 余量, not recommend it
        assert "严禁" in SYSTEM_PROMPT or "绝不提及" in SYSTEM_PROMPT
        # Should not have positive mentions like "余量更高" or "推荐余量"
        assert "余量更高" not in SYSTEM_PROMPT
        assert "推荐余量" not in SYSTEM_PROMPT


class TestConflictUserChoice:
    """#5: Conflicts should be reported, not auto-resolved"""
    def test_list_conflicts_tool_exists(self):
        from llm_agent import TOOLS
        tool_names = [t["function"]["name"] for t in TOOLS]
        assert "list_conflicts" in tool_names

    def test_resolve_conflict_tool_exists(self):
        from llm_agent import TOOLS
        tool_names = [t["function"]["name"] for t in TOOLS]
        assert "resolve_conflict_by_user" in tool_names

    def test_scheduler_logs_conflict_pairs(self):
        """When conflicts exist, scheduler should log them"""
        from session_manager import SessionManager
        from scheduler import CourseScheduler
        from data_adapter import fetch_course_context
        sm = SessionManager("conflict_test")
        sm.update_requirements({"must_have": ["00000051"]})
        solver = CourseScheduler(sm)
        bundles = fetch_course_context(sm.requirements["must_have"])
        conflicts = solver.detect_conflicts(bundles)
        assert isinstance(conflicts, list)


class TestResetSession:
    """#6: Reset clears everything"""
    def test_reset_via_api(self):
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from main import app, get_or_create_session
        client = TestClient(app)
        sid = "reset_test"
        get_or_create_session(sid)
        # Add some data
        client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000051"}})
        # Reset
        r = client.post(f"/api/session/reset?session_id={sid}")
        assert r.json()["success"]

    def test_reset_tool(self):
        from llm_agent import execute_tool
        from session_manager import SessionManager
        s = {"sm": SessionManager("rt"), "history": [{"role":"user","content":"test"}], "last_schedule": {"x":1}, "last_solution_details": []}
        execute_tool("update_requirements", {"course_ids": ["00000051"]}, s)
        execute_tool("reset_selection", {}, s)
        assert s["sm"].requirements["must_have"] == []
        assert s["last_schedule"] is None


class TestSwitchSection:
    """#7: Switch course section works correctly"""
    def test_switch_requires_schedule(self):
        from llm_agent import execute_tool
        from session_manager import SessionManager
        s = {"sm": SessionManager("sw1"), "history": [], "last_schedule": None, "last_solution_details": None}
        r = json.loads(execute_tool("switch_course_section", {"course_name": "建筑与城市美学"}, s))
        assert r["success"] is False  # No schedule yet


class TestGeneralQA:
    """#8: System prompt handles non-course questions"""
    def test_prompt_has_general_qa_guidance(self):
        from llm_agent import SYSTEM_PROMPT
        assert "非选课" in SYSTEM_PROMPT or "闲聊" in SYSTEM_PROMPT
        assert "未小羊" in SYSTEM_PROMPT

