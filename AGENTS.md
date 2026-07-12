# AGENTS.md

## 项目概述

“问道未央”是一个面向高校选课场景的 AI 排课助手。用户通过自然语言提出课程需求和时间约束，系统调用兼容 OpenAI SDK 的大模型接口提取意图，并生成尽量无冲突的课表。

技术栈：

- 后端：Python、FastAPI、SQLite
- Agent：OpenAI Python SDK，支持 DeepSeek / Moonshot 等兼容接口
- 前端：原生 HTML、CSS、JavaScript，集中在 `static/index.html`
- 测试：pytest、FastAPI TestClient

## 代码导航

- `main.py`：FastAPI 应用入口、API 路由、会话生命周期和响应格式化。
- `src/llm_agent.py`：LLM 提示词、工具定义、工具调度和快捷指令。
- `src/session_manager.py`：课程需求、禁止时段、偏好、冲突和方案等会话状态。
- `src/scheduler.py`：三阶段排课求解器；依次尝试严格匹配、放宽禁止时段、按优先级舍弃课程。
- `src/data_adapter.py`：课程检索、课程上下文、时间转换、位图冲突检测和优先级计算。
- `src/database.py`：SQLite 初始化与查询。
- `static/index.html`：单页前端。
- `tests/test_unit.py`：核心逻辑与工具单元测试。
- `tests/integration/test_api.py`：基于 TestClient 的 API 和多轮场景测试，不要求预先启动服务器。
- `data/`：课程体系与时间段等静态数据。

主要请求链路：

`POST /api/chat` → `run_agent()` → 快捷指令或 LLM Function Calling → `execute_tool()` → 会话状态更新 → `CourseScheduler.solve()` → 格式化响应。

## 常用命令

当前主要开发环境为 Windows PowerShell。优先使用跨平台的 Python 命令，不依赖 Bash。

```powershell
# 安装依赖
python -m pip install -r requirements.txt

# 首次配置（随后手动填写密钥）
Copy-Item .env.example .env

# 在具备原始数据文件时初始化数据库
python init_db.py

# 启动服务：http://localhost:8000
python main.py

# 运行全部测试
python -m pytest tests/ -q

# 运行指定测试
python -m pytest tests/test_unit.py -q -k "scheduler"
```

截至 2026-07-12，测试套件包含 97 个测试。数字仅用于说明当前基线；以后以 pytest 实际收集结果为准。

## 修改与验证约定

- 修改行为前先定位现有测试和调用链，尽量保持 API 响应结构与会话状态字段兼容。
- 修改 `src/scheduler.py`、`src/session_manager.py`、`src/data_adapter.py` 或 `src/llm_agent.py` 后，至少运行相关单元测试。
- 修改 `main.py` 的路由、请求模型或响应结构后，运行 `tests/integration/test_api.py`。
- 完成跨模块修改后运行 `python -m pytest tests/ -q`。
- 修复缺陷时优先补充能复现缺陷的回归测试。
- 不为无关代码做大范围格式化；尤其谨慎编辑体量较大的 `static/index.html`。
- 代码和项目文档使用 UTF-8。PowerShell 读取中文文件时显式指定 `-Encoding UTF8`，避免把正常文本误判为乱码。

## 数据与安全边界

- `.env` 包含本地凭据：不要读取、打印、覆盖或提交它。配置说明以 `.env.example` 为准。
- 代码当前只读取 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 三个 LLM 环境变量。
- `scheduler.db`、`data/raw_courses.jsonl` 和 `output/` 是本地或生成数据；不要擅自删除、重建或加入版本控制。
- 不修改或删除与任务无关的未跟踪文件，包括项目报告等文档。
- 工作区可能已有用户修改；编辑前检查 `git status`，只处理当前任务涉及的文件。
- 未经明确要求，不调用真实 LLM 接口，不产生外部 API 费用。

## 文档维护

- 本文件是 Codex 和团队自动化协作的项目级权威说明。
- 架构、命令或约定变化时同步更新本文件，但不要记录容易快速失效的实现细节。
- `README.md` 面向项目使用者；`AGENTS.md` 面向代码代理和维护者。避免把内部协作规则堆入 README。
- `CLAUDE.md` 仅作为 Claude Code 的兼容入口，通用内容不要在两处重复维护。
