import { t as tr } from "../lib/i18n";
import { useState } from "react";
import { fileUrl, post } from "../lib/backend";
import { useStore } from "../lib/store";
import { clockTime, cx, relativeTime, seconds } from "../lib/format";
import { Icon } from "./Icons";
import { Section } from "./PanelSection";
import { SendToUnreal, UnrealSections, unrealBadge } from "./UnrealSections";
import { useLightbox } from "./Lightbox";
import { McpRestartButton } from "./McpRestartButton";
import { ToolCallRow } from "./ToolCalls";
import { blenderBadge } from "./TopBar";
import type { SettingsTab } from "./SettingsDialog";
import { useTick } from "./ChatView";

export function RightPanel({ onClose, onSettings, exiting = false }: { onClose: () => void; onSettings: (tab: SettingsTab) => void; exiting?: boolean }) {
  const { state, actions } = useStore();
  const b = state.blender;
  const [engine, setEngineState] = useState<"blender" | "unreal">(() => {
    try {
      return localStorage.getItem("roxy.rpanel.engine") === "unreal" ? "unreal" : "blender";
    } catch {
      return "blender";
    }
  });
  const setEngine = (v: "blender" | "unreal") => {
    setEngineState(v);
    try {
      localStorage.setItem("roxy.rpanel.engine", v);
    } catch {
      /* per-viewer convenience only */
    }
  };
  const badge = blenderBadge(b);
  const lightbox = useLightbox();
  const [busy, setBusy] = useState<"" | "preview" | "info" | "reconnect">("");
  const run = state.currentId ? state.runs[state.currentId] : undefined;
  useTick(Boolean(run));
  const otherRuns = Object.values(state.runs).filter((r) => r.conversation_id !== state.currentId);
  const selected = new Set(b.scene?.selected ?? []);
  const selectedRows = b.scene?.objects
    ? b.scene.objects.filter((o) => selected.has(o.name)).map((o) => ({
        name: o.name,
        text: [o.name, o.type, ...(b.scene?.columns ?? []).filter((c) => c in o.fields).map((c) => o.fields[c])].join(" | "),
      }))
    : (b.scene?.rows ?? []).filter((r) => selected.has(r.split(" | ")[0])).map((r) => ({ name: r, text: r }));
  const selectedExtra = Math.max(0, (b.scene?.selected_count ?? selected.size) - selected.size);
  const counts = b.scene?.object_counts
    ? Object.entries(b.scene.object_counts).sort(([a], [c]) => a.localeCompare(c)).map(([kind, count]) => `${count} ${kind}`).join(", ") || "empty"
    : b.scene?.counts;
  const settings = b.scene?.settings;
  const fileLine = settings ? [
    b.scene?.file === null ? tr("未保存") : b.scene?.file ?? settings.file,
    `engine ${settings.engine}`,
    `frames ${settings.frames[0]}-${settings.frames[1]} (now ${settings.frames[2]}) at ${settings.fps} fps`,
    settings.resolution.join("x"), `camera ${settings.camera ?? "none"}`,
    `HDRI ${settings.world_hdri ?? "none"}`, `unit scale ${settings.unit_scale}`,
  ].join(" | ") : b.scene?.file_line;
  const results = state.mcpResults.filter((r) => !state.currentId || r.conversation_id === state.currentId);
  const connected = b.blender === "connected" && b.mcp === "running";

  const act = async (kind: "preview" | "info" | "reconnect") => {
    setBusy(kind);
    if (kind === "reconnect") await actions.guard(() => post("/api/blender/reconnect"));
    else {
      const r = await actions.guard(() => post<{ ok: boolean; errors: string[] }>("/api/blender/refresh", { preview: kind === "preview" }));
      if (r && !r.ok) actions.toast(r.errors.join("\n"), "error");
    }
    setBusy("");
  };

  return (
    <aside className={cx("rpanel", exiting && "slide-out")} inert={exiting}>
      <div className="rpanel-head">
        <div className="segmented engine-tabs" role="tablist" aria-label={tr("エンジン")}>
          <button role="tab" aria-selected={engine === "blender"} className={cx(engine === "blender" && "on")} onClick={() => setEngine("blender")}>
            <span className={cx("dot", blenderBadge(b).cls)} /> Blender
          </button>
          <button role="tab" aria-selected={engine === "unreal"} className={cx(engine === "unreal" && "on")} onClick={() => setEngine("unreal")}>
            <span className={cx("dot", unrealBadge(state.unreal).cls)} /> UE5
          </button>
        </div>
        <button className="icon-btn" title={tr("閉じる")} onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      <div className="rpanel-scroll">
        {engine === "unreal" && <UnrealSections onSettings={onSettings} />}
        {engine === "blender" && (
          <>
        <Section
          title={tr("接続状況")}
          right={
            <button className="icon-btn sm" title={tr("MCPに再接続")} disabled={busy !== ""} onClick={() => void act("reconnect")}>
              {busy === "reconnect" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          <div className={cx("status-line", badge.cls)}>
            <span className="dot" /> {badge.label}
          </div>
          <dl className="kv">
            <dt>{tr("接続先")}</dt>
            <dd>{b.endpoint || "—"}</dd>
            <dt>{tr("MCPサーバー")}</dt>
            <dd>{b.server?.name ? `${b.server.name} ${b.server.version ?? ""}` : b.mcp}</dd>
          </dl>
          <McpRestartButton />
          {b.error && <div className="error-text">{tr(b.error)}</div>}
          {b.addon?.text && b.blender === "connected" && <pre className="mini-pre">{b.addon.text}</pre>}
          {(b.blender === "unresponsive" || b.blender === "port_conflict") && <p className="error-text">{b.addon?.text}</p>}
          {b.blender === "disconnected" && b.mcp === "running" && (
            <p className="muted small">{tr("Blenderの3Dビューで N キー →「Roxy Blender MCP」タブから接続してください。")}</p>
          )}
          {b.mcp === "error" && (
            <button className="btn small" onClick={() => onSettings("mcp")}>{tr("MCP設定を開く")}</button>
          )}
        </Section>

        <Section
          title={tr("プレビュー")}
          right={
            <button className="icon-btn sm" title={tr("ビューポートを取得")} disabled={!connected || busy !== ""} onClick={() => void act("preview")}>
              {busy === "preview" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          {b.preview ? (
            <>
              <img
                className="preview-img"
                src={fileUrl(b.preview.attachment_id)}
                alt={tr("Blenderビューポート")}
                onClick={() => lightbox({ src: fileUrl(b.preview!.attachment_id), name: tr("Blender ビューポート") })}
              />
              <div className="muted small">{clockTime(b.preview.at)} {tr("に取得")}</div>
            </>
          ) : (
            <div className="placeholder">
              <Icon name="image" size={22} />
              <span>{connected ? tr("更新ボタンでビューポートを取得します") : tr("Blender接続後に表示できます")}</span>
            </div>
          )}
        </Section>

        <Section
          title={tr("選択オブジェクト")}
          right={
            <button className="icon-btn sm" title={tr("シーン情報を更新")} disabled={!connected || busy !== ""} onClick={() => void act("info")}>
              {busy === "info" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          {b.scene ? (
            <>
              <dl className="kv">
                <dt>{tr("シーン")}</dt>
                <dd>{b.scene.scene ?? "—"}</dd>
                <dt>{tr("アクティブ")}</dt>
                <dd>{b.scene.active ?? tr("なし")}</dd>
                <dt>{tr("選択")}</dt>
                <dd>{b.scene.selected?.length ? b.scene.selected.join(", ") : tr("なし")}{selectedExtra > 0 && tr` ほか${selectedExtra}件`}</dd>
                <dt>{tr("モード")}</dt>
                <dd>{b.scene.mode ?? "—"}</dd>
                <dt>{tr("内容")}</dt>
                <dd>{counts ?? "—"}</dd>
              </dl>
              {selectedRows.length > 0 && (
                <div className="rows">
                  <div className="muted small">{(b.scene.columns ?? []).join(" | ")}</div>
                  {selectedRows.map((r) => (
                    <div key={r.name} className="row-line">{r.text}</div>
                  ))}
                </div>
              )}
              {fileLine && <div className="muted small wrap">{fileLine}{b.scene.is_dirty && tr(" | 未保存の変更あり")}</div>}
              <SendToUnreal />
              <details>
                <summary className="muted small">{tr("MCPの出力全文")}</summary>
                <pre className="mini-pre">{b.scene.text}</pre>
              </details>
              <div className="muted small">{relativeTime(b.scene.at)} {tr("更新")}</div>
            </>
          ) : (
            <div className="muted small">{connected ? tr("更新ボタンでシーン情報を取得します") : tr("Blender接続後に表示できます")}</div>
          )}
        </Section>

          </>
        )}

        <Section title={tr("AIの作業進捗")}>
          {run ? (
            <div className="progress">
              <div className="status-line wait">
                <span className="spinner" /> {tr(run.status_text)}
              </div>
              <div className="muted small">{tr("経過")}{seconds(run.started_at)} {tr("・ ツール")}{run.tool_calls.length} {tr("件")}</div>
              <ol className="steps">
                {run.tool_calls.map((tc) => (
                  <li key={tc.id} className={tc.status}>
                    <span className="ellipsis">{tc.name}</span>
                    <span className="muted small">{tc.status === "running" ? tr("実行中") : tc.status === "ok" ? tr("完了") : tc.status}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <div className="muted small">{tr("この会話で実行中の処理はありません")}</div>
          )}
          {otherRuns.length > 0 && (
            <div className="muted small">{tr("他の会話で")}{otherRuns.length} {tr("件実行中")}</div>
          )}
        </Section>

        <Section title={tr`MCPの結果${results.length ? `（${results.length}）` : ""}`}>
          {results.length === 0 && <div className="muted small">{tr("AIが Blender / UE5 の MCP ツールを使うと、結果がここに表示されます")}</div>}
          {results.slice(0, 10).map((tc) => (
            <ToolCallRow key={tc.id} tc={tc} defaultOpen={tc === results[0] && tc.images.length > 0} />
          ))}
        </Section>

        {engine === "blender" && (
        <Section title={tr`MCPツール（${b.tools.length}）`} defaultOpen={false}>
          {b.tools.length === 0 && <div className="muted small">{tr("MCPサーバー未接続")}</div>}
          <ul className="tool-list">
            {b.tools.map((t) => (
              <li key={t.name} title={t.description}>
                <code>{t.name}</code>
                {t.read_only && <span className="chip">{tr("読み取り")}</span>}
                {t.app_only && <span className="chip">{tr("GUI用")}</span>}
              </li>
            ))}
          </ul>
        </Section>
        )}
      </div>
    </aside>
  );
}
