import sqlite3
import json
import os

DB_NAME = "scheduler.db"

def init_db_full(slot_json_path, raw_jsonl_path, curriculum_json_path=None):
    """
    L0 - 全量数据持久化层：
    1. 逐行读取 raw_courses.jsonl -> course_details (全量微观表，含 row_index)
    2. 读取 position_slot_map.json -> course_slots (宏观位点表)
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. 重置数据库结构
    cursor.execute('DROP TABLE IF EXISTS course_slots')
    cursor.execute('DROP TABLE IF EXISTS course_details')
    cursor.execute('DROP TABLE IF EXISTS curriculum')

    # ---------------------------------------------------------
    # 2. 构建 course_details (物理底座 - 全量表)
    # ---------------------------------------------------------
    # 扫描全文件获取所有字段名，动态建表 (因为不同行字段可能不一致)
    all_fields = set()
    with open(raw_jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            all_fields.update(data.keys())
    
    fields = sorted(list(all_fields))
    
    # 强制将“本科生课余量”等关键数值字段设为 INTEGER 或 REAL 以支持高效查询
    # 其他默认 TEXT
    field_definitions = ["row_index INTEGER PRIMARY KEY"]
    for f in fields:
        # 避免重复定义 row_index
        if f == "row_index": continue
        if f in ["本科生课余量", "本科生课容量", "选课学生人数"]:
            field_definitions.append(f'"{f}" INTEGER')
        elif f in ["学分"]:
            field_definitions.append(f'"{f}" REAL')
        else:
            field_definitions.append(f'"{f}" TEXT')
    
    cursor.execute(f'CREATE TABLE course_details ({", ".join(field_definitions)})')

    # ---------------------------------------------------------
    # 3. 构建 course_slots (宏观位点表)
    # ---------------------------------------------------------
    cursor.execute('''
        CREATE TABLE course_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT,
            course_name TEXT,
            dept TEXT,
            section_id TEXT,
            weekday INTEGER,
            period_list TEXT, -- JSON 字符串
            weeks TEXT,
            overlap_count INTEGER,
            origin_indices TEXT -- JSON 字符串，关联 course_details.row_index
        )
    ''')
    cursor.execute('CREATE INDEX idx_slots_course_weekday ON course_slots (course_id, weekday)')

    # ---------------------------------------------------------
    # 4. 构建 curriculum (培养方案辅助表)
    # ---------------------------------------------------------
    cursor.execute('''
        CREATE TABLE curriculum (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            major TEXT,
            grade TEXT,
            semester TEXT,
            course_id TEXT,
            course_type TEXT
        )
    ''')

    # ---------------------------------------------------------
    # 5. 执行全量导入 (BEGIN TRANSACTION 事务保证)
    # ---------------------------------------------------------
    try:
        conn.execute("BEGIN TRANSACTION")
        
        # A. 导入 course_details
        with open(raw_jsonl_path, 'r', encoding='utf-8') as f:
            idx = 0
            for line in f:
                data = json.loads(line)
                data['row_index'] = idx
                # 构造插入语句
                columns = list(data.keys())
                placeholders = ', '.join(['?'] * len(columns))
                sql = f'INSERT INTO course_details ({", ".join([f"\"{c}\"" for c in columns])}) VALUES ({placeholders})'
                cursor.execute(sql, list(data.values()))
                idx += 1
        
        # B. 导入 course_slots (展开 position_slot_map.json)
        with open(slot_json_path, 'r', encoding='utf-8') as f:
            slot_data = json.load(f)
            # slot_data 结构: [["course_id", "course_name", "dept", [["section_id", [[slots]], "notes"], ...]]]
            for course_info in slot_data:
                # 检查是否包含说明元素 (第一个元素是 Schema 说明)
                if isinstance(course_info, dict) and "___SCHEMA_DESCRIPTION___" in course_info:
                    continue
                
                course_id, course_name, dept, sections = course_info
                for section in sections:
                    section_id, slots, notes = section
                    for slot in slots:
                        weekday, period_list, weeks, overlap_count, origin_indices = slot
                        cursor.execute('''
                            INSERT INTO course_slots (course_id, course_name, dept, section_id, weekday, period_list, weeks, overlap_count, origin_indices)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            course_id, course_name, dept, section_id,
                            int(weekday), 
                            json.dumps(period_list), 
                            weeks, 
                            overlap_count, 
                            json.dumps(origin_indices)
                        ))
        
        # C. 导入 curriculum (如果有)
        if curriculum_json_path and os.path.exists(curriculum_json_path):
            with open(curriculum_json_path, 'r', encoding='utf-8') as f:
                curr_data = json.load(f)
                for d in curr_data:
                    cursor.execute('''
                        INSERT INTO curriculum (major, grade, semester, course_id, course_type)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (d['major'], d['grade'], d.get('semester', ''), d['course_id'], d['course_type']))

        conn.commit()
        print(f"L0层持久化完成: course_details ({idx}条), course_slots 导入成功。")
    except Exception as e:
        conn.rollback()
        print(f"L0层导入失败: {e}")
        raise e
    finally:
        conn.close()

# ---------------------------------------------------------
# L0 原子查询 API (API 规范)
# ---------------------------------------------------------

def get_slots_by_course(course_id):
    """
    功能: 给排课引擎提供该课程的所有时空位点。
    """
    def _expand_period_blocks(period_list):
        if not isinstance(period_list, list) or not period_list:
            return period_list
        try:
            nums = [int(x) for x in period_list]
        except (TypeError, ValueError):
            return period_list
        block_map = {1: (1, 2), 2: (3, 4), 3: (5, 6), 4: (7, 8), 5: (9, 10), 6: (11, 12)}
        if all((0 <= n <= 6) for n in nums):
            out = []
            for n in nums:
                if n in block_map:
                    a, b = block_map[n]
                    out.extend([a, b])
                else:
                    out.append(n)
            out = sorted(set(out))
            return out
        return nums

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM course_slots WHERE course_id = ?', (course_id,))
    res = [dict(row) for row in cursor.fetchall()]
    # 反序列化 JSON 字段
    for r in res:
        r['period_list'] = _expand_period_blocks(json.loads(r['period_list']))
        r['origin_indices'] = json.loads(r['origin_indices'])
    conn.close()
    return res

def find_course_ids_by_query(query):
    """
    L0 原子查询: 支持按课名或老师名模糊搜索，返回匹配的 course_id 列表。
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 在 course_details 中搜索课程号、课程名、教师名1、教师名2
    # 注意: 原始字段名包含中文
    sql = '''
        SELECT DISTINCT "课程号" FROM course_details 
        WHERE "课程号" = ?
        OR "课程名" LIKE ? 
        OR "教师名1" LIKE ? 
        OR "教师名2" LIKE ?
    '''
    like_query = f'%{query}%'
    cursor.execute(sql, (query, like_query, like_query, like_query))
    
    res = [row[0] for row in cursor.fetchall()]
    conn.close()
    return res

def get_full_details_by_indices(row_indices):
    """
    核心功能: 根据 course_slots 提供的 origin_indices（行号列表），执行一次全量查询。
    输出: 返回这些课程的全量原始属性，供 RAG 层进行深度分析和名额校验。
    """
    if not row_indices:
        return []
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    placeholders = ', '.join(['?'] * len(row_indices))
    sql = f'SELECT * FROM course_details WHERE row_index IN ({placeholders})'
    cursor.execute(sql, row_indices)
    
    res = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return res

def get_required_courses_full_info(major, grade):
    """
    跨表联动查询：返回该生必修课的所有宏观占位。
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 联动查询：curriculum -> course_slots
    query = '''
        SELECT s.*, c.course_type
        FROM curriculum c
        JOIN course_slots s ON c.course_id = s.course_id
        WHERE c.major = ? AND c.grade = ? AND c.course_type = '必修'
    '''
    cursor.execute(query, (major, grade))
    res = [dict(row) for row in cursor.fetchall()]
    
    # 填充微观详情
    for r in res:
        r['period_list'] = json.loads(r['period_list'])
        indices = json.loads(r['origin_indices'])
        r['origin_indices'] = indices
        r['detailed_options'] = get_full_details_by_indices(indices)
        
    conn.close()
    return res

def get_curriculum_by_major_semester(major, semester):
    """
    L0 原子查询: 根据专业和学期获取课程计划。
    用于 Agent 在对话开始时向用户展示推荐列表。
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 查找该专业、该学期的所有课程 (必修 + 选修)
    query = '''
        SELECT c.course_id, c.course_type, s.course_name, s.dept
        FROM curriculum c
        JOIN (SELECT DISTINCT course_id, course_name, dept FROM course_slots) s 
        ON c.course_id = s.course_id
        WHERE c.major = ? AND c.semester = ?
    '''
    cursor.execute(query, (major, semester))
    res = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return res

if __name__ == "__main__":
    # 配置路径
    RAW_JSONL = os.path.join("data", "raw_courses.jsonl")
    SLOT_JSON = os.path.join("output", "position_slot_map.json")
    CURR_JSON = os.path.join("data", "curriculum.json")

    # 1. 初始化 L0
    init_db_full(SLOT_JSON, RAW_JSONL, CURR_JSON)

    # 2. 测试：根据课号取位点
    test_cid = "00000051"
    print(f"\n--- 测试: 获取课程 {test_cid} 的宏观位点 ---")
    slots = get_slots_by_course(test_cid)
    for s in slots:
        print(f"星期{s['weekday']} | 节次{s['period_list']} | 索引列表: {s['origin_indices']}")

        # 3. 测试：根据行号取全量详情
        print(f"--- 击中微观详情 (row_index={s['origin_indices'][0]}) ---")
        details = get_full_details_by_indices(s['origin_indices'])
        for d in details:
            print(f"教师: {d.get('教师名1') or '未知'} | 余量: {d.get('本科生课余量')} | 地点: {d.get('上课地点')}")

    # 4. 测试：必修课联动
    print(f"\n--- 测试: 建筑学 大一 必修课全联动 ---")
    full_info = get_required_courses_full_info("建筑学", "大一")
    for item in full_info:
        print(f"必修: {item['course_name']} ({item['course_id']}) | 坑位: 周{item['weekday']} {item['period_list']}")
        for opt in item['detailed_options']:
            print(f"  -> 选项: {opt.get('教师名1')} (余量:{opt.get('本科生课余量')})")
