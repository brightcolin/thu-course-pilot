# 问道未央 🐏

### THU Course Pilot · 清荷 Studio

> 🏆 第三届"未央城"智能体大赛 · 创意赛道二等奖

清华大学智能选课助手。通过自然语言对话，AI Agent 自动完成课程搜索、时间约束提取、冲突检测与课表生成。

## 系统架构

```
FastAPI (main.py)
  └─ LLM Agent (src/llm_agent.py)      # Function Calling，8 个工具
       └─ Session Manager               # 会话状态、约束合并
            └─ Scheduler (3-phase)      # 完美匹配 → 约束松弛 → 贪心舍弃
                 └─ Data Adapter        # 课程搜索、位图冲突检测
                      └─ SQLite DB      # 18,000+ 门课程
```

前端为单页应用（`static/index.html`），含对话区、周课表视图和约束面板。

## 环境要求

- Python 3.8+
- [DeepSeek](https://platform.deepseek.com/) 或 [Moonshot](https://platform.moonshot.cn/) API Key

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 3. 准备数据库（二选一，见下方说明）
python init_db.py   # 方式 A：从原始数据生成
# 或直接将 scheduler.db 放到项目根目录  # 方式 B：使用预构建数据库

# 4. 启动
python main.py
# 访问 http://localhost:8000
```

### 关于数据库

本项目使用 SQLite 数据库（`scheduler.db`），包含清华大学 18,000+ 门课程的时间、教师、地点等信息。

**为什么没有上传到仓库：** 原始数据文件 `data/raw_courses.jsonl`（约 60 MB）和生成的数据库文件均因体积过大而加入了 `.gitignore`。

**如何获取：**

- **方式 A（推荐）：** 联系项目团队获取预构建的 `scheduler.db`，直接放到项目根目录即可运行，无需其他步骤。
- **方式 B：** 获取原始数据文件 `data/raw_courses.jsonl`，放到 `data/` 目录后运行 `python init_db.py` 生成数据库（同时需要 `output/position_slot_map.json`，也可向团队索取）。

## 运行测试

```bash
python -m pytest tests/ -v
# 或单独运行某类测试：
python -m pytest tests/test_unit.py -v -k "scheduler"
```

## 对话示例

```
你：我要选面向对象程序设计基础，不想上早八
你：帮我换一个面向对象基础的班级
你：换一个方案
你：清空，重新来
```

Agent 会自动识别课程名称（支持别名，如"高数"→"微积分A"）、提取时间禁止条件（"早八"= 第 1-2 节），调用排课引擎生成无冲突课表。

## 配置说明

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | API 密钥（必填） |
| `LLM_BASE_URL` | 接口地址，默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名，默认 `deepseek-chat` |

详见 `.env.example`。

## 关于本项目

本项目由 **清荷 Studio** 团队共同开发完成。

🏆 第三届"未央城"智能体大赛 · 创意赛道二等奖

**团队成员：**  
[@brightcolin](https://github.com/brightcolin) · [@YangAn8800](https://github.com/YangAn8800) · [@sayankk](https://github.com/sayankk) · [@rainlanelongings-cmd](https://github.com/rainlanelongings-cmd)
