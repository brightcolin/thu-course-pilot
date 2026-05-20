"""数据库初始化脚本"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from database import init_db_full

if __name__ == "__main__":
    RAW_JSONL = os.path.join("data", "raw_courses.jsonl")
    SLOT_JSON = os.path.join("output", "position_slot_map.json")
    CURR_JSON = os.path.join("data", "curriculum.json")
    init_db_full(SLOT_JSON, RAW_JSONL, CURR_JSON)
    print("数据库初始化完成！")
