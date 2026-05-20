import sys
import os
import json

# 确保可以导入 L0 层 database.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import get_slots_by_course, get_full_details_by_indices, find_course_ids_by_query, get_curriculum_by_major_semester

class CourseContextBundle:
    """
    打平后的结构化课程素材包，供 Agent 直接使用。
    """
    def __init__(self, course_id, course_name, slots_data):
        self.course_id = course_id
        self.course_name = course_name
        self.slots = slots_data # List of flattened slot objects

    def to_dict(self):
        return {
            "course_id": self.course_id,
            "course_name": self.course_name,
            "slots": self.slots
        }

def find_course_ids(query):
    """
    L1 封装: 按课名或老师名模糊搜索。
    """
    return find_course_ids_by_query(query)

def fetch_course_context(course_name_list):
    """
    核心功能: 将课程名称列表转化为聚合了“时空位置”与“业务详情”的 CourseContextBundle 列表。
    链路: 搜索 ID -> 获取位点 -> 获取全量详情 -> 按课序号(section_id)聚合。
    """
    bundles = []
    
    for query in course_name_list:
        course_ids = find_course_ids(query)
        if not course_ids:
            continue
            
        for cid in course_ids:
            # 1. 获取该课程的所有宏观位点
            raw_slots = get_slots_by_course(cid)
            if not raw_slots:
                continue
            
            course_name = raw_slots[0]['course_name']
            
            # 2. 按 section_id 聚合位点
            # sections_map = { section_id: [slot1, slot2, ...] }
            sections_map = {}
            for s in raw_slots:
                sid = s['section_id']
                if sid not in sections_map:
                    sections_map[sid] = []
                sections_map[sid].append(s)
            
            aggregated_sections = []
            
            # 3. 为每个课序号(老师/班级)构建完整的上下文
            for sid, slots in sections_map.items():
                # 我们假设同一课序号的老师、学分等基本信息一致，取第一个位点关联的详情
                # 获取该 section 的所有原始行索引，用于更精确的名额校验
                all_indices = []
                for s in slots:
                    all_indices.extend(s['origin_indices'])
                
                # 去重
                all_indices = sorted(list(set(all_indices)))
                details = get_full_details_by_indices(all_indices)
                
                if not details:
                    continue
                
                # 取首条详情作为该班级的基本信息
                d = details[0]
                remains = d.get("本科生课余量", 0)
                status = "AVAILABLE" if remains > 0 else "FULL"
                priority = calculate_priority(d)
                
                # 构造班级包 (Section Bundle)
                section_data = {
                    "section_id": sid,
                    "teacher": d.get("教师名1") or d.get("教师名2") or "未知",
                    "location": d.get("上课地点") or "未知",
                    "remains": remains,
                    "status": status,
                    "credits": d.get("学分"),
                    "hours": d.get("学时"),
                    "score": priority.get("score", 0),
                    "is_heavy": bool(priority.get("is_heavy", False)),
                    "priority_tag": priority.get("tag", "NORMAL"),
                    "row_indices": all_indices, # 记录该班级关联的所有原始行
                    "time_slots": [] # 包含多个上课位点
                }
                
                for s in slots:
                    try:
                        w = int(s["weekday"])
                    except (TypeError, ValueError):
                        continue
                    if not (1 <= w <= 7):
                        continue
                    section_data["time_slots"].append({
                        "weekday": w,
                        "period_list": s['period_list'],
                        "weeks": s['weeks']
                    })
                
                aggregated_sections.append(section_data)
            
            if aggregated_sections:
                bundles.append({
                    "course_id": cid,
                    "course_name": course_name,
                    "sections": aggregated_sections
                })
            
    return bundles

def get_curriculum_suggestions(major, semester):
    """
    L1 封装: 获取该专业、学期的课程计划建议列表。
    Agent 在对话初期调用，用于向用户推荐并获取确认。
    """
    raw_suggestions = get_curriculum_by_major_semester(major, semester)
    
    # 将其格式化为对 Agent 友好的推荐列表
    suggestions = []
    for s in raw_suggestions:
        suggestions.append({
            "course_id": s["course_id"],
            "course_name": s["course_name"],
            "dept": s["dept"],
            "type": s["course_type"] # 标注必修或选修
        })
    return suggestions

def verify_realtime_availability(row_index_list):
    """
    动态名额敏感检索 ($z_1$ 感知):
    最后一次确认这些特定行记录的 remains 是否依然可用。
    """
    if not row_index_list:
        return []
        
    details = get_full_details_by_indices(row_index_list)
    results = []
    for d in details:
        remains = d.get("本科生课余量", 0)
        results.append({
            "row_index": d.get("row_index"),
            "course_id": d.get("课程号"),
            "section_id": d.get("课序号"),
            "remains": remains,
            "is_available": remains > 0
        })
    return results

def load_time_slots(file_path=None):
    if file_path is None:
        file_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "time_slots.json"))
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    res = {}
    for item in data:
        try:
            p = int(item.get("period"))
        except (TypeError, ValueError):
            continue
        res[p] = item
    return res

def period_list_to_clock_range(period_list, time_slots_map=None):
    if not period_list:
        return None
    try:
        min_p = min(int(p) for p in period_list)
    except (TypeError, ValueError):
        return None
    block = (min_p + 1) // 2
    if time_slots_map is None:
        time_slots_map = load_time_slots()
    slot = time_slots_map.get(block)
    if not slot:
        return None
    start = slot.get("start")
    end = slot.get("end")
    label = slot.get("label")
    if start and end and label:
        return f"{start}-{end}({label})"
    if start and end:
        return f"{start}-{end}"
    return label

# --- L3 辅助: 位图转换工具 ---

def periods_to_bitmask(period_list):
    """
    将节次列表 [1, 2, 3] 转换为 16 位整数位图。
    """
    mask = 0
    for p in period_list:
        if 1 <= p <= 14:
            mask |= (1 << (p - 1))
    return mask

def check_collision(mask_a, mask_b):
    """
    使用位运算进行 O(1) 的冲突检测。
    """
    return (mask_a & mask_b) != 0

# --- L1 优先级计算与权重系统 ---

def calculate_priority(course_details):
    """
    智能权重系统逻辑:
    Score = (学分 * 10) + (学时 / 2)
    标记 HEAVY_WEIGHT: 学分 >= 3 或 学时 >= 48
    """
    try:
        credits = float(course_details.get("学分", 0) or 0)
        hours = float(course_details.get("学时", 0) or 0)
    except (ValueError, TypeError):
        credits = 0
        hours = 0
        
    score = (credits * 10) + (hours / 2)
    is_heavy = credits >= 3 or hours >= 48
    
    return {
        "score": score,
        "is_heavy": is_heavy,
        "tag": "HEAVY_WEIGHT" if is_heavy else "NORMAL"
    }

def get_courses_priority_info(course_id_list):
    """
    从 L0 批量获取课程的优先级信息。
    """
    if not course_id_list:
        return {}
        
    # 由于 course_id 对应的 row_indices 可能有多个（不同老师），
    # 我们取该课程号下学分学时最大的记录作为权重参考（通常该课号下学分学时是统一的）
    import sqlite3
    from database import DB_NAME
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    placeholders = ', '.join(['?'] * len(course_id_list))
    # 字段名包含中文，需加引号
    sql = f'''
        SELECT "课程号", "学分", "学时" 
        FROM course_details 
        WHERE "课程号" IN ({placeholders})
        GROUP BY "课程号"
    '''
    cursor.execute(sql, course_id_list)
    rows = cursor.fetchall()
    conn.close()
    
    priority_map = {}
    for row in rows:
        cid = row["课程号"]
        p_info = calculate_priority(dict(row))
        priority_map[cid] = p_info
        
    return priority_map

if __name__ == "__main__":
    # 测试代码
    print("--- L1 Data Adapter 测试 ---")
    
    # 1. 测试搜索
    queries = ["建筑与城市美学", "王辉"]
    print(f"\n1. 模糊搜索 {queries}:")
    for q in queries:
        ids = find_course_ids(q)
        print(f"  查询 '{q}' -> 结果: {ids}")

    # 2. 测试上下文抓取 (聚合位点与详情)
    print("\n2. 获取课程上下文 (CourseContextBundle):")
    context = fetch_course_context(["建筑与城市美学"])
    for bundle in context:
        print(f"课程: {bundle['course_name']} ({bundle['course_id']})")
        for section in bundle['sections'][:2]: # 只显示前两个班级
            print(f"  -> 班级: {section['section_id']} | 老师: {section['teacher']} | 状态: {section['status']} (余量: {section['remains']})")
            for ts in section['time_slots']:
                print(f"     时间: 周{ts['weekday']} {ts['period_list']}")

    # 3. 测试实时名额校验
    if context and context[0]['sections']:
        target_index = context[0]['sections'][0]['row_indices'][0]
        print(f"\n3. 校验 row_index={target_index} 的实时名额:")
        v_res = verify_realtime_availability([target_index])
        print(f"  结果: {v_res}")
