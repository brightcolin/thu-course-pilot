# CLAUDE.md

本仓库的项目说明、架构导航、开发命令、测试要求和安全边界统一维护在 [`AGENTS.md`](./AGENTS.md)。

Claude Code 在开始任何分析或修改前，必须先完整阅读并遵守 `AGENTS.md`。如本文件与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准。

## Claude Code 兼容说明

- 当前主要开发环境是 Windows PowerShell；优先使用 `AGENTS.md` 中的跨平台 Python 命令。
- 不要读取或输出 `.env`，也不要调用真实 LLM 接口，除非用户明确要求。
- 编辑前检查现有工作区状态，保留与任务无关的用户修改和未跟踪文件。
- 通用项目知识只更新 `AGENTS.md`，不要复制到本文件，以免两份说明再次漂移。
