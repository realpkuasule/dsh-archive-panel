#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh_project.py — DSH 全局项目规范命令行（目录 / TASK.json / CHANGELOG.jsonl）。

单一自包含脚本（仅标准库），既是全局安装版，也可拷贝进项目做 CI 自检：
  ~/.dsh/tools/project/dsh_project.py            # 全局安装位置
  <project>/tools/dsh_project.py                 # 项目 vendored 副本（CI 用）

三个数据契约（见 global/workspace-layout.md）：
  .dsh-layout.json   dsh-layout/1     项目角色→目录映射与入库边界
  TASK.json          dsh-task/1       任务追踪（结构化、可校验）
  CHANGELOG.jsonl    dsh-changelog/1  项目变更（只追加、一行一条）

常用：
  dsh_project.py init --profile app --name demo --dest ~/DSH/demo
  dsh_project.py init --adopt --root . --profile pipeline --roles core=engine,deliverables=shots
  dsh_project.py task add "实现登录" --priority P1 --owner lead --scope src/auth
  dsh_project.py task list / show / start / done / block / drop / check / next
  dsh_project.py changelog add --kind feat --summary "新增登录" --task T-0001
  dsh_project.py changelog check / log --since 2026-11-01
  dsh_project.py layout check [--ci] [--json]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TASK_SCHEMA = "dsh-task/1"
CHANGELOG_SCHEMA = "dsh-changelog/1"
LAYOUT_SCHEMA = "dsh-layout/1"
TASK_FILE = "TASK.json"
CHANGELOG_FILE = "CHANGELOG.jsonl"
LAYOUT_FILE = ".dsh-layout.json"

STATUSES = ("pending", "in_progress", "blocked", "done", "dropped")
OPEN_STATUSES = ("pending", "in_progress", "blocked")
PRIORITIES = ("P0", "P1", "P2", "P3")
KINDS = ("feat", "fix", "refactor", "perf", "docs", "test", "chore", "breaking", "revert")

HIDDEN_ALLOW = {".git", ".github", ".gitignore", ".gitattributes", LAYOUT_FILE}
JUNK_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
JUNK_SUFFIXES = (".pyc", ".pyo", ".log")
JUNK_NAMES = {".DS_Store", "Thumbs.db"}
# 常见生态产物目录：默认视为不入库（与 scaffold 生成的 .gitignore 对齐；仍可由 --ignore-globs 覆盖）
COMMON_IGNORE_GLOBS = ["node_modules/", ".venv/", "dist/", "build/", "out/",
                       "coverage/", ".next/", "target/", ".pytest_cache/", "__pycache__/"]

PROFILES: Dict[str, Dict[str, Any]] = {
    "empty": {
        "roles": {"src": "src", "docs": "docs", "tests": "tests", "tools": "tools"},
        "dirs": ["src", "docs", "tests", "tools"],
        "readmes": ["docs", "tests", "tools"],
        "gitignore": ["__pycache__/", "*.pyc", ".DS_Store", "*.log"],
    },
    "app": {
        "roles": {"src": "src", "docs": "docs", "tests": "tests", "tools": "tools",
                  "config": "config", "assets": "assets"},
        "dirs": ["src", "docs", "tests", "tools", "config", "assets"],
        "readmes": ["docs", "tests", "tools"],
        "gitignore": ["__pycache__/", "*.pyc", ".DS_Store", "*.log", "dist/", "build/",
                      "node_modules/", ".venv/", "*.egg-info/"],
    },
    "lib": {
        "roles": {"src": "src", "docs": "docs", "tests": "tests", "examples": "examples", "tools": "tools"},
        "dirs": ["src", "docs", "tests", "examples", "tools"],
        "readmes": ["docs", "tests", "examples"],
        "gitignore": ["__pycache__/", "*.pyc", ".DS_Store", "*.log", "dist/", "build/",
                      ".venv/", "*.egg-info/"],
    },
    "custom": {
        # 不规则仓库：只用 --roles 显式声明，不预置任何角色目录
        "roles": {}, "dirs": [], "readmes": [],
        "gitignore": ["__pycache__/", "*.pyc", ".DS_Store", "*.log", "node_modules/",
                      ".venv/", "dist/", "build/"],
    },
    "pipeline": {
        "roles": {"core": "engine", "orchestration": "pipeline", "tools": "tools",
                  "docs": "docs", "tests": "tests", "template": "template",
                  "inputs": "assets", "deliverables": "shots", "derived": "derived",
                  "preview": "preview.html", "archive": "archive"},
        "dirs": ["engine", "pipeline", "tools", "docs", "tests", "template", "assets",
                 "shots", "derived", "archive", "archive/evidence", "archive/legacy",
                 "archive/external"],
        "readmes": ["pipeline", "tools", "docs", "tests", "archive"],
        "gitignore": ["__pycache__/", "*.pyc", ".DS_Store", "*.log",
                      "shots/", "derived/", "preview.html",
                      "archive/legacy/", "archive/external/"],
        "deliverables_mode": "local",
    },
}

DEFAULT_REQUIRED_ROOT = ["README.md", "AGENTS.md", ".gitignore", TASK_FILE, CHANGELOG_FILE, LAYOUT_FILE]


# --------------------------------------------------------------------------- 基础
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except (ValueError, TypeError):
        return None


class ProjectError(Exception):
    """可路由的 CLI 错误：带退出码，main() 统一打印并返回，不泄漏 traceback。"""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def fail(msg: str, code: int = 2) -> None:
    raise ProjectError(msg, code)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"文件不存在：{path}")
    except UnicodeDecodeError as exc:
        fail(f"文件不是合法 UTF-8：{path}（{exc.reason}）")
    except OSError as exc:
        fail(f"文件读取失败：{path}（{exc}）")
    except json.JSONDecodeError as exc:
        fail(f"JSON 解析失败：{path}（{exc}）")


def atomic_write_text(path: Path, text: str, code: int = 2) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    except OSError as exc:
        fail(f"目录不可写：{path.parent}（{exc}）", code)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as exc:
        fail(f"写入失败：{path}（{exc}）", code)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def write_json(path: Path, data: Any, code: int = 3) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", code=code)


def find_root(start: Path) -> Path:
    p = start.expanduser().resolve()
    for cand in [p] + list(p.parents):
        if (cand / LAYOUT_FILE).is_file() or (cand / TASK_FILE).is_file() or (cand / ".git").exists():
            return cand
    return p


def resolve_root(arg: Optional[str]) -> Path:
    return find_root(Path(arg)) if arg else find_root(Path.cwd())


def role_dir(roles: Dict[str, str], role: str) -> Optional[str]:
    return roles.get(role) or None


def split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_roles(value: Optional[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in split_csv(value):
        if "=" not in item:
            fail(f"--roles 需要 k=v 形式：{item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# --------------------------------------------------------------------------- layout 文件
def load_layout(root: Path) -> Dict[str, Any]:
    path = root / LAYOUT_FILE
    if not path.is_file():
        fail(f"缺少 {LAYOUT_FILE}（先跑 dsh_project.py init --adopt）", 2)
    data = read_json(path)
    if not isinstance(data, dict):
        fail(f"{LAYOUT_FILE} 顶层必须是对象")
    return data


def path_matches_glob(rel: str, glob: str) -> bool:
    """ignore_globs 支持目录前缀与 fnmatch 通配（如 *.egg-info）。"""
    g = str(glob).strip().rstrip("/")
    if not g:
        return False
    if any(ch in g for ch in "*?["):
        return (fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g + "/*")
                or fnmatch.fnmatch(rel.split("/", 1)[0], g))
    return rel == g or rel.startswith(g + "/")


def default_ignore_globs(roles: Dict[str, str], mode: str) -> List[str]:
    # 显式角色路径优先：deliverables/core 等恰好叫 dist/build 时，不被公共忽略表压过
    role_paths = {str(p).rstrip("/"): role for role, p in roles.items()}
    out: List[str] = [
        g for g in COMMON_IGNORE_GLOBS
        if not (g.rstrip("/") in role_paths and role_paths[g.rstrip("/")] != "derived")
    ]
    if roles.get("derived"):
        out.append(str(roles["derived"]).rstrip("/") + "/")
    if mode == "local" and roles.get("deliverables"):
        out.append(str(roles["deliverables"]).rstrip("/") + "/")
    if roles.get("archive"):
        base = str(roles["archive"]).rstrip("/")
        out += [f"{base}/legacy/", f"{base}/external/"]
    if roles.get("preview"):
        out.append(str(roles["preview"]))
    return list(dict.fromkeys(out))


def validate_layout(data: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if data.get("schema") != LAYOUT_SCHEMA:
        errs.append(f"{LAYOUT_FILE}.schema 应为 {LAYOUT_SCHEMA!r}，实际 {data.get('schema')!r}")
    if not isinstance(data.get("profile"), str) or not data.get("profile"):
        errs.append(f"{LAYOUT_FILE}.profile 缺失")
    roles = data.get("roles")
    if not isinstance(roles, dict) or not roles:
        errs.append(f"{LAYOUT_FILE}.roles 必须是非空对象")
        return errs
    seen: Dict[str, str] = {}
    for role, path in roles.items():
        if not isinstance(path, str) or not path.strip():
            errs.append(f"{LAYOUT_FILE}.roles.{role} 必须是非空相对路径")
            continue
        if Path(path).is_absolute():
            errs.append(f"{LAYOUT_FILE}.roles.{role} 不能是绝对路径：{path}")
        if ".." in Path(path).parts:
            errs.append(f"{LAYOUT_FILE}.roles.{role} 不能包含 ..：{path}")
        top = path.rstrip("/").split("/")[0]
        if top in seen and seen[top] != role:
            errs.append(f"{LAYOUT_FILE}.roles 中 {role} 与 {seen[top]} 共用顶层 {top}/")
        seen[top] = role
    if not isinstance(data.get("required_root", []), list):
        errs.append(f"{LAYOUT_FILE}.required_root 必须是数组")
    if not isinstance(data.get("root_files", []), list):
        errs.append(f"{LAYOUT_FILE}.root_files 必须是数组")
    tracking = data.get("tracking")
    if tracking is not None and not isinstance(tracking, dict):
        errs.append(f"{LAYOUT_FILE}.tracking 必须是对象")
    return errs


# --------------------------------------------------------------------------- TASK.json
def load_tasks(root: Path) -> Dict[str, Any]:
    path = root / TASK_FILE
    if not path.is_file():
        fail(f"缺少 {TASK_FILE}（先跑 dsh_project.py init --adopt 或 task init）", 2)
    data = read_json(path)
    if not isinstance(data, dict):
        fail(f"{TASK_FILE} 顶层必须是对象")
    return data


def validate_tasks(data: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errs: List[str] = []
    warns: List[str] = []
    if data.get("schema") != TASK_SCHEMA:
        errs.append(f"{TASK_FILE}.schema 应为 {TASK_SCHEMA!r}，实际 {data.get('schema')!r}")
    for key in ("project", "created", "updated"):
        if not isinstance(data.get(key), str) or not data.get(key):
            errs.append(f"{TASK_FILE}.{key} 缺失")
    for key in ("created", "updated"):
        if isinstance(data.get(key), str) and not parse_iso(data[key]):
            errs.append(f"{TASK_FILE}.{key} 不是合法 ISO8601：{data[key]!r}")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        errs.append(f"{TASK_FILE}.tasks 必须是数组")
        return errs, warns
    ids: Dict[str, dict] = {}
    for idx, task in enumerate(tasks):
        where = f"tasks[{idx}]"
        if not isinstance(task, dict):
            errs.append(f"{where} 必须是对象")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not re.fullmatch(r"T-\d{4,}", tid):
            errs.append(f"{where}.id 必须是 T-0001 形式：{tid!r}")
            continue
        if tid in ids:
            errs.append(f"{where}.id 重复：{tid}")
            continue
        ids[tid] = task
        if not isinstance(task.get("title"), str) or not task["title"].strip():
            errs.append(f"{where}({tid}).title 缺失")
        status = task.get("status")
        if status not in STATUSES:
            errs.append(f"{where}({tid}).status 非法：{status!r}")
        if task.get("priority") not in PRIORITIES:
            errs.append(f"{where}({tid}).priority 非法：{task.get('priority')!r}")
        for key in ("created", "updated"):
            if not isinstance(task.get(key), str) or not parse_iso(task[key]):
                errs.append(f"{where}({tid}).{key} 缺失或非法")
        if status == "done" and not parse_iso(task.get("done_at") or ""):
            errs.append(f"{where}({tid}) 已 done 但 done_at 缺失")
        if status != "done" and task.get("done_at"):
            errs.append(f"{where}({tid}) 非 done 却残留 done_at")
        if status == "blocked" and not (task.get("blocked_reason") or "").strip():
            errs.append(f"{where}({tid}) 已 blocked 但 blocked_reason 缺失")
        if status != "blocked" and task.get("blocked_reason"):
            errs.append(f"{where}({tid}) 非 blocked 却残留 blocked_reason")
        deps = task.get("depends_on", [])
        if not isinstance(deps, list):
            errs.append(f"{where}({tid}).depends_on 必须是数组")
            continue
        for dep in deps:
            if dep == tid:
                errs.append(f"{where}({tid}).depends_on 不能依赖自己")
            elif not isinstance(dep, str) or not re.fullmatch(r"T-\d{4,}", dep):
                errs.append(f"{where}({tid}).depends_on 非法：{dep!r}")
            elif dep not in ids and any(t.get("id") == dep for t in tasks if isinstance(t, dict)):
                pass  # 依赖可能在后面定义，交叉检查在最后
    # 依赖存在性 + 环检测
    def visit(tid: str, chain: List[str]) -> None:
        if tid in chain:
            errs.append("任务依赖成环：" + " -> ".join(chain + [tid]))
            return
        task = ids.get(tid)
        if not task:
            return
        for dep in task.get("depends_on", []) or []:
            if isinstance(dep, str) and dep not in ids:
                errs.append(f"{tid}.depends_on 指向不存在的任务：{dep}")
            elif isinstance(dep, str):
                visit(dep, chain + [tid])

    for tid in ids:
        visit(tid, [])
    return errs, warns


def open_tasks(data: Dict[str, Any]) -> List[dict]:
    return [t for t in data.get("tasks", []) if t.get("status") in OPEN_STATUSES]


def next_task_id(data: Dict[str, Any]) -> str:
    nums = [int(t["id"].split("-")[1]) for t in data.get("tasks", [])
            if isinstance(t.get("id"), str) and re.fullmatch(r"T-\d{4,}", t["id"])]
    return f"T-{(max(nums) + 1) if nums else 1:04d}"


def save_tasks(root: Path, data: Dict[str, Any]) -> None:
    """写前校验：任何会写盘的任务变更都必须先通过契约，避免把文件写成非法态。"""
    data["updated"] = now_iso()
    errs, _warns = validate_tasks(data)
    if errs:
        fail("TASK.json 校验失败，未写入：" + "；".join(errs[:5]), 3)
    write_json(root / TASK_FILE, data)


def find_task(data: Dict[str, Any], tid: str) -> dict:
    for task in data.get("tasks", []):
        if task.get("id") == tid:
            return task
    fail(f"找不到任务：{tid}", 3)
    return {}


def touch_task(task: dict) -> None:
    task["updated"] = now_iso()


def cmd_task(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    if args.action == "init":
        if (root / TASK_FILE).exists() and not args.force:
            fail(f"{TASK_FILE} 已存在（要重建加 --force）", 3)
        data = {
            "schema": TASK_SCHEMA,
            "project": args.project or root.name,
            "created": now_iso(),
            "updated": now_iso(),
            "tasks": [],
        }
        save_tasks(root, data)
        print(f"[task] 已初始化 {root / TASK_FILE}")
        return 0
    if args.action == "add":
        data = load_tasks(root)
        tid = next_task_id(data)
        ts = now_iso()
        task = {
            "id": tid,
            "title": args.title.strip(),
            "status": "pending",
            "priority": args.priority,
            "owner": args.owner or "",
            "created": ts,
            "updated": ts,
            "done_at": None,
            "blocked_reason": None,
            "tags": split_csv(args.tags),
            "depends_on": split_csv(args.deps),
            "scope": split_csv(args.scope),
            "notes": args.notes or "",
            "evidence": [],
        }
        if not task["title"]:
            fail("title 不能为空", 3)
        data["tasks"].append(task)
        save_tasks(root, data)
        print(tid)
        return 0
    data = load_tasks(root)
    if args.action in ("list", "next"):
        tasks = data.get("tasks", [])
        if args.action == "next":
            errs, _warns = validate_tasks(data)
            if errs:
                fail("TASK.json 不合法，next 拒绝执行：" + "；".join(errs[:3]), 3)
            done_ids = {t["id"] for t in tasks if t.get("status") == "done"}
            cands = [t for t in tasks if t.get("status") == "pending"
                     and all(d in done_ids for d in (t.get("depends_on") or []))]
            cands.sort(key=lambda t: (PRIORITIES.index(t.get("priority", "P2")), t["id"]))
            if not cands:
                print("NONE")
                return 0
            print(cands[0]["id"], cands[0]["title"])
            return 0
        view = tasks if args.all else [t for t in tasks if t.get("status") in OPEN_STATUSES]
        if args.status:
            view = [t for t in view if t.get("status") == args.status]
        if args.json:
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            for t in view:
                print(f'{t["id"]}  {t.get("status","?"):11s} {t.get("priority","?"):2s}  '
                      f'{(t.get("owner") or "-"):6s}  {t.get("title","")}')
            if not view:
                print("(no tasks)")
        return 0
    if args.action == "show":
        task = find_task(data, args.id)
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0
    if args.action == "check":
        errs, warns = validate_tasks(data)
        if args.json:
            print(json.dumps({"ok": not errs, "errors": errs, "warnings": warns}, ensure_ascii=False, indent=2))
        else:
            for e in errs:
                print(f"FAIL {e}")
            for w in warns:
                print(f"WARN {w}")
            print(f"[task] {'PASS' if not errs else 'FAIL'} · tasks={len(data.get('tasks', []))} "
                  f"open={len(open_tasks(data))} errors={len(errs)} warnings={len(warns)}")
        return 0 if not errs else 3
    # 单任务变更（状态机 + 字段一致性）
    task = find_task(data, args.id)
    ALLOWED = {
        "pending": {"start", "block", "drop"},
        "in_progress": {"start", "done", "block", "drop"},
        "blocked": {"unblock", "drop"},
        "done": set(),
        "dropped": set(),
    }
    action = args.action
    if action == "update":
        pass  # 字段更新另检
    elif action == "start" and task["status"] not in ("pending", "in_progress"):
        fail(f"{task['id']} 状态为 {task['status']}，不能 start（先 unblock/新建）", 3)
    elif action in ("done", "block", "unblock", "drop") and action not in ALLOWED.get(task["status"], set()):
        fail(f"{task['id']} 状态为 {task['status']}，不允许 {action}", 3)
    if action == "start":
        task["status"] = "in_progress"
    elif action == "done":
        task["status"] = "done"
        task["done_at"] = now_iso()
        task["blocked_reason"] = None
    elif action == "drop":
        task["status"] = "dropped"
        task["blocked_reason"] = None
        if args.reason:
            task["notes"] = (task.get("notes") or "") + (("\n" if task.get("notes") else "") + f"drop: {args.reason}")
    elif action == "block":
        if not args.reason:
            fail("block 需要 --reason", 3)
        task["status"] = "blocked"
        task["blocked_reason"] = args.reason
    elif action == "unblock":
        task["status"] = "pending"
        task["blocked_reason"] = None
    elif action == "update":
        if args.title:
            task["title"] = args.title
        if args.priority:
            task["priority"] = args.priority
        if args.owner is not None:
            task["owner"] = args.owner
        if args.notes is not None:
            task["notes"] = args.notes
        if args.set_status:
            new_status = args.set_status
            if new_status != task["status"] and new_status not in ("pending", "in_progress", "blocked", "done", "dropped"):
                fail(f"非法目标状态：{new_status}", 3)
            task["status"] = new_status
            if new_status == "done":
                task["done_at"] = now_iso()
                task["blocked_reason"] = None
            elif new_status != "blocked":
                task["blocked_reason"] = None
                if new_status != "done":
                    task["done_at"] = None
    else:
        fail(f"未知 task 动作：{args.action}", 2)
    if task["status"] in ("done", "dropped"):
        task["done_at"] = task.get("done_at") if task["status"] == "done" else None
    touch_task(task)
    save_tasks(root, data)
    print(f"{task['id']} -> {task['status']}")
    return 0


# --------------------------------------------------------------------------- CHANGELOG.jsonl
def load_changelog(root: Path) -> List[dict]:
    path = root / CHANGELOG_FILE
    if not path.is_file():
        fail(f"缺少 {CHANGELOG_FILE}（先跑 dsh_project.py init --adopt）", 2)
    entries: List[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{CHANGELOG_FILE} 不是合法 UTF-8：{exc.reason}", 4)
    except OSError as exc:
        fail(f"{CHANGELOG_FILE} 读取失败：{exc}", 4)
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"{CHANGELOG_FILE}:{lineno} JSON 解析失败：{exc}", 4)
        if not isinstance(entry, dict):
            fail(f"{CHANGELOG_FILE}:{lineno} 必须是 JSON 对象", 4)
        entries.append(entry)
    return entries


def validate_changelog(entries: List[dict]) -> List[str]:
    errs: List[str] = []
    last_ts: Optional[datetime] = None
    for idx, entry in enumerate(entries, 1):
        where = f"{CHANGELOG_FILE}:{idx}"
        if entry.get("schema") != CHANGELOG_SCHEMA:
            errs.append(f"{where}.schema 应为 {CHANGELOG_SCHEMA!r}，实际 {entry.get('schema')!r}")
        ts = entry.get("ts")
        dt = parse_iso(ts) if isinstance(ts, str) else None
        if dt is None:
            errs.append(f"{where}.ts 缺失或非法 ISO8601：{ts!r}")
        else:
            if last_ts is not None and dt < last_ts:
                errs.append(f"{where}.ts 早于上一条（JSONL 必须按时间追加）：{ts}")
            last_ts = dt
        if entry.get("kind") not in KINDS:
            errs.append(f"{where}.kind 非法：{entry.get('kind')!r}")
        if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
            errs.append(f"{where}.summary 缺失")
        for key in ("scope", "files"):
            if key in entry and not (isinstance(entry[key], list) and all(isinstance(x, str) for x in entry[key])):
                errs.append(f"{where}.{key} 必须是字符串数组")
        if "task" in entry and not re.fullmatch(r"T-\d{4,}", str(entry.get("task") or "")):
            errs.append(f"{where}.task 非法：{entry.get('task')!r}")
    return errs


def append_changelog(root: Path, entry: Dict[str, Any]) -> None:
    errs = validate_changelog([entry])
    if errs:
        fail("；".join(errs), 4)
    path = root / CHANGELOG_FILE
    # 追加前：已有文件必须整体合法，且新 ts 不早于最后一条（JSONL 只追加，无法回滚）
    last_ts: Optional[datetime] = None
    if path.is_file() and path.stat().st_size > 0:
        existing = load_changelog(root)  # 坏 JSON/非 UTF-8/非对象 → exit 4
        errs_existing = validate_changelog(existing)
        if errs_existing:
            fail("CHANGELOG 当前不合法，先修再追加：" + "；".join(errs_existing[:3]), 4)
        if existing:
            last_ts = parse_iso(existing[-1].get("ts"))
    new_ts = parse_iso(entry.get("ts"))
    if last_ts is not None and new_ts is not None and new_ts < last_ts:
        fail(f"ts 早于上一条（{entry.get('ts')} < {last_ts.isoformat()}）；JSONL 只追加，"
             "补历史请先归档/重建日志", 4)
    prefix = ""
    if path.is_file() and path.stat().st_size > 0:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                prefix = "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        fail(f"写入失败：{path}（{exc}）", 4)


def cmd_changelog(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    if args.action == "init":
        if (root / CHANGELOG_FILE).exists() and not args.force:
            fail(f"{CHANGELOG_FILE} 已存在（要重建加 --force）", 4)
        atomic_write_text(root / CHANGELOG_FILE, "")
        print(f"[changelog] 已初始化 {root / CHANGELOG_FILE}（空文件）")
        return 0
    if args.action == "add":
        entry: Dict[str, Any] = {
            "schema": CHANGELOG_SCHEMA,
            "ts": args.ts or now_iso(),
            "kind": args.kind,
            "summary": args.summary.strip(),
        }
        if args.task:
            entry["task"] = args.task
        if args.scope:
            entry["scope"] = split_csv(args.scope)
        if args.files:
            entry["files"] = split_csv(args.files)
        if args.commit:
            entry["commit"] = args.commit
        if args.agent:
            entry["agent"] = args.agent
        if args.notes:
            entry["notes"] = args.notes
        if args.breaking:
            entry["kind"] = "breaking"
            entry["breaking"] = True
        append_changelog(root, entry)
        print(f"[changelog] 已追加：{entry['kind']} {entry['summary']}")
        return 0
    entries = load_changelog(root)
    if args.action == "check":
        errs = validate_changelog(entries)
        if args.json:
            print(json.dumps({"ok": not errs, "entries": len(entries), "errors": errs}, ensure_ascii=False, indent=2))
        else:
            for e in errs:
                print(f"FAIL {e}")
            print(f"[changelog] {'PASS' if not errs else 'FAIL'} · entries={len(entries)} errors={len(errs)}")
        return 0 if not errs else 4
    # log
    view = entries
    if args.since:
        bound = parse_iso(args.since)
        if not bound:
            fail(f"--since 不是合法 ISO8601：{args.since}", 4)
        view = [e for e in view if (parse_iso(e.get("ts", "")) or datetime.min) >= bound]
    if args.kind:
        view = [e for e in view if e.get("kind") == args.kind]
    if args.task:
        view = [e for e in view if e.get("task") == args.task]
    if args.limit:
        view = view[-args.limit:]
    if args.json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        for e in view:
            print(f"{e.get('ts','?')}  {e.get('kind','?'):8s}  {e.get('summary','')}"
                  + (f"  [{e['task']}]" if e.get("task") else ""))
    return 0


# --------------------------------------------------------------------------- 项目骨架
ROLE_DESC = {
    "core": "核心代码", "src": "源码", "orchestration": "门链/编排",
    "tools": "工具链", "docs": "文档", "tests": "测试", "template": "模板",
    "inputs": "输入素材", "assets": "素材", "deliverables": "交付单元",
    "derived": "派生媒体", "preview": "本地预览", "archive": "历史归档",
    "config": "配置", "examples": "示例",
}


def generated_readme(name: str, profile: str, roles: Dict[str, str], required_root: List[str]) -> str:
    lines = [f"# {name}", "", f"> 本项目遵循 **DSH 全局布局规范 v1**（`{LAYOUT_SCHEMA}`，profile: `{profile}`）。",
             "> 规则与命令：`~/.dsh/tools/project/dsh_project.py`；全局守则见 `~/.dsh/AGENTS.md`。", "",
             "## 目录角色", "", "| 角色 | 目录 | 用途 |", "|---|---|---|"]
    for role, path in roles.items():
        lines.append(f"| `{role}` | `{path}` | {ROLE_DESC.get(role, role)} |")
    lines += ["", "## 必需根文件", "",
              "| 文件 | 作用 |", "|---|---|",
              "| `TASK.json` | 任务追踪（`dsh-task/1`） |",
              "| `CHANGELOG.jsonl` | 项目变更（`dsh-changelog/1`，只追加） |",
              "| `.dsh-layout.json` | 角色→目录映射与入库边界 |",
              "| `AGENTS.md` | 本项目守则（新会话先读） |", ""]
    lines += ["## 常用命令", "", "```sh",
              "python3 tools/dsh_project.py task list",
              "python3 tools/dsh_project.py task add \"任务标题\" --priority P1",
              "python3 tools/dsh_project.py task done T-0001",
              "python3 tools/dsh_project.py changelog add --kind feat --summary \"...\" --task T-0001",
              "python3 tools/dsh_project.py layout check",
              "```", ""]
    return "\n".join(lines)


def generated_agents(name: str) -> str:
    return f"""# AGENTS.md — {name}

本项目的持久工作守则。新会话先读本文件，再读全局 `~/.dsh/AGENTS.md`。

## 任务与变更追踪（强制）

- 任务写 `TASK.json`、变更写 `CHANGELOG.jsonl`；**禁止手改这两个文件**，一律用工具：
  - 全局：`python3 ~/.dsh/tools/project/dsh_project.py ...`
  - 项目内：`python3 tools/dsh_project.py ...`（CI/离线用）
- 接到工作先 `task list`；开工 `task start <id>`；完成 `task done <id>`；有阻塞 `task block <id> --reason "..."`。
- 每个实际变更（代码/文档/结构）都要追加一条 `changelog add`；`kind` 用 feat/fix/refactor/perf/docs/test/chore/breaking/revert。
- 收尾：`task check`、`changelog check`、`layout check` 必须 exit 0。

## 目录与入库边界

- 角色→目录映射见 `.dsh-layout.json`；派生媒体进 `derived/`，交付物进角色目录，历史进 `archive/{{evidence,legacy,external}}/`。
- git 只收代码/文档/测试/模板；`derived/`、本地交付物、legacy/external 不入库（以 `.gitignore` 与 `layout check` 为准）。
- 路径解析用项目内的 `_paths.py`（pipeline 类项目）或等价的唯一路径模块；不写死 `parents[n]`。

## 收尾检查

```sh
python3 tools/dsh_project.py layout check
python3 tools/dsh_project.py task check
python3 tools/dsh_project.py changelog check
```
"""


def generated_gitignore(profile: str, roles: Dict[str, str], mode: str, extra: List[str]) -> str:
    base = list(PROFILES.get(profile, {}).get("gitignore", []))
    # 目录/路径类以 marker 的 ignore_globs 为准（已剔除 tracked deliverables 等同名角色路径）；
    # profile 只贡献文件模式（*.pyc / *.log / *.egg-info/ / .DS_Store）。
    file_patterns = [x for x in base if any(ch in x for ch in "*?[") or x == ".DS_Store"]
    lines = list(dict.fromkeys(file_patterns + list(extra)))
    return "\n".join(["# DSH 全局布局规范 v1（dsh-layout/1）", *lines, ""])


def generated_ci() -> str:
    return """name: dsh-layout

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  layout:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: DSH layout / tasks / changelog
        run: python3 tools/dsh_project.py layout check --ci
"""


def changelog_bootstrap(name: str) -> str:
    entry = {
        "schema": CHANGELOG_SCHEMA,
        "ts": now_iso(),
        "kind": "chore",
        "summary": f"初始化项目骨架（{LAYOUT_SCHEMA}，profile {name}）",
        "scope": ["."],
        "agent": "dsh_project.init",
    }
    return json.dumps(entry, ensure_ascii=False) + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    dest_arg = getattr(args, "dest", None) or getattr(args, "root", None) or "."
    dest = Path(dest_arg).expanduser()
    if not dest.is_absolute():
        base = Path(getattr(args, "root", None) or ".").expanduser().resolve()
        if base.name == dest.name and str(base).endswith(str(dest)):
            base = base.parent
        dest = (base / dest).resolve()
    else:
        dest = dest.resolve()
    profile = args.profile
    base = PROFILES.get(profile)
    if base is None:
        fail(f"未知 profile：{profile}（可选 {', '.join(PROFILES)}）")
    roles: Dict[str, str] = dict(base["roles"])
    roles.update(parse_roles(args.roles))
    if profile == "custom" and not roles:
        fail("custom profile 需要 --roles（例如 --roles core=plugins,tools=tools）")
    mode = args.deliverables_mode or ("local" if roles.get("deliverables") else "tracked")
    name = args.name or dest.name
    required_root = list(DEFAULT_REQUIRED_ROOT)
    root_files = split_csv(args.root_files)
    dir_readmes = split_csv(args.dir_readmes) or list(base.get("readmes", []))
    ignore_globs = split_csv(args.ignore_globs) or default_ignore_globs(roles, mode)
    marker = {
        "schema": LAYOUT_SCHEMA,
        "profile": profile,
        "name": name,
        "created": now_iso(),
        "roles": roles,
        "required_root": required_root,
        "root_files": root_files,
        "dir_readmes": dir_readmes,
        "tracking": {"deliverables_mode": mode, "ignore_globs": ignore_globs},
    }
    marker_errs = validate_layout(marker)
    if marker_errs:
        fail("布局参数不合法（未写盘）：" + "；".join(marker_errs[:5]), 2)
    generated = {
        "README.md": generated_readme(name, profile, roles, required_root),
        "AGENTS.md": generated_agents(name),
        ".gitignore": generated_gitignore(profile, roles, mode, ignore_globs),
        LAYOUT_FILE: json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        TASK_FILE: json.dumps({"schema": TASK_SCHEMA, "project": name, "created": now_iso(),
                               "updated": now_iso(), "tasks": []}, ensure_ascii=False, indent=2) + "\n",
    }
    docs_role = roles.get("docs")
    if docs_role:
        generated[f"{str(docs_role).rstrip('/')}/README.md"] = (
            f"# {docs_role} — {name} 文档\n\n文档索引。新增文档必须登记到这里。\n"
        )
    if roles.get("tools"):
        generated[".github/workflows/dsh-layout.yml"] = generated_ci()
    preview = roles.get("preview")
    if preview and Path(str(preview)).suffix:
        generated[str(preview)] = (
            "<!doctype html>\n<html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            f"<title>{name} preview</title>\n"
            "<body style=\"font-family:system-ui;padding:2rem\">"
            f"<h1>{name}</h1><p>本地预览占位：按项目需要替换（此文件不入库）。</p></body></html>\n"
        )
    # CHANGELOG：新项目写 bootstrap；--adopt 默认留空以便补历史
    if not args.adopt:
        generated[CHANGELOG_FILE] = changelog_bootstrap(name)
    else:
        generated[CHANGELOG_FILE] = ""
    # 目录：完全由角色映射推导（--roles 覆盖时不会残留 profile 默认目录）
    dirs = [str(path).rstrip("/") for path in roles.values()
            if isinstance(path, str) and path and not Path(path).suffix]
    if roles.get("archive"):
        ar = str(roles["archive"]).rstrip("/")
        dirs += [f"{ar}/evidence", f"{ar}/legacy", f"{ar}/external"]
    dirs += [d.rstrip("/") for d in dir_readmes]
    dirs = sorted({d for d in dirs if d})
    # 已有内容保护（dry-run 不创建目录）
    existing = [p.name for p in dest.iterdir()] if dest.is_dir() else []
    if existing and not (args.adopt or args.force):
        fail(f"目标目录非空：{dest}（已有项目用 --adopt，确要覆盖加 --force）")
    plan: List[str] = []
    if args.dry_run:
        for d in dirs:
            plan.append(f"DIR  {d}/")
        for rel in generated:
            plan.append(f"FILE {rel}")
        print("\n".join(plan))
        return 0
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail(f"无法创建目标目录：{dest}（{exc}）")
    for d in dirs:
        try:
            (dest / d).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fail(f"无法创建目录：{dest / d}（{exc}）")
    TRACKING_FILES = (TASK_FILE, CHANGELOG_FILE, LAYOUT_FILE)
    for rel, content in generated.items():
        target = dest / rel
        if target.exists() and rel in TRACKING_FILES:
            # 任务板/变更日志/布局凭证永不因 --force 清空；布局只合并
            if rel == LAYOUT_FILE:
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    old = {}
                merged = {**old, **marker, "roles": {**old.get("roles", {}), **roles}}
                merged["required_root"] = old.get("required_root", required_root)
                merged["root_files"] = sorted(set(old.get("root_files", []) + root_files))
                merged["dir_readmes"] = sorted(set(old.get("dir_readmes", []) + dir_readmes))
                old_globs = (old.get("tracking") or {}).get("ignore_globs")
                if args.ignore_globs:
                    merged_globs = ignore_globs          # 显式给出 → 完全替换
                elif old_globs is not None:
                    merged_globs = old_globs             # 未给出 → 保留既有，避免默认表回流
                else:
                    merged_globs = ignore_globs
                track = {**old.get("tracking", {}),
                         "deliverables_mode": old.get("tracking", {}).get("deliverables_mode", mode),
                         "ignore_globs": sorted(set(merged_globs))}
                merged["tracking"] = track
                atomic_write_text(target, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
                plan.append(f"MERGE {rel}")
            else:
                plan.append(f"PRESERVE {rel}（追踪文件，不覆盖）")
            continue
        if target.exists() and not args.force:
            plan.append(f"SKIP  {rel}（已存在）")
            continue
        if target.exists() and rel == LAYOUT_FILE:
            pass
        atomic_write_text(target, content)
        plan.append(f"WRITE {rel}")
    # 目录 README
    for d in dir_readmes:
        path = dest / d
        if path.is_dir():
            rp = path / "README.md"
            if not rp.exists():
                role = next((r for r, p in roles.items() if p.rstrip("/") == d), d)
                rp.write_text(f"# {d} — {ROLE_DESC.get(role, role)}\n\n本目录角色：`{role}`（见根 `.dsh-layout.json`）。\n", encoding="utf-8")
                plan.append(f"WRITE {d}/README.md")
    # 项目内 vendored CLI（仅当显式声明 tools 角色；custom 仓库可不声明）
    tools_dir = roles.get("tools")
    if tools_dir:
        tool_target = dest / str(tools_dir).rstrip("/") / "dsh_project.py"
        if not tool_target.exists() or args.force:
            try:
                tool_target.parent.mkdir(parents=True, exist_ok=True)
                tool_target.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
                tool_target.chmod(0o755)
            except OSError as exc:
                fail(f"写入 vendored CLI 失败：{tool_target}（{exc}）")
            plan.append(f"WRITE {str(tools_dir).rstrip('/')}/dsh_project.py")
    else:
        plan.append("SKIP  tools/dsh_project.py（未声明 tools 角色：不生成 vendored CLI/CI，CI 可用全局 CLI）")
    if args.git_init and not (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=False)
        plan.append("GIT   init")
    print("\n".join(plan))
    print(f"[init] {dest}  profile={profile}  roles={len(roles)}  files={len(generated)}")
    return 0


# --------------------------------------------------------------------------- layout check
def git_tracked(root: Path) -> Optional[List[str]]:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, check=True)
        return [x for x in out.stdout.decode("utf-8", "replace").split("\0") if x]
    except (OSError, subprocess.CalledProcessError):
        return None


def is_junk(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in JUNK_PARTS for part in parts):
        return True
    if parts[-1] in JUNK_NAMES or rel.endswith(JUNK_SUFFIXES):
        return True
    return False


def cmd_layout(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    errors: List[str] = []
    warns: List[str] = []
    marker_path = root / LAYOUT_FILE
    if not marker_path.is_file():
        fail(f"{root} 缺少 {LAYOUT_FILE}（先跑 dsh_project.py init --adopt）", 2)
    marker = read_json(marker_path)
    errors += validate_layout(marker)
    roles: Dict[str, str] = marker.get("roles") or {}
    required_root = marker.get("required_root") or DEFAULT_REQUIRED_ROOT
    root_files = marker.get("root_files") or []
    for rel in required_root:
        if not (root / rel).exists():
            errors.append(f"缺少必需根文件：{rel}")
    allowed = set(HIDDEN_ALLOW)
    allowed |= {str(x) for x in required_root}
    allowed |= {str(x) for x in root_files}
    for role, path in roles.items():
        top = str(path).rstrip("/").split("/")[0]
        if top:
            allowed.add(top)
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name not in allowed:
            errors.append(f"未知顶层条目：{entry.name}（不在角色映射/必需文件/白名单内）")
    for role, path in roles.items():
        target = root / str(path)
        is_file_role = bool(Path(path).suffix)
        if not target.exists():
            (warns if role == "preview" else errors).append(f"角色 {role} 的路径不存在：{path}")
        elif is_file_role and not target.is_file():
            errors.append(f"角色 {role} 应是文件：{path}")
        elif not is_file_role and not target.is_dir():
            errors.append(f"角色 {role} 应是目录：{path}")
    for rel in marker.get("dir_readmes") or []:
        d = root / rel
        if d.is_dir() and not (d / "README.md").exists():
            warns.append(f"目录缺少 README.md：{rel}")
    # 入库边界
    tracked = git_tracked(root)
    if tracked is None:
        warns.append("未检测到 git 仓库，跳过入库边界检查")
    else:
        for glob in (marker.get("tracking") or {}).get("ignore_globs") or []:
            hits = [f for f in tracked if path_matches_glob(f, glob)]
            if hits:
                errors.append(f"派生/交付目录被 git 跟踪：{glob}（{len(hits)} 个文件，例 {hits[0]}）")
        junk = [f for f in tracked if is_junk(f)]
        if junk:
            errors.append(f"机器产物被 git 跟踪：{junk[:3]}")
    # TASK / CHANGELOG
    errs_t: List[str] = []
    errs_c: List[str] = []
    try:
        errs_t, _ = validate_tasks(load_tasks(root))
    except ProjectError as exc:
        errs_t = [str(exc)]
    try:
        errs_c = validate_changelog(load_changelog(root))
    except ProjectError as exc:
        errs_c = [str(exc)]
    errors += [f"TASK: {e}" for e in errs_t]
    errors += [f"CHANGELOG: {e}" for e in errs_c]
    code = 3 if errs_t else (4 if errs_c else (2 if errors else 0))
    if args.json:
        print(json.dumps({"ok": not errors, "root": str(root), "errors": errors,
                          "warnings": warns}, ensure_ascii=False, indent=2))
    elif args.ci:
        print(f"[layout] {'PASS' if not errors else 'FAIL'} root={root} "
              f"roles={len(roles)} errors={len(errors)} warnings={len(warns)}")
        for e in errors[:8]:
            print(f"  FAIL {e}")
        for w in warns[:8]:
            print(f"  WARN {w}")
    else:
        for e in errors:
            print(f"FAIL {e}")
        for w in warns:
            print(f"WARN {w}")
        print(f"[layout] {'PASS' if not errors else 'FAIL'} · root={root} roles={len(roles)} "
              f"errors={len(errors)} warnings={len(warns)}")
    return code if not errors else max(2, code)


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dsh_project.py",
        description="DSH 全局项目规范：布局（dsh-layout/1）+ TASK.json（dsh-task/1）+ CHANGELOG.jsonl（dsh-changelog/1）",
    )
    p.add_argument("--root", help="项目根（默认从 cwd 向上查找 .dsh-layout.json/TASK.json/.git）")
    p.add_argument("--version", action="version", version=f"dsh_project 1.0.0 ({LAYOUT_SCHEMA})")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    pi = sub.add_parser("init", help="生成/补齐项目骨架")
    pi.add_argument("--profile", default="empty", choices=sorted(PROFILES))
    pi.add_argument("--name")
    pi.add_argument("--dest", help="目标目录（默认取 --root 或 cwd）")
    pi.add_argument("--roles", help="覆盖角色映射，如 core=engine,deliverables=shots")
    pi.add_argument("--root-files", help="额外允许的根文件（逗号分隔）")
    pi.add_argument("--dir-readmes", help="要求带 README 的目录（逗号分隔）")
    pi.add_argument("--ignore-globs", help="不入库路径（逗号分隔，覆盖默认）")
    pi.add_argument("--deliverables-mode", choices=["local", "tracked"], default=None)
    pi.add_argument("--adopt", action="store_true", help="已有项目补齐（不覆盖已有文件，CHANGELOG 留空）")
    pi.add_argument("--git-init", action="store_true")
    pi.add_argument("--force", action="store_true")
    pi.add_argument("--dry-run", action="store_true")
    pi.set_defaults(func=cmd_init)

    # task
    pt = sub.add_parser("task", help="操作 TASK.json")
    pt.add_argument("action", choices=["init", "add", "list", "show", "start", "done", "drop",
                                      "block", "unblock", "update", "check", "next"])
    pt.add_argument("id", nargs="?")
    pt.add_argument("title", nargs="?")
    pt.add_argument("--priority", default="P2", choices=PRIORITIES)
    pt.add_argument("--owner", default="")
    pt.add_argument("--reason")
    pt.add_argument("--notes")
    pt.add_argument("--tags")
    pt.add_argument("--deps")
    pt.add_argument("--scope")
    pt.add_argument("--status")
    pt.add_argument("--all", action="store_true")
    pt.add_argument("--json", action="store_true")
    pt.add_argument("--force", action="store_true")
    pt.add_argument("--project")
    pt.add_argument("--set-status", choices=STATUSES)
    pt.set_defaults(func=cmd_task)

    # changelog
    pc = sub.add_parser("changelog", help="操作 CHANGELOG.jsonl")
    pc.add_argument("action", choices=["init", "add", "check", "log"])
    pc.add_argument("--kind", choices=KINDS)
    pc.add_argument("--summary")
    pc.add_argument("--task")
    pc.add_argument("--scope")
    pc.add_argument("--files")
    pc.add_argument("--commit")
    pc.add_argument("--agent")
    pc.add_argument("--notes")
    pc.add_argument("--ts")
    pc.add_argument("--breaking", action="store_true", help="标记破坏性变更（等价 kind=breaking）")
    pc.add_argument("--since")
    pc.add_argument("--limit", type=int)
    pc.add_argument("--json", action="store_true")
    pc.add_argument("--force", action="store_true")
    pc.set_defaults(func=cmd_changelog)

    # layout
    pl = sub.add_parser("layout", help="校验项目布局/追踪文件/入库边界")
    pl.add_argument("action", choices=["check"])
    pl.add_argument("--ci", action="store_true")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_layout)
    # 子命令后也允许 --root .（不覆盖全局 --root 的值）
    for parser in (pi, pt, pc, pl):
        parser.add_argument("--root", default=argparse.SUPPRESS,
                            help="项目根（默认从 cwd 向上查找）")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.cmd == "task":
            if args.action == "add":
                if not args.title and args.id:
                    args.title = args.id
                    args.id = None
                if not args.title:
                    fail("task add 需要 title", 3)
            if args.action in ("show", "start", "done", "drop", "block", "unblock", "update") and not args.id:
                fail(f"task {args.action} 需要任务 id", 3)
        if args.cmd == "changelog":
            if args.action == "add" and (not args.kind or not args.summary):
                fail("changelog add 需要 --kind 与 --summary", 4)
        return int(args.func(args))
    except ProjectError as exc:
        sys.stderr.write(f"[dsh_project] {exc}\n")
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
