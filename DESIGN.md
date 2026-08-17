# 已归档会话：查看 + 取消归档（unarchive）功能设计

> 状态：需求已澄清（grilling 两轮），技术可行性已验证，**已实现**
> 实现载体：动态 Cordis 插件（Host + Client）+ **profile 插件（免审批、持久）**
> 适用版本：DSH web profile（路径以部署的 `DSH_HOME` 为准，本文示例为 `~/.dsh`）

---

## 1. 背景与问题

DSH 已具备「归档会话」能力，但**只有入口、没有出口**：

| 层 | 现状 |
| --- | --- |
| Host 服务 `ctx.workspaceRegistry` | `archiveSession(id)` 把会话 id 追加进持久化的全局集合 `archivedSessionIds`（`$DSH_HOME/storages/workspace.json`）。**没有 unarchive API**，源码注释明确预留："a future unarchive restores its position"（归档保留 `sessionIds` 槽位，取消归档即恢复原位） |
| API 层 `dsh-host-apiproxy` | 暴露 `archiveSession` RPC 与 `host/archived-sessions-changed` 事件（轮询 registry 状态变化后推送） |
| 客户端 `dsh-client-runtime` | store 持有 `archivedSessionIds`，收到事件后 `installArchived` 全量替换，UI 自动重渲染 |
| UI `dsh-client-ui-workspace` | 已归档会话从**所有**展示面消失：分组列表、扁平列表、搜索结果（`sessionVisible` 一律排除）；右键菜单只有「归档会话」，无「取消归档」。官方 README 自述："sessions can be archived, but archived sessions have no viewing or unarchive surface" |

**结论**：归档是一个"写一半"的功能——数据层和同步机制都已就绪且对称（任何走 registry 写路径的状态变更都会自动推送客户端），缺的只是：
1. Host 端的 `unarchiveSession` 写路径；
2. 客户端的"已归档"查看与操作界面。

---

## 2. 需求（grilling 澄清结果）

| # | 问题 | 决定 |
| --- | --- | --- |
| 1 | 交付物 | **设计文档 + 实现** |
| 2 | 查看入口 | 侧边栏「已归档」区域（实现为：侧边栏底部入口按钮 + 左侧滑出抽屉面板，见 §5 约束说明） |
| 3 | 取消归档语义 | **恢复原工作区原位置**（利用保留的 `sessionIds` 槽位，无需额外逻辑） |
| 4 | 操作粒度 | 单个 + 批量多选 + 「全部恢复」 |
| 5 | 列表信息 | 标题、所属工作区、最后活跃时间、cwd 路径、会话 ID（可复制）、最近消息摘要 |
| 6 | 打开行为 | 归档视图内**不可打开**对话（只浏览 + 恢复） |
| 7 | 搜索过滤 | 需要：按标题 / 摘要 / ID / cwd 即时过滤 |
| 8 | 删除操作 | 不做（非破坏性设计，只查看 + 取消归档） |

---

## 3. 已验证的技术事实（决定实现路线）

1. **registry 写路径可复用**：`WorkspaceRegistry` 的 `enqueueOperation` / `requireState` / `setState` 都是原型方法；`archiveSession` 的标准写法是
   `enqueueOperation → 检查已存在 → setState({...state, archivedSessionIds: [...]})`。
   → **在服务实例上以 `defineProperty` 添加 `unarchiveSession`**（镜像 archive 的逻辑，改为 filter 移除），即可完整复用：序列化写队列、`global.set` 持久化、`this.state` 内存更新。插件 dispose 时删除该属性，无全局污染。

2. **客户端同步自动生效**：`dsh-host-apiproxy` 轮询 `ctx.workspaceRegistry.archivedSessionIds`，变化后推送 `host/archived-sessions-changed`；客户端 store `installArchived` 全量替换并触发重渲染。→ **unarchive 成功后，官方会话列表会自动重新出现该会话，无需任何额外同步代码**。

3. **客户端 store 已含全部列表数据**（无需为列表做 RPC）：
   - `useWorkspaces`（标准 props 注入）：`archivedSessionIds`、workspaces（`sessionIds` / `title` / `path`）；
   - `useSessions`（标准 props 注入）：`byId` 含**所有**会话摘要（含已归档）——`displayTitle` / `cwd` / `updatedAt` / `origin` / `blank` / `running` / `completed`。
   - 唯一缺失项：**最近消息摘要**（需读日志），由 Host RPC 补充。

4. **Slot 选型**（全部 `replaceRisk: none`，纯加插，不影响官方 UI）：
   - `sidebar.footer.action`（list）：侧边栏底部"已归档"入口按钮，owner props `{ wide }`（rail 收起时显示图标），自带 `useSessions`/`useWorkspaces` 标准 props；
   - `shell.overlay`（list）：抽屉面板宿主（click-through 层，需自己 opt-in pointer-events）。

5. **没有可用的 unarchive 事件**：客户端事件表只有 `connection/reset` / `locale/change` / `slots/changed` / `theme/change`，已归档变化经 store 订阅自动反映（`useWorkspaces` selector 即反应式），无需轮询。

---

## 4. 架构设计

```
┌─────────────────────────── 浏览器（Client 插件）──────────────────────────┐
│                                                                           │
│  sidebar.footer.action（入口按钮）──点击──▶ shell.overlay（抽屉面板）      │
│    • useWorkspaces: archivedSessionIds/workspaces  → 徽标计数(反应式)      │
│    • useSessions: byId 摘要              → 列表主体(标题/时间/cwd/状态)    │
│    • 搜索框：标题/ID/cwd/工作区 本地即时过滤                                │
│    • 行操作：单行恢复 | 多选批量恢复 | 全部恢复                             │
│    • 会话 ID 点击复制                                                      │
│         │ host.call('archived/unarchive', {ids})                          │
│         │ host.call('archived/preview', {ids})   （打开面板时一次性拉取）  │
└────────┴──────────────────────────────────────────────────────────────────┘
         │ 包私有 JSON RPC（harness.handle / host.call）
┌────────▼────────────────────────── Node（Host 插件）──────────────────────┐
│                                                                           │
│  apply(ctx)                                                               │
│  ├─ 实例补丁：registry.unarchiveSession(id)                               │
│  │    enqueueOperation: state.archivedSessionIds 移除 id → setState       │
│  │    （与 archiveSession 同一写路径：队列/持久化/内存/事件推送全部对称）   │
│  ├─ handle('archived/unarchive', {ids})                                   │
│  │    逐 id 幂等移除，返回剩余 archivedSessionIds                          │
│  └─ handle('archived/preview', {ids})                                     │
│       sessionQuery.listEvents(id) 尾部取最近 user/assistant 文本，          │
│       截断 ≤120 字符；缺失 id 返回空（client 侧以 store 数据兜底）          │
│  ctx.effect(dispose)：删除实例补丁属性，host 无其他残留                    │
└───────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Host 端

**`unarchiveSession(sessionId)`（实例补丁）**——镜像官方 `archiveSession`：

```js
// 伪代码：在 ctx.workspaceRegistry 实例上 defineProperty
unarchiveSession(sessionId) {
  return this.enqueueOperation(async () => {
    const state = this.requireState();
    if (!state.archivedSessionIds.includes(sessionId)) return;   // 幂等
    await this.setState({
      ...state,
      archivedSessionIds: state.archivedSessionIds.filter((id) => id !== sessionId),
    });
  });
}
```

**RPC `archived/unarchive`**：入参 `{ ids: string[] }`；对每个 id 调 `unarchiveSession`（未知 id 幂等跳过）；返回 `{ archivedSessionIds }`（供 UI 即时确认）。

**RPC `archived/preview`**：入参 `{ ids: string[] }`；逐个 `sessionQuery.listEvents(id)` 取尾部事件的文本块（user/assistant 消息正文，跳过工具调用噪声），截断 120 字符；返回 `[{ id, preview }]`。批量读取可加 `Promise.all` 并发上限（如 8）。

### 4.2 Client 端

**入口（sidebar.footer.action，id 自选如 `archived-panel`）**：
- 宽侧边栏：`归档 (N)` 文字按钮；rail 收起态（`wide === false`）：归档图标 + 角标 N；
- N 直接来自 `useWorkspaces(state => state.archivedSessionIds.length)`，全反应式，零轮询；
- 点击 → 打开抽屉。

**抽屉（shell.overlay，id 自选如 `archived-panel-drawer`）**：
- 定位：`position: fixed; left: <侧边栏宽>; top/bottom: 0; width: 360px`，深色半透明遮罩或独立面板（样式用主题 token，浅/深色各查 `Theme.listTokens` 后取值）；
- 头部：标题「已归档 (N)」、关闭按钮（Esc 也可关闭）；
- 搜索框：本地过滤 `displayTitle` / id / cwd / 工作区标题；
- 列表（按 `updatedAt` 倒序，与官方列表排序一致）：
  - 标题（无标题回退 `displayTitle` 逻辑或「未命名会话」）；
  - 次要行：`工作区名 · 相对时间 · cwd`；
  - 最近消息摘要（Host preview，缺失时省略）；
  - 会话 ID：hover 显示，点击复制（`navigator.clipboard` 经 Builtin 确认后使用，否则 fallback）；
  - 行尾：恢复按钮（单个）；勾选框进入批量模式；
- 底部操作条：`全部恢复`（N>0 时可用）、批量模式下 `恢复选中 (M)`；
- 交互状态：
  - unarchive 成功 → 行即时移除（本地 state + 依赖 `useWorkspaces` 的 archivedSessionIds 自动收敛），官方列表同步出现该会话（§3.2）；
  - 失败 → 行内提示，不改本地状态；
  - 数据源优先级：store（标题/时间/cwd/工作区）→ Host preview 合并；store 中缺失的 id（极端情况）以 preview RPC 的兜底字段显示。

**不做**：打开会话（需求 #6）、删除（需求 #8）、持久化任何插件状态（动态插件进程级生命周期，无需持久化）。

---

## 5. 关键设计决策与取舍

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| unarchive 写路径 | 实例补丁 `unarchiveSession`（defineProperty） | 无公开 API；与官方 archive 共用 enqueue/setState → 持久化、事件推送、并发安全全部对称；实例级补丁可精确清理，不污染原型 |
| 列表数据源 | 客户端 store 为主 + Host preview 补充 | store 已含全部所需字段且反应式；避免为列表新建 RPC、避免重复实现排序/标题逻辑 |
| 「侧边栏已归档区域」的落点 | footer 入口 + 左侧抽屉 | `sidebar.workspaces` 是 `single` 且 `shadows-shipped-ui`（整体替换会覆盖官方搜索/分组/新建逻辑，升级即碎）；footer action + overlay 均为 `replaceRisk: none` 纯加插位，符合插件边界；抽屉贴左缘滑出，视觉上等价"侧边栏的一个区域" |
| 批量恢复 | 前端收集 ids，单次 RPC 顺序处理 | 每次写操作走 enqueue 队列，天然串行无竞态 |
| 预览获取时机 | 抽屉打开时一次性拉取 + 操作后失效重拉 | 15 个量级下开销可忽略；避免常驻轮询 |
| 搜索 | 客户端本地过滤 | 数据已在 store，零延迟 |

---

## 6. 风险与边界

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| DSH 升级后内部实现变动 | 补丁依赖的 `enqueueOperation/setState` 若改名则 unarchive 失效 | 插件属动态层，升级后重新加载即可；补丁内部对方法存在性做运行时检查，缺失则 RPC 返回明确错误而非静默失败 |
| `shell.overlay` 为 click-through 层 | 面板可能点不中 | 面板根元素显式 `pointer-events: auto`（slot 文档明示此要求） |
| 客户端 store 不含极端老会话 | 行缺失 | preview RPC 返回兜底字段（title/cwd/updatedAt），UI 合并渲染 |
| unarchive 当前会话 | 无特殊影响 | 官方列表自动恢复显示；无需处理 |
| 会话数极大（千级） | 面板渲染压力 | 虚拟化不在首版范围；当前量级（15 个）无虞，文档记录扩展点 |

---

## 7. 验证清单（实现后）

- [ ] 抽屉中看到全部已归档会话（标题/工作区/时间/cwd/ID/摘要）
- [ ] 单个恢复：行消失，官方侧边栏对应会话立即出现且回到原工作区原位
- [ ] 批量恢复 / 全部恢复行为正确
- [ ] 搜索过滤（标题/ID/cwd）即时生效
- [ ] 会话 ID 可复制
- [ ] rail 收起态入口可用；徽标计数随归档/恢复实时变化
- [ ] 重启 DSH 后：已恢复会话不再出现在归档集合（`workspace.json` 校验）；插件需重新加载（动态层固有行为，记录在案）
- [ ] 插件 stop 后：实例补丁已清理，无残留

---

## 9. 实现状态与交付（2026-08-17）

### 9.1 双轨交付

| 轨道 | 位置 | 状态 |
| --- | --- | --- |
| 动态插件 `archv-1/pkg-1` | 会话级动态插件（cordis_define/run） | ✅ 曾运行（开发期验证用；进程重启后消失） |
| **官方 bundle** `dsh-archive-panel` | `plugins/dsh-archive-panel/`（包：`package.json` 声明 `dsh.bundle.patch` + `dsh.client`，`cordis.patch.yml` 层，`lib/{index.js,client.js}`） | ✅ **当前交付形态**：`dsh plugin --profile web add` 安装（或手动复制 + patch 行），重启后自动生效 |

**Bundle 化（按官方规范 `docs/user/develop/basic/publish.md`）**：
- 包名遵循官方惯例 `dsh-<name>`（`dsh-archive-panel`，与 repo 名一致）；
- `dsh.bundle.patch: "./cordis.patch.yml"`：bundle 层在 profile 组合时按序应用（dsh-base → dsh-web-app → dsh-archive-panel → 用户层），插入插件行 `- id: dsh-archive-panel / name: dsh-archive-panel`；
- `dsh.client.platform: "web"` + `exports["./client"]`：client-modules 扫描并 serve `/plugins/dsh-archive-panel/client.js`；
- 纯 JS 无构建产物：git 安装无需 `prepare` 脚本、无需 pnpm `allowBuilds`（官方文档明确的构建脚本例外不适用）。

**profile 化与动态版的差异**（profile client 无 `harness`/`host`/`styles` builtin）：
- RPC 通道改为 HTTP：host 半 `ctx.webServer.register` 两个端点（`POST /archived/unarchive`、`POST /archived/preview`），client 半 `fetch` 调用；
- 鉴权：host 每进程生成随机 token，经 `tapIndex` 注入页面 `window.__DSH_ARCHIVED_TOKEN__`，请求带 `x-archived-token` header，不匹配返回 403（LAN 外部设备无法调用）；
- 样式：`<style>` 标签注入，由 ModuleLoader 的 claimStyles 认领（HMR 清理）；复制提示用 `ctx.timeout`（timer 服务）；
- 其余逻辑（实例补丁 unarchiveSession、store 驱动列表、事件自动同步）与动态版一致。

### 9.2 验证记录

- [x] `node --check` 语法校验 host/client 两半通过
- [x] host 半 ESM import 成功（name/inject/apply 结构正确）
- [x] client-modules 发现链模拟通过：包从 `profiles/node_modules` 可解析、`dsh.client.platform="web"`、`exports["./client"]` 定位到 bundle、bundle 注册 id 与包名一致
- [x] `cordis.patch.yml` insert 条目已添加；index.html 含 `</head>`（token 注入点有效）
- [ ] **待重启后人工验证**：抽屉展示 15 个归档会话 → 单个/批量/全部恢复 → 官方侧边栏原位重现 → 搜索/复制/徽标计数

### 9.3 使用说明

1. 安装：`dsh plugin --profile web add ./dsh-archive-panel`（或 `github:realpkuasule/dsh-archive-panel`），或手动复制 + patch 行（README Option B）；
2. 重启 `dsh web` 后生效：侧边栏底部 Settings 旁「已归档 (N)」→ 抽屉查看/恢复；
3. 恢复的会话立即出现在官方侧边栏原工作区原位（`host/archived-sessions-changed` 自动推送）。

---

## 10. 备选方案（已评估，未选）

1. **整体替换 `sidebar.workspaces`**：把"已归档"做进官方浏览区。放弃——需要重写官方搜索/分组/新建/对话框逻辑，`shadows-shipped-ui`，升级即失效。
2. **Host 直接写 storage domain**（`storageDomain.open(workspaceDomainSpec).global.set`）：持久化可行，但绕过 registry 内存快照 → 运行期官方 UI 仍按旧集合过滤，需重启才一致。放弃——实例补丁走官方队列，无此问题。
3. **设置页（`settings.section`）做管理页**：可行且零风险，但与"侧边栏区域"的直觉不符，且设置页不适合高频操作。留作后续增强。
4. **官方改版**（修改 `dsh-workspace` / `dsh-host-apiproxy` / `dsh-client-ui-workspace` 并重建 Web 产物）：最彻底，但属部署级修改、升级被覆盖、工作量大。**建议作为上游贡献方向**（README 已预留 unarchive 语义，实现成本低）。
