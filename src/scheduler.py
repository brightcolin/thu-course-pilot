import sys
import os

# 确保导入层级正确
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_adapter import fetch_course_context, periods_to_bitmask, check_collision
from session_manager import SessionManager

# CourseScheduler handles course scheduling logic
class CourseScheduler:
    """
    L3 - 排课引擎：执行递归回溯搜索。
    """
    def __init__(self, session_manager: SessionManager):
        self.sm = session_manager
        # 预加载全局参数
        params = self.sm.get_backtrack_params()
        self.must_have = params["must_have"]
        self.forbidden_masks = self._init_forbidden_masks(params["forbidden_slots"])
        self.blacklist = set(params["blacklist_indices"])
        self.section_constraints = params.get("section_constraints", {}) or {}

    def _apply_section_constraints(self, context_bundles):
        if not self.section_constraints:
            return context_bundles
        out = []
        for b in context_bundles:
            cid = b.get("course_id")
            rule = self.section_constraints.get(cid)
            if not rule:
                out.append(b)
                continue
            teacher_contains = (rule.get("teacher_contains") or "").strip()
            strict = bool(rule.get("strict", True))
            if not teacher_contains:
                out.append(b)
                continue
            secs = b.get("sections") or []
            matched = [s for s in secs if teacher_contains in (s.get("teacher") or "")]
            if matched:
                out.append({**b, "sections": matched})
                continue
            msg = f"课程[{b.get('course_name', cid)}]未找到老师包含“{teacher_contains}”的班级"
            if strict:
                self.sm.record_conflict("SECTION_FILTER_NO_MATCH", msg, [cid])
                return None
            self.sm.record_conflict("SECTION_FILTER_RELAXED", msg + "，已自动改用同课程号的其他老师班级。", [cid])
            out.append(b)
        return out

    def _init_forbidden_masks(self, forbidden_slots):
        """
        初始化禁用时间位图：{weekday: mask}
        """
        masks = {i: 0 for i in range(1, 8)}
        for fs in forbidden_slots:
            w = self._normalize_weekday(fs["weekday"])
            if w is None:
                continue
            mask = periods_to_bitmask(fs.get("period", []))
            masks[w] |= mask
        return masks

    def _normalize_weekday(self, weekday):
        try:
            w = int(weekday)
        except (TypeError, ValueError):
            return None
        if 1 <= w <= 7:
            return w
        return None

    def solve(self, max_solutions=3):
        """
        入口：自愈式近似求解与约束松弛 (Auto-Relaxation)
        阶段一：完美匹配 (带 forbidden_slots)
        阶段二：约束松弛 (忽略 forbidden_slots)
        阶段三：贪心舍弃 (保住大课，舍弃权重低的课)
        """
        # 1. 抓取素材包
        context_bundles = fetch_course_context(self.must_have)
        if not context_bundles:
            if self.must_have:
                msg = f"未找到课程 {self.must_have} 的任何有效位点，请检查课程号是否正确。"
                self.sm.record_conflict("DATA_MISSING", msg)
                print(f"\n[Scheduler] {msg}")
            return []
        context_bundles = self._apply_section_constraints(context_bundles)
        if context_bundles is None:
            return []

        # --- 阶段一：完美匹配 ---
        print("\n[Scheduler] 阶段一：尝试完美匹配 (应用所有约束)...")
        all_solutions = []
        current_masks_v1 = {i: 0 for i in range(1, 8)}
        for w, mask in self.forbidden_masks.items():
            current_masks_v1[w] = mask
            
        self._backtrack(context_bundles, 0, current_masks_v1, [], all_solutions, max_solutions)
        
        if all_solutions:
            # 特征分析与标注
            analyzed_solutions = self.analyze_solutions(all_solutions)
            analyzed_solutions = self._sort_solutions(analyzed_solutions)
            self._finalize_solutions(analyzed_solutions)
            print(f"[OK] 阶段一成功：找到 {len(all_solutions)} 个完美方案并完成特征标注。")
            return analyzed_solutions

        # --- 阶段二：约束松弛 ---
        if self.forbidden_masks and any(m > 0 for m in self.forbidden_masks.values()):
            print("\n[Scheduler] [WARN] 阶段一失败，进入阶段二：约束松弛 (忽略‘禁止时间’)...")
            all_solutions = []
            empty_masks = {i: 0 for i in range(1, 8)}
            self._backtrack(context_bundles, 0, empty_masks, [], all_solutions, max_solutions)
            
            if all_solutions:
                # 标记约束已松弛并分析特征
                for sol in all_solutions:
                    for item in sol:
                        item["constraint_relaxed"] = True
                
                analyzed_solutions = self.analyze_solutions(all_solutions)
                analyzed_solutions = self._sort_solutions(analyzed_solutions)
                self._finalize_solutions(analyzed_solutions)
                msg = "由于您的‘禁止时间’与课程冲突，系统已自动忽略部分时间限制为您排出全量课表。"
                self.sm.record_conflict("CONSTRAINT_RELAXED", msg)
                print(f"[OK] 阶段二成功：找到 {len(all_solutions)} 个松弛方案。")
                return analyzed_solutions

        # --- 阶段三：冲突检测 + 贪心舍弃 ---
        print("\n[Scheduler] [WARN] 阶段二失败，进入阶段三：检测冲突并贪心舍弃...")
        # 先检测具体冲突对，报告给用户
        conflicts = self.detect_conflicts(context_bundles)
        if conflicts:
            for pair in conflicts:
                self.sm.record_conflict("TIME_CONFLICT", pair["description"], pair["courses"])

        partial_solution = self._solve_partial_with_priority(context_bundles)
        
        if partial_solution:
            analyzed_solutions = self.analyze_solutions([partial_solution])
            analyzed_solutions = self._sort_solutions(analyzed_solutions)
            self._finalize_solutions(analyzed_solutions)
            return analyzed_solutions
            
        return []

    def detect_conflicts(self, context_bundles=None, selected_section_ids=None):
        """
        检测所有已选课程之间的时间冲突对。
        selected_section_ids: {course_id: section_id}，优先使用当前课表中已选的班级；
                              未提供时回退到第一个非黑名单班级。
        返回 [{"courses": [cid_a, cid_b], "names": [name_a, name_b],
                "weekday": w, "periods": [...], "description": "..."}]
        """
        if context_bundles is None:
            context_bundles = fetch_course_context(self.must_have)
            if not context_bundles:
                return []

        selected = selected_section_ids or {}
        course_slots = []  # [(cid, cname, [(weekday, period_set)])]
        for b in context_bundles:
            cid = b["course_id"]
            cname = b["course_name"]
            sections = b.get("sections", [])

            # 优先使用当前课表中已选的班级
            picked = None
            target_sid = selected.get(cid)
            if target_sid is not None:
                picked = next(
                    (s for s in sections
                     if str(s.get("section_id")) == str(target_sid)
                     and not any(idx in self.blacklist for idx in s.get("row_indices", []))),
                    None,
                )

            # 回退：取第一个非黑名单班级
            if picked is None:
                for sec in sections:
                    if any(idx in self.blacklist for idx in sec.get("row_indices", [])):
                        continue
                    picked = sec
                    break

            if picked is None:
                continue

            slots = []
            for ts in picked.get("time_slots", []):
                w = self._normalize_weekday(ts.get("weekday"))
                if w:
                    slots.append((w, set(ts.get("period_list", []))))
            if slots:
                course_slots.append((cid, cname, slots))

        day_names = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        conflicts = []
        for i in range(len(course_slots)):
            for j in range(i + 1, len(course_slots)):
                cid_a, name_a, slots_a = course_slots[i]
                cid_b, name_b, slots_b = course_slots[j]
                for wa, pa in slots_a:
                    for wb, pb in slots_b:
                        if wa == wb:
                            overlap = pa & pb
                            if overlap:
                                conflicts.append({
                                    "courses": [cid_a, cid_b],
                                    "names": [name_a, name_b],
                                    "weekday": wa,
                                    "periods": sorted(overlap),
                                    "description": f'\u201c{name_a}\u201d和\u201c{name_b}\u201d在{day_names[wa]}第{sorted(overlap)}节时间冲突，请选择保留其中一门。'
                                })
        return conflicts

    def _backtrack(self, bundles, bundle_idx, current_masks, current_solution, all_solutions, max_solutions):
        """
        核心递归逻辑：寻找最多 max_solutions 个全量解。
        """
        if len(all_solutions) >= max_solutions:
            return

        if bundle_idx == len(bundles):
            # 找到一个全量解，深拷贝存入结果集
            all_solutions.append(list(current_solution))
            return

        bundle = bundles[bundle_idx]
        cname = bundle["course_name"]

        # 尝试该课程的所有可行班级 (Sections)
        for section in bundle["sections"]:
            # A. 过滤黑名单
            if any(idx in self.blacklist for idx in section["row_indices"]):
                continue

            # B. 余量仅作为排序参考，不作为过滤条件（数据为历史余量）

            # C. 冲突检测
            collision_detected = False
            section_masks = []
            for ts in section["time_slots"]:
                w = self._normalize_weekday(ts["weekday"])
                if w is None:
                    collision_detected = True
                    break
                slot_mask = periods_to_bitmask(ts["period_list"])
                if slot_mask == 0:
                    collision_detected = True
                    break
                if check_collision(current_masks[w], slot_mask):
                    collision_detected = True
                    break
                section_masks.append((w, slot_mask))

            if collision_detected:
                continue

            # D. 试探
            for w, m in section_masks:
                current_masks[w] |= m
            
            current_solution.append({
                "course_id": bundle["course_id"],
                "course_name": cname,
                "section_id": section["section_id"],
                "teacher": section["teacher"],
                "location": section["location"],
                "remains": section.get("remains", 0),
                "credits": section.get("credits", 0),
                "hours": section.get("hours", 0),
                "score": section.get("score", 0),
                "is_heavy": section.get("is_heavy", False),
                "priority_tag": section.get("priority_tag", "NORMAL"),
                "time_slots": section["time_slots"],
                "row_indices": section["row_indices"]
            })

            self._backtrack(bundles, bundle_idx + 1, current_masks, current_solution, all_solutions, max_solutions)

            # E. 回溯
            current_solution.pop()
            for w, m in section_masks:
                current_masks[w] &= ~m

    def analyze_solutions(self, solutions):
        """
        特征分析与差异化标注 (Top-3 Analysis)
        """
        solution_stats = []
        for sol in solutions:
            total_gap = 0
            slots_by_day = {d: [] for d in range(1, 8)}
            for item in sol:
                for ts in item["time_slots"]:
                    slots_by_day[ts["weekday"]].append(min(ts["period_list"]))

            for periods in slots_by_day.values():
                if len(periods) > 1:
                    periods.sort()
                    for j in range(len(periods) - 1):
                        total_gap += (periods[j + 1] - periods[j])

            avg_remains = sum(item.get("remains", 0) for item in sol) / len(sol) if sol else 0
            friday_classes = sum(1 for item in sol if any(ts["weekday"] == 5 for ts in item["time_slots"]))

            solution_stats.append({
                "total_gap": total_gap,
                "avg_remains": avg_remains,
                "friday_classes": friday_classes,
                "has_heavy": any(item.get("is_heavy") for item in sol)
            })

        min_gap = min((s["total_gap"] for s in solution_stats), default=0)
        max_avg_remains = max((s["avg_remains"] for s in solution_stats), default=0)
        min_friday = min((s["friday_classes"] for s in solution_stats), default=0)

        analyzed = []
        for i, sol in enumerate(solutions):
            stats = solution_stats[i]
            tags = []

            if stats["total_gap"] == min_gap:
                tags.append("COMPACT")
            if stats["avg_remains"] == max_avg_remains and stats["avg_remains"] > 0:
                tags.append("HIGH_SCORE")
            if stats["friday_classes"] == min_friday:
                tags.append("WEEKEND_FRIENDLY")
            if stats["has_heavy"]:
                tags.append("高学分保障")

            for item in sol:
                item["solution_rank"] = i + 1
                item["solution_tags"] = tags

            analyzed.append({
                "rank": i + 1,
                "tags": tags,
                "details": sol,
                "row_indices": [idx for item in sol for idx in item["row_indices"]],
                "stats": stats
            })

        return analyzed

    def _sort_solutions(self, analyzed_solutions):
        prefs = self.sm.get_backtrack_params().get("preferences", {}) or {}

        def _score(sol_obj):
            details = sol_obj.get("details") or []
            days = set()
            morning = 0
            prefer_building_hits = 0
            prefer_building = prefs.get("preferred_building")
            for item in details:
                loc = item.get("location") or ""
                if prefer_building and prefer_building in loc:
                    prefer_building_hits += 1
                for ts in item.get("time_slots", []) or []:
                    w = ts.get("weekday")
                    if isinstance(w, int):
                        days.add(w)
                    for p in ts.get("period_list", []) or []:
                        try:
                            ip = int(p)
                        except (TypeError, ValueError):
                            continue
                        if ip <= 2:
                            morning += 1

            target_days = prefs.get("max_days")
            try:
                target_days = int(target_days) if target_days is not None else None
            except (TypeError, ValueError):
                target_days = None

            no_morning = bool(prefs.get("no_morning", False))

            days_count = len(days) if days else 0
            days_over = max(0, days_count - target_days) if target_days else 0
            morning_penalty = morning if no_morning else 0
            return (
                -days_over,
                -prefer_building_hits,
                -morning_penalty,
                -(sol_obj.get("stats", {}) or {}).get("avg_remains", 0),
                (sol_obj.get("stats", {}) or {}).get("total_gap", 0),
                (sol_obj.get("stats", {}) or {}).get("friday_classes", 0),
                sol_obj.get("rank", 0),
            )

        sorted_list = sorted(analyzed_solutions, key=_score)
        for i, sol_obj in enumerate(sorted_list):
            sol_obj["rank"] = i + 1
            for item in sol_obj.get("details", []) or []:
                item["solution_rank"] = i + 1
                item["solution_tags"] = sol_obj.get("tags", [])
        return sorted_list

    def _finalize_solutions(self, analyzed_solutions):
        """
        将分析后的方案存入 SessionManager
        """
        solution_indices = []
        for sol_obj in analyzed_solutions:
            solution_indices.append(sol_obj["row_indices"])
        self.sm.store_top_solutions(solution_indices)

    def _solve_partial_with_priority(self, bundles):
        """
        阶段三逻辑：
        1. 按照权重降序尝试。
        2. 如果是大课 (学分 >= 3 或 学时 >= 48，即 Score >= 30 且 is_heavy=True)，尽力保住。
        3. 记录 dropped_courses 到冲突日志。
        """
        current_masks = {i: 0 for i in range(1, 8)}
        # 阶段三也忽略禁止时间，以最大化排课为准
        
        partial_solution = []
        dropped_courses = []
        dropped_names = []
        
        # bundles 已经在 SessionManager 中按权重排好序了
        for bundle in bundles:
            cname = bundle["course_name"]
            cid = bundle["course_id"]
            
            found_section = False
            for section in bundle["sections"]:
                # 基本过滤
                if any(idx in self.blacklist for idx in section["row_indices"]):
                    continue
                    
                # 冲突检测
                collision_detected = False
                section_masks = []
                for ts in section["time_slots"]:
                    w = self._normalize_weekday(ts["weekday"])
                    if w is None:
                        collision_detected = True
                        break
                    slot_mask = periods_to_bitmask(ts["period_list"])
                    if slot_mask == 0:
                        collision_detected = True
                        break
                    if check_collision(current_masks[w], slot_mask):
                        collision_detected = True
                        break
                    section_masks.append((w, slot_mask))
                
                if not collision_detected:
                    for w, m in section_masks:
                        current_masks[w] |= m
                    partial_solution.append({
                        "course_id": cid,
                        "course_name": cname,
                        "section_id": section["section_id"],
                        "teacher": section["teacher"],
                        "location": section["location"],
                        "remains": section.get("remains", 0),
                        "credits": section.get("credits", 0),
                        "hours": section.get("hours", 0),
                        "score": section.get("score", 0),
                        "is_heavy": section.get("is_heavy", False),
                        "priority_tag": section.get("priority_tag", "NORMAL"),
                        "time_slots": section["time_slots"],
                        "row_indices": section["row_indices"]
                    })
                    found_section = True
                    break
            
            if not found_section:
                dropped_courses.append(cid)
                dropped_names.append(cname)
                self.sm.record_conflict("PARTIAL_SOLVE_SKIP", f"由于时间冲突，自动为您剔除了课程 [{cname}]，其余课程已排好。", [cid])
        
        # 特殊标注被剔除的课程列表
        if dropped_courses:
            for log in self.sm.conflict_log:
                if log["type"] == "PARTIAL_SOLVE_SKIP":
                    log["dropped_courses"] = dropped_courses
            names_str = "、".join(dropped_names) if dropped_names else "部分课程"
            self.sm.record_conflict(
                "PARTIAL_SOLVE_SUMMARY",
                f"由于课程时间冲突且需要优先保留高学分/大课，系统已自动剔除：{names_str}。",
                dropped_courses,
            )
                    
        return partial_solution

if __name__ == "__main__":
    # 测试排课引擎
    sm = SessionManager("test_user")
    
    # 模拟需求：必选“建筑与城市美学”和“风景之道”
    sm.update_requirements({
        "must_have": ["00000051", "00000212"],
        "forbidden_slots": [{"weekday": 1, "period": [1, 2]}] # 模拟禁用周一上午 1-2 节，不与风景之道(3-4节)冲突
    })

    scheduler = CourseScheduler(sm)
    result = scheduler.solve()

    if result:
        print("\n[OK] 排课成功！")
        for res in result:
            for ts in res["time_slots"]:
                print(f"- [{res['course_name']}] {res['teacher']} | 周{ts['weekday']} 第{ts['period_list']}节 | 地点: {res['location']}")
    else:
        print("\n[ERR] 排课失败，冲突日志：")
        for log in sm.conflict_log:
            print(f"  [{log['type']}] {log['description']}")
