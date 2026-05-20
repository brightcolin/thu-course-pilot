import json
import os
from collections import defaultdict

class CourseDistiller:
    def __init__(self, input_path, output_path):
        self.input_path = input_path
        self.output_path = output_path

    def load_data(self):
        """Loads raw JSON or JSONL data from the input path."""
        if not os.path.exists(self.input_path):
            print(f"Warning: Input file {self.input_path} not found.")
            return []
        
        with open(self.input_path, 'r', encoding='utf-8') as f:
            # Check if the file is JSONL (line-by-line) or standard JSON
            if self.input_path.endswith('.jsonl'):
                data = []
                for line in f:
                    if line.strip():
                        data.append(json.loads(line))
                return data
            else:
                # Default to standard JSON
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    # Fallback: if JSON fails, try reading as JSONL
                    f.seek(0)
                    data = []
                    for line in f:
                        if line.strip():
                            data.append(json.loads(line))
                    return data

    def distill(self):
        """Processes the raw course data into the required format."""
        raw_data = self.load_data()
        if not raw_data:
            return []

        # Pre-filter: Remove courses with "网上不选课" in "选课说明"
        filtered_data = []
        filtered_count = 0
        for idx, record in enumerate(raw_data):
            notes = record.get("选课说明", "")
            if notes and "网上不选课" in notes:
                filtered_count += 1
                continue
            filtered_data.append((idx, record))
        
        if filtered_count > 0:
            print(f"Filtered out {filtered_count} records containing '网上不选课' in '选课说明'.")

        # Step 1: Group by (course_id, course_name, department)
        # course_groups = { (id, name, dept): [records] }
        course_groups = defaultdict(list)
        for idx, record in filtered_data:
            # Using the keys specified by the user
            cid = record.get("课程号", "")
            cname = record.get("课程名", "")
            dept = record.get("开课单位", "")
            course_key = (cid, cname, dept)
            course_groups[course_key].append((idx, record))

        final_output = []

        for course_key, records in course_groups.items():
            cid, cname, dept = course_key
            
            # Step 2: For each course, group by section_id (课序号)
            # section_records = { section_id: [records] }
            section_records = defaultdict(list)
            for idx, record in records:
                sid = record.get("课序号", "default_section")
                section_records[sid].append((idx, record))
            
            compact_sections = []
            for sid, s_records in section_records.items():
                # Step 3: For each section, group by (weekday, weeks)
                # This helps identify continuous periods for a single class session
                # slot_groups = { (weekday, weeks): [ (period, idx) ] }
                slot_groups = defaultdict(list)
                notes = ""
                for idx, record in s_records:
                    weekday = record.get("上课星期", 0)
                    weeks = record.get("上课周次", "")
                    period = record.get("上课节次", 0)
                    # Capture notes if available
                    if not notes:
                        notes = record.get("选课说明", "")
                    
                    # Ensure period is treated as integers for sorting and continuity
                    try:
                        if isinstance(period, str) and "-" in period:
                            start, end = map(int, period.split("-"))
                            for p in range(start, end + 1):
                                if p >= 1:
                                    slot_groups[(weekday, weeks)].append((p, idx))
                        else:
                            p_val = int(period)
                            if p_val >= 1:
                                slot_groups[(weekday, weeks)].append((p_val, idx))
                    except (ValueError, TypeError):
                        continue

                # Step 4: Find continuous periods for each slot session
                # sessions = [ {weekday, periods: [3,4], weeks, origin_indices: [idx1, idx2]} ]
                all_sessions = []
                for (weekday, weeks), period_info in slot_groups.items():
                    try:
                        weekday_int = int(weekday)
                    except (TypeError, ValueError):
                        continue
                    if not (1 <= weekday_int <= 7):
                        continue

                    # Sort periods to find continuity
                    period_info.sort()
                    
                    current_periods = []
                    current_indices = []
                    
                    for i in range(len(period_info)):
                        p, idx = period_info[i]
                        if not current_periods or p == current_periods[-1] + 1:
                            current_periods.append(p)
                            current_indices.append(idx)
                        else:
                            # Break in continuity, save previous and start new
                            all_sessions.append({
                                "weekday": weekday_int,
                                "periods": current_periods,
                                "weeks": weeks,
                                "origin_indices": current_indices
                            })
                            current_periods = [p]
                            current_indices = [idx]
                    
                    if current_periods:
                        all_sessions.append({
                            "weekday": weekday_int,
                            "periods": current_periods,
                            "weeks": weeks,
                            "origin_indices": current_indices
                        })

                # Step 5: Cluster sessions by (weekday, periods, weeks) to find overlaps
                # occupancy_map = { (weekday, tuple(periods), weeks): {overlap_count, origin_indices} }
                occupancy_map = {}
                for session in all_sessions:
                    occ_key = (session["weekday"], tuple(session["periods"]), session["weeks"])
                    if occ_key not in occupancy_map:
                        occupancy_map[occ_key] = {
                            "weekday": session["weekday"],
                            "periods": session["periods"],
                            "weeks": session["weeks"],
                            "overlap_count": 1,
                            "origin_indices": session["origin_indices"]
                        }
                    else:
                        occupancy_map[occ_key]["overlap_count"] += 1
                        occupancy_map[occ_key]["origin_indices"].extend(session["origin_indices"])

                # Step 6: Format into compact slot structure
                # Slot format: [weekday, periods, weeks, overlap_count, origin_indices]
                compact_slots = []
                for occ in occupancy_map.values():
                    occ["origin_indices"] = sorted(list(set(occ["origin_indices"])))
                    compact_slots.append([
                        occ["weekday"],
                        occ["periods"],
                        occ["weeks"],
                        occ["overlap_count"],
                        occ["origin_indices"]
                    ])
                
                # Add this section to the course entry
                # Section format: [section_id, slots, notes]
                compact_sections.append([
                    sid,
                    compact_slots,
                    notes
                ])

            # Course format: [course_id, course_name, department, sections]
            final_output.append([
                cid,
                cname,
                dept,
                compact_sections
            ])

        return final_output

    def save_output(self, data):
        """Saves processed data with one course per line for readability while staying compact."""
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write("[\n")
            for i, course in enumerate(data):
                # Compact JSON for each line
                line = json.dumps(course, ensure_ascii=False, separators=(',', ':'))
                f.write(f"  {line}")
                if i < len(data) - 1:
                    f.write(",\n")
                else:
                    f.write("\n")
            f.write("]\n")
        print(f"Distillation complete. Line-delimited compact output saved to {self.output_path}")

if __name__ == "__main__":
    # Check for .jsonl first, then fallback to .json
    input_file = "data/raw_courses.jsonl"
    if not os.path.exists(input_file):
        input_file = "data/raw_courses.json"
        
    distiller = CourseDistiller(input_file, "output/position_slot_map.json")
    distilled_data = distiller.distill()
    distiller.save_output(distilled_data)
