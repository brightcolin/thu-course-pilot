# 问道未央 🐏

[简体中文](README.md) | [English](README_EN.md)

![获奖](https://img.shields.io/badge/Award-Second%20Prize-gold)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![测试](https://img.shields.io/badge/Tests-97%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/Framework-FastAPI-009688?logo=fastapi&logoColor=white)
![数据](https://img.shields.io/badge/Data-Virtual%20Demo%20Only-blue)
![许可证](https://img.shields.io/badge/License-No%20Open--Source%20License-lightgrey)

### THU Course Pilot · 清荷 Studio

> 🏆 第三届“未央城”智能体大赛 · 创意赛道二等奖

“问道未央”是一个面向高校选课场景的 AI 排课助手。用户可以通过自然语言提出课程需求、禁用时间和偏好条件，系统自动完成课程搜索、约束提取、冲突检测与课表生成。

> **项目状态：** 本仓库是比赛作品归档，当前不提供线上服务，也没有继续产品化或公开部署的计划。代码保留用于成果展示与技术交流。

本项目为学生团队参加第三届“未央城”智能体大赛创作的比赛作品，并非清华大学或未央书院官方选课系统。

## 作品展示

![问道未央智能选课与排课界面](docs/images/schedule-demo.jpg)

截图使用虚拟演示数据，教师和地点等信息已做模糊处理。

## 核心能力

- 使用自然语言添加、删除和查询课程。
- 识别“不要早八”“周三下午有空”等时间约束。
- 支持教师、地点和课程时段等班级偏好。
- 检测课程冲突，并由用户决定保留或移除的课程。
- 生成多套候选课表，并展示学分、上课天数和早课数量。
- 通过快捷指令重新排课或清空当前选择。

## 系统架构

```text
FastAPI API（main.py）
  └─ LLM Agent（src/llm_agent.py）
       ├─ Session Manager（会话状态与约束合并）
       ├─ Scheduler（三阶段排课求解）
       ├─ Data Adapter（课程检索与位图冲突检测）
       └─ SQLite（虚拟演示课程数据）
```

前端为原生 HTML、CSS 和 JavaScript 实现的单页应用，集中在 `static/index.html`。

## 环境要求

- Python 3.10+
- DeepSeek、Moonshot 或其他兼容 OpenAI SDK 的模型接口
- 本地虚拟演示数据库 `scheduler.db`

## 数据说明

本项目比赛期间使用的真实课程数据不属于本仓库的公开内容，也不会上传到 GitHub。

开发和演示使用本地准备的虚拟数据库。`scheduler.db`、原始课程文件 `data/raw_courses.jsonl` 和中间产物 `output/position_slot_map.json` 均已通过 `.gitignore` 排除，不应提交真实课程数据或包含个人信息的数据。

如需从自有、已获授权的数据初始化数据库，需要准备：

- `data/raw_courses.jsonl`
- `output/position_slot_map.json`
- 可选的 `data/curriculum.json`

随后运行 `python init_db.py`。仓库中的截图仅用于展示系统效果，不能用于还原真实课程信息。

## 本地运行

在已准备好虚拟 `scheduler.db` 的前提下：

```powershell
# 1. 创建并激活虚拟环境（Windows PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装锁定依赖
python -m pip install -r requirements-lock.txt

# 3. 配置模型接口
Copy-Item .env.example .env
# 编辑 .env，填入自己的 LLM_API_KEY

# 4. 启动
python main.py
# 浏览器访问 http://localhost:8000
```

不要提交 `.env`，也不要在公开 Issue、日志或截图中暴露 API Key。

## 测试

```powershell
python -m pytest tests/ -q

# 运行指定类别
python -m pytest tests/test_unit.py -q -k "scheduler"
```

截至 2026-07-12，当前基线为 97 个测试全部通过。测试覆盖课程搜索、会话状态、三阶段排课、Agent 工具、快捷指令、冲突检测和 API 场景；默认测试不会调用真实 LLM 接口。

## 对话示例

```text
你：帮我选微积分A、英语阅读写作、基础物理学、一年级男生体育，尽量不要早八
你：帮我换一个微积分A的班级
你：换一个方案
你：清空，重新来
```

## 模型配置

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | 模型接口密钥 |
| `LLM_BASE_URL` | 兼容接口地址，默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名称，默认 `deepseek-chat` |

配置示例见 `.env.example`。

## 团队

本项目由 **清荷 Studio** 团队共同开发完成。

[@brightcolin](https://github.com/brightcolin) · [@YangAn8800](https://github.com/YangAn8800) · [@sayankk](https://github.com/sayankk) · [@rainlanelongings-cmd](https://github.com/rainlanelongings-cmd)

## 版权说明

本项目是团队共同创作的比赛作品，项目名称、代码、文档和视觉素材的相关权益由其各自权利人依法享有。

本仓库目前**未附开放源代码许可证**。公开可见不代表授予复制、修改、再发布或商业使用的许可。若需引用、展示或基于本项目进行二次开发，请事先取得项目团队及相关权利人的许可，并保留原项目与团队署名。

第三方平台、模型、学校和赛事名称及标识的权利归各自权利人所有。
