# dsh-archive-panel

**View and unarchive archived sessions in DeepSeek Harness (DSH).**

DeepSeek Harness 侧边栏「已归档」面板：查看全部已归档会话（标题 / 工作区 / 最后活跃时间 / cwd / 会话 ID / 最近消息摘要），支持单个、批量与全部取消归档（unarchive）。

## Why

DSH ships **archiving** but no **unarchiving**: `workspaceRegistry.archiveSession(id)` hides a session from every UI surface, while the official README admits *"sessions can be archived, but archived sessions have no viewing or unarchive surface."* The host design even reserved the semantics — *"a future unarchive restores its position"* — but never implemented it.

This plugin completes the loop:

- **Host**: an `unarchiveSession` instance patch on the workspace registry that mirrors the shipped `archiveSession` write path (`enqueueOperation → requireState → setState`), so persistence, in-memory state, and the `host/archived-sessions-changed` push all stay consistent — **a restored session reappears in the official sidebar at its original workspace position immediately**.
- **Browser**: a sidebar-footer entry (with a live count badge) opening a left drawer that lists every archived session and offers single / batch / restore-all actions, plus search and click-to-copy session IDs.

## Features

- 📂 List all archived sessions: title, workspace, last activity, `cwd`, session ID, recent-message preview
- ♻️ Restore one, a selected batch, or all archived sessions
- 🔍 Instant client-side search over title / ID / path / workspace / preview
- 🔗 Click session ID to copy
- 🔢 Reactive count badge in the sidebar (updates as sessions are archived/restored)
- 🔒 Host endpoints guarded by a per-process random token injected into the index page

## Install

> A **profile plugin** (a local package in the DSH web profile) — no build step, no dynamic-plugin approval, survives restarts.

```bash
# 1. Copy the package into your profile's node_modules
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
mkdir -p "$DSH_HOME/profiles/node_modules/archived-panel"
cp -R plugins/archived-panel/. "$DSH_HOME/profiles/node_modules/archived-panel/"
# (mirror the structure of dsh-session-id-footer in the same directory)
```

```yaml
# 2. Register it in $DSH_HOME/profiles/web/cordis.patch.yml (add to an `- insert:` list)
    - id: archived-panel
      name: 'archived-panel'
```

```bash
# 3. Restart `dsh web`
```

Open the GUI: the sidebar footer now shows **已归档 (N)** beside Settings. Click it to open the drawer.

## Architecture

```
Browser (client.js, __ModuleLoader__ bundle)
 ├─ sidebar.footer.action entry ──▶ shell.overlay drawer
 │    • useWorkspaces/useSessions store hooks → reactive list + count
 │    • fetch → POST /archived/unarchive | /archived/preview
 └─ x-archived-token header (window.__DSH_ARCHIVED_TOKEN__ injected by host)
Node (index.js, profile Cordis plugin)
 ├─ instance patch: registry.unarchiveSession(id)
 │    same enqueue/requireState/setState path as shipped archiveSession
 ├─ POST /archived/unarchive → per-id idempotent removal
 ├─ POST /archived/preview   → sessionQuery.filterEvents tail → 120-char preview
 └─ apiProxy poller pushes host/archived-sessions-changed → official UI refreshes
```

- Listing data comes from the client stores (`useSessions`/`useWorkspaces`), which already contain archived sessions — only the message preview needs a host read.
- Unarchive goes through the registry's own serialized write queue, so the official sidebar updates with zero extra sync code.
- The token guard (random per process, injected via `tapIndex`) keeps the HTTP endpoints callable only from pages served by this process.

## Repository layout

```
dsh-archive-panel/
├── README.md
├── DESIGN.md              # full design doc: research, decisions, risks, verification
├── LICENSE                # MIT
└── plugins/
    └── archived-panel/
        ├── package.json   # dsh.client.platform: web declaration
        └── lib/
            ├── index.js   # host half
            └── client.js  # browser half
```

## License

MIT
