import { t as tr } from "../lib/i18n";
import { useStore } from "../lib/store";
import { cx } from "../lib/format";
import { Icon } from "./Icons";
import { UsageBadge } from "./UsageBadge";
import { unrealBadge } from "./UnrealSections";
import type { SettingsTab } from "./SettingsDialog";

export function blenderBadge(b: { mcp: string; blender: string }): { cls: string; label: string } {
  if (b.mcp === "error") return { cls: "bad", label: tr("MCPエラー") };
  if (b.mcp === "stopped") return { cls: "off", label: tr("MCP停止") };
  if (b.mcp === "starting") return { cls: "wait", label: tr("MCP起動中") };
  if (b.blender === "connected") return { cls: "ok", label: tr("Blender接続中") };
  if (b.blender === "disconnected") return { cls: "bad", label: tr("Blender未接続") };
  if (b.blender === "unresponsive") return { cls: "wait", label: tr("Blender応答なし") };
  if (b.blender === "port_conflict") return { cls: "bad", label: tr("ポート競合") };
  return { cls: "wait", label: tr("確認中") };
}

export function TopBar(props: {
  sidebarOpen: boolean;
  panelOpen: boolean;
  onToggleSidebar: () => void;
  onTogglePanel: () => void;
  onSettings: (tab: SettingsTab) => void;
}) {
  const { state } = useStore();
  const status = state.providers.find((p) => p.id === state.draftProvider);
  const project = state.projects.find((p) => p.id === state.activeProjectId);
  const badge = blenderBadge(state.blender);

  return (
    <header className="topbar">
      <div className="row gap-sm">
        {!props.sidebarOpen && (
          <button className="icon-btn" title={tr("サイドバーを開く")} onClick={props.onToggleSidebar}>
            <Icon name="sidebar" />
          </button>
        )}
        <span className="chat-title ellipsis">{state.detail?.conversation.title || (state.draftMode === "design_prompt" ? tr("設定画生成") : state.draftMode === "prompt" ? tr("モデリングプロンプト") : tr("新しいチャット"))}</span>
        {status && !status.auth_ok && !state.providersLoading && (
          <button className="pill bad" onClick={() => props.onSettings("ai")} title={tr(status.detail)}>
            <Icon name="alert" size={12} /> {tr("未ログイン")}</button>
        )}
      </div>
      <div className="row gap-sm topbar-right">
        <UsageBadge />
        <button className="project-name ellipsis" title={tr("プロジェクト設定")} onClick={() => props.onSettings("projects")}>
          <Icon name="folder" size={14} /> {project?.name}
        </button>
        <button
          className={cx("pill", badge.cls)}
          title={state.blender.error || state.blender.endpoint}
          onClick={() => (props.panelOpen ? props.onSettings("mcp") : props.onTogglePanel())}
        >
          <span className="dot" /> {badge.label}
        </button>
        {state.unreal.status !== "disabled" && (
          <button
            className={cx("pill", unrealBadge(state.unreal).cls)}
            title={state.unreal.editor?.level || state.unreal.url}
            onClick={() => (props.panelOpen ? props.onSettings("unreal") : props.onTogglePanel())}
          >
            <span className="dot" /> {unrealBadge(state.unreal).label}
          </button>
        )}
        {(state.settings.extra_mcp?.servers ?? []).filter((s) => s.enabled !== false && state.extraMcp[s.name]).map((s) => (
          <button
            key={s.name}
            className={cx("pill", state.extraMcp[s.name] === "connected" ? "ok" : "off")}
            title={`MCP: ${s.name} — ${state.extraMcp[s.name] === "connected" ? tr("接続中") : tr("未接続")}`}
            onClick={() => props.onSettings("extra")}
          >
            <span className="dot" /> {s.label || s.name}
          </button>
        ))}
        <button className="icon-btn" title={props.panelOpen ? tr("右パネルを閉じる") : tr("右パネルを開く")} onClick={props.onTogglePanel}>
          <Icon name="panel" />
        </button>
      </div>
    </header>
  );
}
