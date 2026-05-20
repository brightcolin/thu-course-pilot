"""集成测试：API 端点 + 对话场景测试 (#1)"""
import sys, os, json, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from fastapi.testclient import TestClient
from main import app, get_or_create_session
client = TestClient(app)

class TestEndpoints:
    def test_health(self):
        assert client.get("/health").json()["status"] == "ok"
    def test_courses_list(self):
        d = client.get("/api/courses?limit=5").json()
        assert len(d["courses"]) <= 5
    def test_courses_search(self):
        d = client.get("/api/courses?keyword=建筑").json()
        assert any("建筑" in c["course_name"] for c in d["courses"])
    def test_courses_no_capacity_field(self):
        """#10: 课程列表不含余量"""
        d = client.get("/api/courses?keyword=建筑").json()
        for c in d["courses"]:
            assert "capacity_left" not in c
    def test_course_detail(self):
        d = client.get("/api/course_detail/00000051").json()
        assert d["found"] and d["course_name"] == "建筑与城市美学"
    def test_frontend_serves(self):
        assert client.get("/").status_code == 200

class TestConstraintsAPI:
    def test_add_remove(self):
        sid = "api_c1"; get_or_create_session(sid)
        r = client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000051"}}).json()
        assert any(c["course_id"] == "00000051" for c in r["constraints"]["must_have"])
        r = client.post("/api/constraints/update", json={"session_id": sid, "action": "remove_course", "data": {"course_id": "00000051"}}).json()
        assert not any(c["course_id"] == "00000051" for c in r["constraints"]["must_have"])
    def test_clear(self):
        sid = "api_c2"; get_or_create_session(sid)
        client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000051"}})
        r = client.post("/api/constraints/update", json={"session_id": sid, "action": "clear_all", "data": {}}).json()
        assert len(r["constraints"]["must_have"]) == 0

class TestScheduleSolve:
    def test_with_courses(self):
        sid = "api_s1"; get_or_create_session(sid)
        client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000051"}})
        client.post("/api/constraints/update", json={"session_id": sid, "action": "add_course", "data": {"course_id": "00000212"}})
        d = client.post(f"/api/schedule/solve?session_id={sid}").json()
        assert d["success"] and len(d["schedule"]) > 0
    def test_empty(self):
        sid = "api_s2"; get_or_create_session(sid)
        assert client.post(f"/api/schedule/solve?session_id={sid}").json()["success"] is False


# ═══ 对话场景测试 (#1) ═══
# 这些测试不调用真实 LLM，而是测试工具执行的端到端流程

class TestDialogScenarios:
    """模拟多组典型对话场景的工具调用流程"""

    def _sess(self):
        from session_manager import SessionManager
        return {"sm": SessionManager("dialog_test"), "history": [], "last_schedule": None}

    def test_scenario_basic_single_course(self):
        """场景1: 基本选课 - 选一门课并排课"""
        from llm_agent import execute_tool
        s = self._sess()
        # 搜索
        r = json.loads(execute_tool("search_courses", {"query": "建筑与城市美学"}, s))
        assert r["found"]
        cid = r["courses"][0]["course_id"]
        # 添加
        execute_tool("update_requirements", {"course_ids": [cid]}, s)
        # 排课
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"]

    def test_scenario_multi_course(self):
        """场景2: 多课程选课 - 同时选两门课"""
        from llm_agent import execute_tool
        s = self._sess()
        r1 = json.loads(execute_tool("search_courses", {"query": "建筑与城市美学"}, s))
        r2 = json.loads(execute_tool("search_courses", {"query": "风景之道"}, s))
        cid1, cid2 = r1["courses"][0]["course_id"], r2["courses"][0]["course_id"]
        execute_tool("update_requirements", {"course_ids": [cid1, cid2]}, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"]
        assert len(set(c["course_id"] for c in r["schedule"])) == 2

    def test_scenario_with_forbidden(self):
        """场景3: 带禁用时间 - 选课+不要早八"""
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {
            "course_ids": ["00000051"],
            "forbidden_slots": [{"weekday": w, "period": [1, 2]} for w in range(1, 6)]
        }, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        # Should handle via relaxation if needed
        assert isinstance(r, dict)

    def test_scenario_remove_and_readd(self):
        """场景4: 移除课程再添加新课"""
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051", "00000212"]}, s)
        execute_tool("update_requirements", {"remove_course_ids": ["00000051"]}, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"]
        assert all(c["course_id"] != "00000051" for c in r["schedule"])

    def test_scenario_soft_constraints(self):
        """场景5: 软约束 - 偏好设置"""
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {
            "course_ids": ["00000051"],
            "preferences": {"no_morning": True, "preferred_building": "六教"}
        }, s)
        assert s["sm"].requirements["preferences"]["no_morning"] is True
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert isinstance(r, dict)

    def test_scenario_full_reset(self):
        """场景6: 完全重置后重新选课"""
        from llm_agent import execute_tool
        s = self._sess()
        execute_tool("update_requirements", {"course_ids": ["00000051"]}, s)
        execute_tool("reset_selection", {}, s)
        assert s["sm"].requirements["must_have"] == []
        execute_tool("update_requirements", {"course_ids": ["00000212"]}, s)
        r = json.loads(execute_tool("solve_schedule", {}, s))
        assert r["success"]

    def test_scenario_course_detail(self):
        """场景7: 查看课程详情"""
        from llm_agent import execute_tool
        s = self._sess()
        r = json.loads(execute_tool("get_course_detail", {"course_id": "00000051"}, s))
        assert r["found"]
        assert "sections" in r and len(r["sections"]) > 0

    def test_scenario_nonexistent_course(self):
        """场景8: 搜索不存在的课程"""
        from llm_agent import execute_tool
        s = self._sess()
        r = json.loads(execute_tool("search_courses", {"query": "量子纠缠烹饪学"}, s))
        assert r["found"] is False
