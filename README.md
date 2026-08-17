# dsh-archive-panel

**View and unarchive archived sessions in DeepSeek Harness (DSH).**

[中文](README.zh.md) | English

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

> A standard DSH **profile bundle** (the official external-plugin distribution path per [docs/user/develop/basic/publish.md](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/user/develop/basic/publish.md)): the package declares `dsh.bundle.patch`, `dsh plugin` installs it, and the profile composes its `cordis.patch.yml` layer. No build step — this package ships plain JS, so git installs need neither a `prepare` script nor an `allowBuilds` entry.

### Option A — official `dsh plugin` (recommended)

```bash
# From a directory that contains this checkout:
dsh plugin --profile web add ./dsh-archive-panel
# or straight from GitHub (plain JS, no build permission needed):
#   dsh plugin --profile web add github:realpkuasule/dsh-archive-panel

# Verify the composed layer without booting:
dsh --profile web --dump-config | grep -A2 dsh-archive-panel
```

`dsh plugin` links the package, records it in the profile's dependencies, and appends `dsh-archive-panel` to `dsh.profile.bundles`.

### Option B — manual (no CLI)

```bash
# 1. Copy the package into your profile's node_modules
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
mkdir -p "$DSH_HOME/profiles/node_modules/dsh-archive-panel"
cp -R plugins/dsh-archive-panel/. "$DSH_HOME/profiles/node_modules/dsh-archive-panel/"
# (mirror the structure of dsh-session-id-footer in the same directory)
```

```yaml
# 2. Add the bundle layer to $DSH_HOME/profiles/web/cordis.patch.yml
#    (an `- insert:` list entry — same shape as the bundle's own patch file)
    - id: dsh-archive-panel
      name: dsh-archive-panel
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
├── README.md              # English
├── README.zh.md           # 中文
├── DESIGN.md              # full design doc: research, decisions, risks, verification
├── LICENSE                # MIT
└── plugins/
    └── dsh-archive-panel/
        ├── package.json   # dsh.bundle.patch + dsh.client.platform: web declarations
        ├── cordis.patch.yml   # the bundle's patch layer (inserts the plugin row)
        └── lib/
            ├── index.js   # host half
            └── client.js  # browser half (__ModuleLoader__ bundle)
```

## License

MIT
