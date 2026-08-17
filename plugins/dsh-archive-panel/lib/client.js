// dsh-archive-panel — browser half.
// Bundle format mirrors the shipped dsh-client-ui-* packages:
// window.__ModuleLoader__.load({ id, factory }) with the package name as id.
// - inject: ["slots", "timer"]
// - reads the per-process token injected by the host half
//   (window.__DSH_ARCHIVED_TOKEN__) and calls the host HTTP endpoints
//   POST /archived/unarchive and POST /archived/preview.
// - style injection: the factory-owned <style> tag is claimed by the module
//   system (claimStyles) for HMR cleanup.
window.__ModuleLoader__.load({
	id: "dsh-archive-panel",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		let react = require("react");

		const inject = ["slots", "timer"];

		// ---- host RPC over the guarded HTTP endpoints ----
		function callHost(endpoint, payload) {
			const token = typeof window !== "undefined" && window.__DSH_ARCHIVED_TOKEN__
				? window.__DSH_ARCHIVED_TOKEN__
				: "";
			return fetch(endpoint, {
				method: "POST",
				headers: {
					"content-type": "application/json",
					"x-archived-token": token
				},
				body: JSON.stringify(payload || {})
			}).then((res) => {
				if (!res.ok) throw new Error("HTTP " + res.status);
				return res.json();
			}).then((data) => {
				if (!data || data.ok !== true) throw new Error((data && data.error) || "request failed");
				return data;
			});
		}

		// ---- package styles ----
		const style = document.createElement("style");
		style.textContent = `
			.archv-entry {
				display: flex; align-items: center; gap: 6px;
				padding: 6px 10px; border: none; border-radius: 6px;
				background: transparent; color: var(--dsw-alias-label-secondary, #999);
				cursor: pointer; font: inherit; white-space: nowrap;
			}
			.archv-entry:hover, .archv-entry.is-open {
				background: var(--dsw-alias-bg-layer-2, rgba(128,128,128,.15));
				color: var(--dsw-alias-label-primary, #eee);
			}
			.archv-entry-badge {
				min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px;
				background: var(--dsw-alias-brand-primary, #4a7dff);
				color: #fff; font-size: 10px; line-height: 16px; text-align: center;
			}
			.archv-root { position: fixed; inset: 0; z-index: 1000; }
			.archv-backdrop { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.35); }
			.archv-panel {
				position: absolute; top: 0; left: 0; bottom: 0; width: 380px; max-width: 92vw;
				display: flex; flex-direction: column;
				background: var(--dsw-alias-bg-overlay, #202024);
				border-right: 1px solid var(--dsw-alias-border-l1, #333);
				color: var(--dsw-alias-label-primary, #eee);
				font-size: 13px; box-shadow: 4px 0 24px rgba(0,0,0,.3);
			}
			.archv-header {
				display: flex; align-items: center; justify-content: space-between;
				padding: 14px 16px; border-bottom: 1px solid var(--dsw-alias-border-l1, #333);
			}
			.archv-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
			.archv-count {
				min-width: 20px; padding: 1px 7px; border-radius: 10px; text-align: center;
				background: var(--dsw-alias-bg-layer-2, rgba(128,128,128,.2));
				color: var(--dsw-alias-label-secondary, #999); font-size: 12px; font-weight: 500;
			}
			.archv-close {
				border: none; background: transparent; color: var(--dsw-alias-label-secondary, #999);
				font-size: 20px; line-height: 1; cursor: pointer; padding: 2px 6px; border-radius: 4px;
			}
			.archv-close:hover { color: var(--dsw-alias-label-primary, #eee); background: var(--dsw-alias-bg-layer-2, rgba(128,128,128,.15)); }
			.archv-search {
				margin: 12px 16px 4px; padding: 7px 10px; border-radius: 6px;
				border: 1px solid var(--dsw-alias-border-l1, #333);
				background: var(--dsw-alias-bg-layer-1, #26262b);
				color: var(--dsw-alias-label-primary, #eee); font: inherit; outline: none;
			}
			.archv-search:focus { border-color: var(--dsw-alias-brand-primary, #4a7dff); }
			.archv-notice { margin: 8px 16px 0; padding: 6px 10px; border-radius: 6px; font-size: 12px;
				background: color-mix(in srgb, var(--dsw-alias-state-error-primary, #e5484d) 15%, transparent);
				color: var(--dsw-alias-state-error-primary, #ff6b70); }
			.archv-list { flex: 1; overflow-y: auto; padding: 8px 8px 12px; }
			.archv-empty { padding: 28px 16px; text-align: center; color: var(--dsw-alias-label-secondary, #888); }
			.archv-row {
				display: flex; align-items: flex-start; gap: 8px;
				padding: 9px 10px; border-radius: 8px; margin-bottom: 2px;
			}
			.archv-row:hover, .archv-row.is-selected { background: var(--dsw-alias-bg-layer-2, rgba(128,128,128,.12)); }
			.archv-check { padding-top: 2px; }
			.archv-check input { accent-color: var(--dsw-alias-brand-primary, #4a7dff); cursor: pointer; }
			.archv-row-main { flex: 1; min-width: 0; }
			.archv-row-title { font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
			.archv-row-sub {
				margin-top: 3px; font-size: 12px; color: var(--dsw-alias-label-secondary, #999);
				display: flex; gap: 6px; overflow: hidden; white-space: nowrap;
			}
			.archv-row-cwd { overflow: hidden; text-overflow: ellipsis; max-width: 160px; }
			.archv-row-preview {
				margin-top: 4px; font-size: 12px; color: var(--dsw-alias-label-secondary, #aaa);
				display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
			}
			.archv-row-id {
				margin-top: 4px; font-size: 11px; font-family: ui-monospace, monospace;
				color: var(--dsw-alias-label-secondary, #888); cursor: pointer; user-select: none;
			}
			.archv-row-id:hover { color: var(--dsw-alias-label-primary, #ddd); }
			.archv-copied { margin-left: 6px; color: var(--dsw-alias-state-success-primary, #46a758); }
			.archv-btn {
				border: 1px solid var(--dsw-alias-border-l2, #444); border-radius: 6px;
				background: transparent; color: var(--dsw-alias-label-primary, #eee);
				padding: 5px 10px; font: inherit; font-size: 12px; cursor: pointer;
			}
			.archv-btn:hover:not(:disabled) { background: var(--dsw-alias-bg-layer-2, rgba(128,128,128,.15)); }
			.archv-btn:disabled { opacity: .5; cursor: default; }
			.archv-btn-primary {
				background: var(--dsw-alias-brand-primary, #4a7dff); border-color: transparent; color: #fff;
			}
			.archv-btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
			.archv-btn-restore { flex-shrink: 0; margin-top: 2px; }
			.archv-footer {
				display: flex; gap: 8px; justify-content: flex-end;
				padding: 12px 16px; border-top: 1px solid var(--dsw-alias-border-l1, #333);
			}
			/* Sidebar footer layout fix: the shipped Cordis occupant claims the
			   whole footer-actions row, so give this entry its own wrapped line.
			   The selector matches the semantic class suffix so it survives
			   CSS-module hash changes across DSH updates. */
			[class*="footerActions"] { flex-wrap: wrap; }
			.archv-entry { flex: 0 0 100%; width: 100%; }
			[class*="collapsed"] .archv-entry { flex: 0 0 auto; width: auto; }
		`;
		document.head.appendChild(style);

		function apply(ctx) {
			const slots = ctx.get("slots");
			if (slots === undefined) return;

			// ---- shared panel open state between the footer entry and the drawer ----
			const panelState = { open: false, listeners: new Set() };
			const setOpen = (open) => {
				if (panelState.open === open) return;
				panelState.open = open;
				for (const listener of panelState.listeners) listener(open);
			};
			const subscribe = (listener) => {
				panelState.listeners.add(listener);
				return () => panelState.listeners.delete(listener);
			};
			function usePanelOpen() {
				const [open, setOpenState] = react.useState(panelState.open);
				react.useEffect(() => subscribe(setOpenState), []);
				return [open, setOpen];
			}

			// ---- shared helpers ----
			const ARCHIVE_ICON = react.createElement("svg", { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.4, "aria-hidden": true },
				react.createElement("rect", { x: 1.5, y: 2.5, width: 13, height: 3, rx: 1 }),
				react.createElement("path", { d: "M2.5 5.5v7a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-7" }),
				react.createElement("path", { d: "M6.5 8.5h3" }),
			);
			function relativeTime(ts) {
				if (!ts || typeof ts !== "number") return "";
				const diff = Date.now() - ts;
				const m = 60 * 1000;
				const h = 60 * m;
				const d = 24 * h;
				if (diff < m) return "刚刚";
				if (diff < h) return Math.floor(diff / m) + " 分钟前";
				if (diff < d) return Math.floor(diff / h) + " 小时前";
				if (diff < 30 * d) return Math.floor(diff / d) + " 天前";
				return new Date(ts).toLocaleDateString();
			}
			function copyText(text) {
				if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
					navigator.clipboard.writeText(text).catch(() => {});
				}
			}

			// ---- entry button in the sidebar footer ----
			// The footer actions row is a single-line flex container whose shipped
			// Cordis occupant claims the full row width, so a second occupant would
			// overflow and get clipped by the sidebar column. CSS below wraps the
			// row and gives this entry its own full-width line instead.
			slots.inject("sidebar.footer.action", () => slots.register(
				{ name: "sidebar.footer.action", id: "dsh-archive-panel", order: 10 },
				(props) => {
					const [open, setOpenLocal] = usePanelOpen();
					const count = props.useWorkspaces((state) => state.archivedSessionIds.length);
					return react.createElement(
						"button",
						{
							className: "archv-entry" + (open ? " is-open" : ""),
							title: "已归档会话：查看与恢复",
							onClick: () => setOpenLocal(!open),
						},
						ARCHIVE_ICON,
						props.wide
							? react.createElement("span", { className: "archv-entry-label" }, "已归档" + (count > 0 ? " (" + count + ")" : ""))
							: count > 0
								? react.createElement("span", { className: "archv-entry-badge" }, count > 99 ? "99+" : String(count))
								: null,
					);
				},
			));

			// ---- drawer panel in the frame-wide overlay layer ----
			slots.inject("shell.overlay", () => slots.register(
				{ name: "shell.overlay", id: "archived-panel-drawer", order: 10 },
				(props) => react.createElement(Drawer, { useWorkspaces: props.useWorkspaces, useSessions: props.useSessions }),
			));

			function Drawer({ useWorkspaces, useSessions }) {
				const [open, setOpenLocal] = usePanelOpen();
				const archivedIds = useWorkspaces((state) => state.archivedSessionIds);
				const workspaces = useWorkspaces((state) => state.items);
				const list = useSessions((state) => state);
				const [query, setQuery] = react.useState("");
				const [previews, setPreviews] = react.useState({});
				const [selected, setSelected] = react.useState({});
				const [busyIds, setBusyIds] = react.useState({});
				const [notice, setNotice] = react.useState("");
				const [copiedId, setCopiedId] = react.useState("");

				const idsKey = (archivedIds || []).join(",");
				react.useEffect(() => {
					if (!open) return;
					let cancelled = false;
					callHost("/archived/preview", { ids: archivedIds || [] })
						.then((result) => {
							if (cancelled) return;
							const map = {};
							if (result && Array.isArray(result.items)) {
								for (const item of result.items) map[item.id] = item;
							}
							setPreviews(map);
						})
						.catch(() => {});
					return () => { cancelled = true; };
					// eslint-disable-next-line react-hooks/exhaustive-deps
				}, [open, idsKey]);

				if (!open) return null;

				const wsBySession = new Map();
				for (const ws of workspaces || []) {
					for (const sid of ws.sessionIds || []) wsBySession.set(sid, ws);
				}
				const q = query.trim().toLowerCase();
				const rows = (archivedIds || [])
					.map((id) => {
						const summary = list && list.byId ? list.byId[id] : undefined;
						const ws = wsBySession.get(id);
						const title = summary && summary.displayTitle ? summary.displayTitle : "未命名会话";
						const cwd = (summary && summary.cwd) || (ws && ws.path) || "";
						const wsLabel = ws
							? (ws.title || String(ws.path || "").split("/").filter(Boolean).pop() || "未分组")
							: "未分组";
						const preview = previews[id] ? previews[id].preview : "";
						const updatedAt = (summary && summary.updatedAt) || (previews[id] && previews[id].updatedAt) || 0;
						return { id, title, cwd, wsLabel, preview, updatedAt, origin: summary && summary.origin };
					})
					.filter((row) => row.origin !== "subagent")
					.filter((row) => {
						if (!q) return true;
						return row.title.toLowerCase().includes(q)
							|| row.id.toLowerCase().includes(q)
							|| row.cwd.toLowerCase().includes(q)
							|| row.wsLabel.toLowerCase().includes(q)
							|| row.preview.toLowerCase().includes(q);
					})
					.sort((a, b) => b.updatedAt - a.updatedAt);

				const selectedIds = Object.keys(selected);
				const restore = (ids) => {
					if (ids.length === 0) return;
					setNotice("");
					setBusyIds((prev) => {
						const next = { ...prev };
						for (const id of ids) next[id] = true;
						return next;
					});
					callHost("/archived/unarchive", { ids })
						.then(() => {
							// The shipped client runtime receives host/archived-sessions-changed
							// and refreshes archivedSessionIds, so rows disappear automatically.
							setSelected({});
							setBusyIds({});
						})
						.catch((error) => {
							setBusyIds({});
							setNotice("恢复失败：" + String((error && error.message) || error));
						});
				};
				const toggleSelect = (id) => {
					setSelected((prev) => {
						const next = { ...prev };
						if (next[id]) delete next[id];
						else next[id] = true;
						return next;
					});
				};
				const handleCopy = (row) => {
					copyText(row.id);
					setCopiedId(row.id);
					ctx.timeout(() => setCopiedId(""), 1200);
				};

				return react.createElement("div", { className: "archv-root" },
					react.createElement("div", { className: "archv-backdrop", onClick: () => setOpenLocal(false) }),
					react.createElement("div", { className: "archv-panel" },
						react.createElement("div", { className: "archv-header" },
							react.createElement("div", { className: "archv-title" },
								"已归档会话",
								react.createElement("span", { className: "archv-count" }, archivedIds ? String(archivedIds.length) : "0"),
							),
							react.createElement("button", { className: "archv-close", title: "关闭", onClick: () => setOpenLocal(false) }, "×"),
						),
						react.createElement("input", {
							className: "archv-search",
							placeholder: "搜索标题 / 会话 ID / 路径…",
							value: query,
							onChange: (e) => setQuery(e.target.value),
						}),
						notice ? react.createElement("div", { className: "archv-notice" }, notice) : null,
						react.createElement("div", { className: "archv-list" },
							rows.length === 0
								? react.createElement("div", { className: "archv-empty" }, "没有匹配的已归档会话")
								: rows.map((row) => react.createElement(Row, {
									key: row.id,
									row,
									selected: !!selected[row.id],
									busy: !!busyIds[row.id],
									copied: copiedId === row.id,
									onToggle: () => toggleSelect(row.id),
									onRestore: () => restore([row.id]),
									onCopy: () => handleCopy(row),
								})),
						),
						react.createElement("div", { className: "archv-footer" },
							selectedIds.length > 0
								? react.createElement(react.Fragment, null,
									react.createElement("button", { className: "archv-btn archv-btn-primary", onClick: () => restore(selectedIds) }, "恢复选中 (" + selectedIds.length + ")"),
									react.createElement("button", { className: "archv-btn", onClick: () => setSelected({}) }, "取消选择"),
								)
								: react.createElement("button", {
									className: "archv-btn archv-btn-primary",
									disabled: rows.length === 0,
									onClick: () => restore(rows.map((r) => r.id)),
								}, "全部恢复"),
						),
					),
				);
			}

			function Row({ row, selected, busy, copied, onToggle, onRestore, onCopy }) {
				return react.createElement("div", { className: "archv-row" + (selected ? " is-selected" : "") },
					react.createElement("label", { className: "archv-check" },
						react.createElement("input", { type: "checkbox", checked: selected, onChange: onToggle }),
					),
					react.createElement("div", { className: "archv-row-main" },
						react.createElement("div", { className: "archv-row-title" }, row.title),
						react.createElement("div", { className: "archv-row-sub" },
							react.createElement("span", null, row.wsLabel),
							row.updatedAt ? react.createElement("span", null, "· " + relativeTime(row.updatedAt)) : null,
							row.cwd ? react.createElement("span", { className: "archv-row-cwd", title: row.cwd }, "· " + row.cwd) : null,
						),
						row.preview ? react.createElement("div", { className: "archv-row-preview" }, row.preview) : null,
						react.createElement("div", { className: "archv-row-id", onClick: onCopy, title: "点击复制会话 ID" },
							row.id.slice(0, 24) + "…",
							copied ? react.createElement("span", { className: "archv-copied" }, "已复制") : null,
						),
					),
					react.createElement("button", {
						className: "archv-btn archv-btn-restore",
						disabled: busy,
						onClick: onRestore,
					}, busy ? "恢复中…" : "恢复"),
				);
			}
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
