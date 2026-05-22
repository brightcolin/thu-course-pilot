import json
import os
import hashlib

class SessionManager:
    """
    L2 - 会话状态层：管理用户需求演进与方案快照。
    """
    def __init__(self, session_id="default_user"):
        self.session_id = session_id
        # 初始需求包
        self.requirements = {
            "must_have": [],        # 必须选中的 course_id 列表 (已排序)
            "forbidden_slots": [],  # 绝对禁止的时间点: [{"weekday": 5, "period": [6, 7, 8]}]
            "preferences": {},      # 软约束: {"morning_only": True, "no_late_night": True}
            "blacklist_indices": [], # 用户明确否决过的 row_index
            "section_constraints": {} # course_id -> {"teacher_contains": "...", "strict": True}
        }
        # 记录导致排课失败的“冲突路径”
        self.conflict_log = [] 
        # 方案快照: { "Solution_1": [row_index1, row_index2, ...] }
        self.solutions = {}
        # 记录上一次的方案指纹，用于跳过重复计算
        self.last_requirement_fingerprint = None

    def invalidate_cache(self):
        self.solutions = {}
        self.last_requirement_fingerprint = None

    def reset_all(self):
        self.requirements = {"must_have": [], "forbidden_slots": [], "preferences": {}, "blacklist_indices": [], "section_constraints": {}}
        self.conflict_log = []
        self.invalidate_cache()

    def update_requirements(self, parsed_json):
        """
        核心功能：深度合并 LLM 提取的最新约束片段。
        """
        # 清空之前的冲突记录，准备新一轮排课
        self.conflict_log = []
        
        # 1. 合并必须选中的 ID
        if "must_have" in parsed_json:
            new_ids = parsed_json["must_have"]
            # 去重合并
            for cid in new_ids:
                if cid not in self.requirements["must_have"]:
                    self.requirements["must_have"].append(cid)
            
            # --- 建立“学分/学时驱动”的智能权重系统 ---
            self._sort_must_have_by_priority()

        # 2. 合并禁止时间 (处理冲突：以最新需求为准)
        if "forbidden_slots" in parsed_json:
            # 简单策略：如果是全量覆盖，则替换；如果是增量，则合并。
            # 这里采用合并策略，并对重复的 weekday 进行去重。
            new_slots = parsed_json["forbidden_slots"]
            for ns in new_slots:
                # 查找是否已有该星期的禁用
                found = False
                for i, existing in enumerate(self.requirements["forbidden_slots"]):
                    if existing["weekday"] == ns["weekday"]:
                        # 合并 period 列表并去重
                        combined = list(set(existing.get("period", []) + ns.get("period", [])))
                        self.requirements["forbidden_slots"][i]["period"] = sorted(combined)
                        found = True
                        break
                if not found:
                    self.requirements["forbidden_slots"].append(
                        {"weekday": ns["weekday"], "period": ns.get("period", [])}
                    )

        # 3. 合并软约束
        if "preferences" in parsed_json:
            self.requirements["preferences"].update(parsed_json["preferences"])

        # 4. 更新黑名单
        if "blacklist_indices" in parsed_json:
            new_black = parsed_json["blacklist_indices"]
            self.requirements["blacklist_indices"] = list(set(self.requirements["blacklist_indices"] + new_black))

        if "section_constraints" in parsed_json and isinstance(parsed_json["section_constraints"], dict):
            for cid, rule in parsed_json["section_constraints"].items():
                if not isinstance(cid, str) or not cid:
                    continue
                if not isinstance(rule, dict):
                    continue
                self.requirements["section_constraints"][cid] = {
                    "teacher_contains": (rule.get("teacher_contains") or "").strip(),
                    "strict": bool(rule.get("strict", True))
                }

        if "remove_section_constraints" in parsed_json:
            for cid in parsed_json.get("remove_section_constraints") or []:
                if isinstance(cid, str):
                    self.requirements["section_constraints"].pop(cid, None)

        print(f"Session [{self.session_id}] 需求已更新。")

    def _sort_must_have_by_priority(self):
        """
        自动排序：调用 L1 获取学分学时权重，并重新排列 must_have 列表。
        """
        if not self.requirements["must_have"]:
            return
            
        from data_adapter import get_courses_priority_info
        
        priority_map = get_courses_priority_info(self.requirements["must_have"])
        
        # 根据 score 降序排列
        self.requirements["must_have"].sort(
            key=lambda cid: priority_map.get(cid, {}).get("score", 0), 
            reverse=True
        )
        
        # 打印排序结果供调试
        print(f"[Priority] 已按权重重新排列课程: {self.requirements['must_have']}")
        for cid in self.requirements["must_have"]:
            p = priority_map.get(cid, {})
            print(f"  - {cid}: Score={p.get('score')}, Tag={p.get('tag')}")

    def record_conflict(self, conflict_type, description, items=None):
        """
        记录排课失败的具体路径（如：A课与B课冲突，或名额已满）。
        """
        self.conflict_log.append({
            "type": conflict_type, # 'TIME_CONFLICT', 'NO_CAPACITY', 'USER_FORBIDDEN'
            "description": description,
            "items": items or [] # 涉及的课程号或行索引
        })

    def store_top_solutions(self, solutions_list):
        """
        功能：为每个方案分配 ID 并记录其包含的 row_index。
        solutions_list: [ [idx1, idx2, ...], [idx3, idx4, ...], ... ]
        """
        self.solutions = {}
        for i, indices in enumerate(solutions_list):
            sol_id = f"Solution_{i+1}"
            self.solutions[sol_id] = indices
        
        # 更新指纹（使用 SHA256 确保稳定性）
        req_str = json.dumps(self.requirements, sort_keys=True)
        self.last_requirement_fingerprint = hashlib.sha256(req_str.encode()).hexdigest()
        return list(self.solutions.keys())

    def get_backtrack_params(self):
        """
        输出：为 L3 排课层准备最终的参数包。
        """
        return {
            "must_have": self.requirements["must_have"],
            "forbidden_slots": self.requirements["forbidden_slots"],
            "blacklist_indices": self.requirements["blacklist_indices"],
            "preferences": self.requirements["preferences"],
            "section_constraints": self.requirements.get("section_constraints", {})
        }

    def export_state(self, file_path=None):
        """
        状态原子化：导出为 JSON。
        """
        state = {
            "session_id": self.session_id,
            "requirements": self.requirements,
            "solutions": self.solutions,
            "conflict_log": self.conflict_log
        }
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        return state

    def load_state(self, state_json):
        """
        恢复状态。
        """
        self.session_id = state_json.get("session_id", self.session_id)
        self.requirements = state_json.get("requirements", self.requirements)
        self.solutions = state_json.get("solutions", self.solutions)

if __name__ == "__main__":
    # 测试 L2 会话状态演进
    sm = SessionManager("user_123")

    print("--- 第一轮：添加必选课 ---")
    sm.update_requirements({"must_have": ["00000051"]})
    print(sm.get_backtrack_params())

    print("\n--- 第二轮：追加禁用时间 ---")
    sm.update_requirements({
        "forbidden_slots": [{"weekday": 5, "period": [1, 2, 3]}]
    })
    print(sm.get_backtrack_params())

    print("\n--- 第三轮：软约束与指纹存储 ---")
    sm.update_requirements({"preferences": {"no_morning_classes": True}})
    
    # 模拟算法生成的方案
    mock_solutions = [
        [101, 202, 303], # 方案 1 的 row_index 组合
        [101, 205, 408]  # 方案 2 的 row_index 组合
    ]
    sol_ids = sm.store_top_solutions(mock_solutions)
    print(f"生成的方案 ID: {sol_ids}")
    print(f"方案指纹 (Hash): {sm.last_requirement_fingerprint}")

    print("\n--- 状态导出测试 ---")
    state = sm.export_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))
