# 🐏 未小羊 · 智能选课排课系统

通过自然语言对话与 AI 助手"未小羊"交流，自动完成课程搜索、约束提取、冲突解决和课表生成。

## ✨ 功能特性

- **自然语言选课**：直接说"选建筑与城市美学和风景之道，不想上早八"，AI 自动理解并排课
- **Function Calling Agent**：LLM 自主调用 5 个工具（搜索/更新约束/排课/查详情/重置）
- **多任务理解**：一句话可同时包含多门课程、多个约束，全部提取处理
- **三阶段自愈排课**：完美匹配 → 约束松弛 → 贪心舍弃
- **冲突智能解释**：用通俗语言说明哪些课冲突、为什么、怎么解决
- **硬/软约束区分**：自动识别"不要"（硬约束）和"尽量"（软约束）
- **实时约束展示**：底部面板显示已识别的课程和约束，可手动增删
- **课表交互**：点击课表中的课程 → 引用/更换/移除
- **弹窗选课**：浏览搜索 5000+ 课程，点击直接发送到对话
- **快捷指令**：简单操作（如"换一个方案""清空"）直接执行，无需等待 LLM

## 🖥 环境要求

- **Python** 3.8+（推荐 3.10+）
- **LLM API Key**：DeepSeek 或 Moonshot
  - DeepSeek：https://platform.deepseek.com/
  - Moonshot：https://platform.moonshot.cn/

## 📦 安装与启动

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API

编辑 `.env` 文件：

```ini
LLM_API_KEY=sk-你的密钥          # 必填
LLM_BASE_URL=https://api.deepseek.com   # DeepSeek 地址
LLM_MODEL=deepseek-chat                 # 模型名
```

如果使用 Moonshot Kimi：
```ini
LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_MODEL=kimi-k2.5
```

### 3. 初始化数据库（首次）

项目自带 `scheduler.db`，可跳过。如需重新生成：

```bash
python init_db.py
```

### 4. 启动

```bash
python main.py
```

访问 **http://localhost:8000**

## 🎮 使用说明

### 登录
输入名字即可开始（年级、院系可选）。

### 界面
```
┌──────────────────────┬──────────────────────┐
│    💬 对话区域        │    📅 课表区域        │
│                      │                      │
│  你：选建筑美学...    │   周一  周二  周三... │
│  未小羊：好的...      │   [课程] [课程]       │
│                      │                      │
├──────────────────────┤   统计 / 冲突日志     │
│ 📌 已识别约束         │                      │
│ [课程A ✕] [周一1-2 ✕]│                      │
├──────────────────────┤                      │
│ 📋  [输入框]  [发送]  │                      │
└──────────────────────┴──────────────────────┘
```

### 对话示例
```
你：我要选建筑与城市美学和风景之道，不想上早八
你：帮我再加一门极地建筑
你：把建筑与城市美学换一个班级
你：帮我去掉风景之道
你：换一个方案
你：清空，重新来
```

### 弹窗选课
点击 📋 按钮 → 搜索课程 → 点击课程 → 自动发送到对话

### 课表交互
点击课表中的课程 → 弹出菜单：引用至对话 / 更换班级 / 移除

## 🧪 运行测试

```bash
python -m pytest tests/ -v
```

测试覆盖（47 项）：
- 课程搜索、别名映射
- 会话管理（增删约束、合并禁用时间）
- 排课引擎三阶段
- 5 个工具的执行
- 快捷指令
- 位图冲突检测
- API 端点
- 8 组对话场景模拟

## ❓ FAQ

| 问题 | 解决方法 |
|------|----------|
| API 调用失败 | 检查 `.env` 中 API Key、BASE_URL、MODEL 是否正确 |
| 数据库初始化报错 | 确认 `data/raw_courses.jsonl` 和 `output/position_slot_map.json` 存在 |
| 排课总是无解 | 减少硬约束，或检查课程名是否准确 |
| 前端不加载 | 确认后端运行中且端口 8000 可访问 |
| 回复很慢 | 正常——Agent 可能需要多次工具调用。简单指令（换方案/清空）会秒回 |

## 📁 目录结构

```
├── main.py                 # FastAPI 后端
├── init_db.py              # 数据库初始化
├── scheduler.db            # SQLite 数据库
├── .env / .env.example     # API 配置
├── requirements.txt
├── pytest.ini
├── static/
│   ├── index.html          # 前端界面
│   └── mascot.png          # 未小羊头像
├── src/
│   ├── database.py         # L0 数据持久化
│   ├── data_adapter.py     # L1 数据适配
│   ├── session_manager.py  # L2 会话管理
│   ├── scheduler.py        # L3 排课引擎
│   └── llm_agent.py        # Agent（Function Calling）
├── data/                   # 课程数据
└── tests/                  # 测试（47项）
```
