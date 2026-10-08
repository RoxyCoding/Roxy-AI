import { useStore } from "../lib/store";
import { cx } from "../lib/format";
import { Icon } from "./Icons";
import type { SettingsTab } from "./SettingsDialog";

export function blenderBadge(b: { mcp: string; blender: string }): { cls: string; label: string } {
  if (b.mcp === "error") return { cls: "bad", label: "MCPエラー" };
  if (b.mcp === "stopped") return { cls: "off", label: "MCP停止" };
  if (b.mcp === "starting") return { cls: "wait", label: "MCP起動中" };
  if (b.blender === "connected") return { cls: "ok", label: "Blender接続中" };
  if (b.blender === "disconnected") return { cls: "bad", label: "Blender未接続" };
  if (b.blender === "unresponsive") return { cls: "wait", label: "Blender応答なし" };
  return { cls: "wait", label: "確認中" };
}

export function TopBar(props: {
  sidebarOpen: boolean;
  panelOpen: boolean;
  onToggleSidebar: () => void;
  onTogglePanel: () => void;
  onSettings: (tab: SettingsTab) => void;
}) {
  const { state, actions } = useStore();
  const provider = state.draftProvider;
  const status = state.providers.find((p) => p.id === provider);
  const models = status?.models ?? [];
  const project = state.projects.find((p) => p.id === state.activeProjectId);
  const badge = blenderBadge(state.blender);
  const running = state.currentId ? Boolean(state.runs[state.currentId]) : false;
  const enabled = state.boot.providers.filter((p) => state.settings.providers[p.id]?.enabled !== false);

  const switchProvider = (id: string) => {
    if (id === provider) return;
    void actions.setDraft(id, state.settings.providers[id]?.default_model ?? "");
  };

  return (
    <header className="topbar">
      <div className="row gap-sm">
        {!props.sidebarOpen && (
          <button className="icon-btn" title="サイドバーを開く" onClick={props.onToggleSidebar}>
            <Icon name="sidebar" />
          </button>
        )}
        <div className="segmented" role="tablist" aria-label="AIの切り替え">
          {enabled.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={p.id === provider}
              className={cx(p.id === provider && "on")}
              disabled={running}
              title={running ? "応答生成中は切り替えできません" : `${p.name} に切り替え`}
              onClick={() => switchProvider(p.id)}
            >
              {p.name}
            </button>
          ))}
        </div>
        <select
          className="select"
          value={state.draftModel}
          disabled={running}
          title="モデル"
          onChange={(e) => void actions.setDraft(provider, e.target.value)}
        >
          {provider === "codex" && <option value="">既定のモデル</option>}
          {models.map((m) => (
            <option key={m.id} value={m.id} title={m.description}>
              {m.name}
              {m.supports_images ? "" : "（画像非対応）"}
            </option>
          ))}
          {state.draftModel && !models.some((m) => m.id === state.draftModel) && (
            <option value={state.draftModel}>{state.draftModel}</option>
          )}
        </select>
        {status && !status.auth_ok && !state.providersLoading && (
          <button className="pill bad" onClick={() => props.onSettings("ai")} title={status.detail}>
            <Icon name="alert" size={12} /> 未ログイン
          </button>
        )}
      </div>
      <div className="row gap-sm topbar-right">
        <button className="project-name ellipsis" title="プロジェクト設定" onClick={() => props.onSettings("projects")}>
          <Icon name="folder" size={14} /> {project?.name}
        </button>
        <button
          className={cx("pill", badge.cls)}
          title={state.blender.error || state.blender.endpoint}
          onClick={() => (props.panelOpen ? props.onSettings("mcp") : props.onTogglePanel())}
        >
          <span className="dot" /> {badge.label}
        </button>
        <button className="icon-btn" title={props.panelOpen ? "右パネルを閉じる" : "右パネルを開く"} onClick={props.onTogglePanel}>
          <Icon name="panel" />
        </button>
      </div>
    </header>
  );
}
