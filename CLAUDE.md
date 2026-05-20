# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

"未小羊" is a Chinese university AI-powered course selection and scheduling system. Students converse in natural language with an LLM agent to select courses and generate conflict-free weekly schedules. Built with FastAPI + SQLite backend, vanilla JS frontend, and DeepSeek/Moonshot LLM APIs.

## Commands

```bash
# Setup
pip install -r requirements.txt
cp .env.example .env  # Fill in LLM API credentials
python init_db.py     # Initialize scheduler.db from raw_courses.jsonl

# Run
python main.py        # Starts on http://localhost:8000

# Test
python -m pytest tests/ -v
python -m pytest tests/test_unit.py -v -k "test_search"  # Single test by keyword
bash run_tests.sh
```

## Architecture

4-layer architecture with clear separation of concerns:

```
API Layer (main.py)
  └─ Session management (TTL=30min, max 500 sessions), endpoints for /api/chat, /api/courses, /api/constraints, /api/schedule/solve

LLM Agent (src/llm_agent.py)
  └─ 8 function-calling tools; shortcut detection ("换一个方案"/"清空" bypass LLM); conflict prompting

Session Manager (src/session_manager.py)
  └─ Per-session state: course requirements, forbidden slots, soft preferences, conflict logs, solutions

Scheduler (src/scheduler.py)
  └─ 3-phase solver: ① perfect match → ② relax forbidden slots → ③ greedy drop by priority
  └─ Backtracking + bitmask collision detection

Data Adapter (src/data_adapter.py)
  └─ Course search, priority ranking, time utilities, bitmask ops over SQLite (18K+ courses)

Database (src/database.py)
  └─ SQLite tables: course_details, course_slots, curriculum
```

## Agent Tools (src/llm_agent.py)

The agent has 8 tools dispatched by `execute_tool()`:

| Tool | Purpose |
|---|---|
| `search_courses` | Full-text search; resolves aliases (e.g. "高数" → "微积分A(2)") |
| `update_requirements` | Merges course list + forbidden slots + soft preferences into session state |
| `solve_schedule` | Triggers 3-phase scheduler; returns formatted solution |
| `get_course_detail` | Returns all sections with teacher, time, location |
| `reset_selection` | Clears all courses and constraints |
| `switch_course_section` | Pins a course to a specific section |
| `list_conflicts` | Returns current time conflicts |
| `resolve_conflict_by_user` | Keeps one course when user resolves a conflict |

## Key Data Flows

**Chat request flow:**
`POST /api/chat` → `LLMAgent.run_agent()` → shortcut check → LLM function calling → `execute_tool()` loop → `SessionManager.update_requirements()` → `CourseScheduler.solve()` → formatted reply

**Time constraint example:** "不想上早八" → LLM produces `forbidden_slots: [{weekday: 1-5, period: [1,2]}]` → merged into session state via bitmask

## Environment Variables

See `.env.example`. Key vars:
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` — LLM provider (DeepSeek or Moonshot/Kimi)
- `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` — generation params

## Testing

47 tests covering: course search + alias resolution, session state merging, all 3 scheduler phases, all 8 tools, shortcut commands, bitmask collision, REST endpoints, and 8 end-to-end dialogue scenarios.

Integration tests in `tests/integration/test_api.py` run multi-turn conversations against the live API — they require the server to be running or use `TestClient`.
