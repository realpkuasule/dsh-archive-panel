# dsh-archive-panel

**查看与取消归档 DeepSeek Harness（DSH）的已归档会话。**

[English](README.md) | 中文

## 为什么需要它

DSH 自带**归档**功能，却没有**取消归档**：`workspaceRegistry.archiveSession(id)` 会把会话从所有界面隐藏，官方 README 也承认 *"sessions can be archived, but archived sessions have no viewing or unarchive surface."*（会话可以归档，但已归档会话没有任何查看或取消归档的界面）。官方设计甚至预留了语义——*"a future unarchive restores its position"*（未来的取消归档将恢复其位置）——但从未实现。

本插件补全了这条闭环：

- **Host 端**：在 workspace registry 实例上补丁 `unarchiveSession`，完全镜像官方 `archiveSession` 的写路径（`enqueueOperation → requireState → setState`），因此持久化、内存状态与 `host/archived-sessions-changed` 事件推送全部保持一致——**恢复的会话会立即出现在官方侧边栏的原工作区原位**。
- **浏览器端**：侧边栏底部入口（带实时计数徽标），点击滑出左侧抽屉，列出所有已归档会话，支持单个 / 批量 / 全部恢复，以及搜索和点击复制会话 ID。

## 功能特性

- 📂 列出全部已归档会话：标题、工作区、最后活跃时间、`cwd`、会话 ID、最近消息摘要
- ♻️ 恢复单个、勾选批量恢复、或一键全部恢复
- 🔍 客户端即时搜索（标题 / ID / 路径 / 工作区 / 摘要）
- 🔗 点击复制会话 ID
- 🔢 侧边栏计数徽标实时更新（归档/恢复时自动变化）
- 🔒 Host 端点由注入到页面的每进程随机 token 保护

## 安装

> 标准 DSH **profile bundle**（官方外部插件分发路径，见 [docs/user/develop/basic/publish.md](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/user/develop/basic/publish.md)）：包声明 `dsh.bundle.patch`，由 `dsh plugin` 安装，profile 组合其 `cordis.patch.yml` 层。**无需构建步骤**——本包是纯 JS，git 安装既不需要 `prepare` 脚本，也不需要 `allowBuilds` 授权。

### 方式 A — 官方 `dsh plugin`（推荐）

```bash
# 在包含本项目 checkout 的目录下执行：
dsh plugin --profile web add ./dsh-archive-panel
# 或直接从 GitHub 安装（纯 JS，无需构建授权）：
#   dsh plugin --profile web add github:realpkuasule/dsh-archive-panel

# 不启动服务，验证组合后的配置层：
dsh --profile web --dump-config | grep -A2 dsh-archive-panel
```

`dsh plugin` 会链接该包、写入 profile 的依赖，并把 `dsh-archive-panel` 追加到 `dsh.profile.bundles` 列表。

### 方式 B — 手动安装（无需 CLI）

```bash
# 1. 把包复制到 profile 的 node_modules
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
mkdir -p "$DSH_HOME/profiles/node_modules/dsh-archive-panel"
cp -R plugins/dsh-archive-panel/. "$DSH_HOME/profiles/node_modules/dsh-archive-panel/"
# （与同目录下的 dsh-session-id-footer 结构一致）
```

```yaml
# 2. 在 $DSH_HOME/profiles/web/cordis.patch.yml 中加入 bundle 层
#    （一个 `- insert:` 列表项 —— 与包自带的 patch 文件格式相同）
    - id: dsh-archive-panel
      name: dsh-archive-panel
```

```bash
# 3. 重启 `dsh web`
```

打开 GUI：侧边栏底部 Settings 旁会出现 **已归档 (N)**，点击即可打开抽屉。

## 架构

```
浏览器（client.js，__ModuleLoader__ bundle）
 ├─ sidebar.footer.action 入口 ──▶ shell.overlay 抽屉
 │    • useWorkspaces/useSessions store hooks → 反应式列表 + 计数
 │    • fetch → POST /archived/unarchive | /archived/preview
 └─ x-archived-token header（由 host 注入 window.__DSH_ARCHIVED_TOKEN__）
Node（index.js，profile Cordis 插件）
 ├─ 实例补丁：registry.unarchiveSession(id)
 │    与官方 archiveSession 相同的 enqueue/requireState/setState 路径
 ├─ POST /archived/unarchive → 逐 id 幂等移除
 ├─ POST /archived/preview   → sessionQuery.filterEvents 尾部 → 120 字符摘要
 └─ apiProxy 轮询推送 host/archived-sessions-changed → 官方 UI 自动刷新
```

- 列表数据直接来自客户端 store（`useSessions`/`useWorkspaces`），其中已包含已归档会话——只有消息摘要需要 Host 读取。
- 取消归档走 registry 自己的串行写队列，官方侧边栏零额外同步代码即可更新。
- token 防护（每进程随机、经 `tapIndex` 注入）保证 HTTP 端点只能被本进程服务的页面调用。

## 仓库结构

```
dsh-archive-panel/
├── README.md              # 英文
├── README.zh.md           # 中文
├── DESIGN.md              # 完整设计文档：调研、决策、风险、验证
├── LICENSE                # MIT
└── plugins/
    └── dsh-archive-panel/
        ├── package.json   # dsh.bundle.patch + dsh.client.platform: web 声明
        ├── cordis.patch.yml   # bundle 的 patch 层（插入插件行）
        └── lib/
            ├── index.js   # Host 半
            └── client.js  # 浏览器半（__ModuleLoader__ bundle）
```

## 许可证

MIT
