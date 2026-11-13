# AGENTS.md — dsh-archive-panel

本项目的持久工作守则。新会话先读本文件，再读全局 `~/.dsh/AGENTS.md`。

## 任务与变更追踪（强制）

- 任务写 `TASK.json`、变更写 `CHANGELOG.jsonl`；**禁止手改这两个文件**，一律用工具：
  - 全局：`python3 ~/.dsh/tools/project/dsh_project.py ...`
  - 项目内：`python3 tools/dsh_project.py ...`（CI/离线用）
- 接到工作先 `task list`；开工 `task start <id>`；完成 `task done <id>`；有阻塞 `task block <id> --reason "..."`。
- 每个实际变更（代码/文档/结构）都要追加一条 `changelog add`；`kind` 用 feat/fix/refactor/perf/docs/test/chore/breaking/revert。
- 收尾：`task check`、`changelog check`、`layout check` 必须 exit 0。

## 目录与入库边界

- 角色→目录映射见 `.dsh-layout.json`；派生媒体进 `derived/`，交付物进角色目录，历史进 `archive/{evidence,legacy,external}/`。
- git 只收代码/文档/测试/模板；`derived/`、本地交付物、legacy/external 不入库（以 `.gitignore` 与 `layout check` 为准）。
- 路径解析用项目内的 `_paths.py`（pipeline 类项目）或等价的唯一路径模块；不写死 `parents[n]`。

## 收尾检查

```sh
python3 tools/dsh_project.py layout check
python3 tools/dsh_project.py task check
python3 tools/dsh_project.py changelog check
```
