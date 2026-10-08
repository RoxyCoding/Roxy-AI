import { useState, type ReactNode } from "react";
import { fileUrl, post } from "../lib/backend";
import { useStore } from "../lib/store";
import { clockTime, cx, relativeTime, seconds } from "../lib/format";
import { Icon } from "./Icons";
import { useLightbox } from "./Lightbox";
import { McpRestartButton } from "./McpRestartButton";
import { ToolCallRow } from "./ToolCalls";
import { blenderBadge } from "./TopBar";
import type { SettingsTab } from "./SettingsDialog";
import { useTick } from "./ChatView";

function Section({ title, children, right, defaultOpen = true }: { title: string; children: ReactNode; right?: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="panel-section">
      <div className="panel-section-head">
        <button className="link-btn strong" onClick={() => setOpen((v) => !v)}>
          <Icon name={open ? "down" : "chevron"} size={12} /> {title}
        </button>
        {right}
      </div>
      {open && <div className="panel-section-body">{children}</div>}
    </section>
  );
}

export function RightPanel({ onClose, onSettings, exiting = false }: { onClose: () => void; onSettings: (tab: SettingsTab) => void; exiting?: boolean }) {
  const { state, actions } = useStore();
  const b = state.blender;
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
    b.scene?.file === null ? "未保存" : b.scene?.file ?? settings.file,
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
        <span className="strong">Blender</span>
        <button className="icon-btn" title="閉じる" onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      <div className="rpanel-scroll">
        <Section
          title="接続状況"
          right={
            <button className="icon-btn sm" title="MCPに再接続" disabled={busy !== ""} onClick={() => void act("reconnect")}>
              {busy === "reconnect" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          <div className={cx("status-line", badge.cls)}>
            <span className="dot" /> {badge.label}
          </div>
          <dl className="kv">
            <dt>接続先</dt>
            <dd>{b.endpoint || "—"}</dd>
            <dt>MCPサーバー</dt>
            <dd>{b.server?.name ? `${b.server.name} ${b.server.version ?? ""}` : b.mcp}</dd>
          </dl>
          <McpRestartButton />
          {b.error && <div className="error-text">{b.error}</div>}
          {b.addon?.text && b.blender === "connected" && <pre className="mini-pre">{b.addon.text}</pre>}
          {b.blender === "unresponsive" && <p className="error-text">{b.addon?.text}</p>}
          {b.blender === "disconnected" && b.mcp === "running" && (
            <p className="muted small">Blenderの3Dビューで N キー →「Roxy Blender MCP」タブから接続してください。</p>
          )}
          {b.mcp === "error" && (
            <button className="btn small" onClick={() => onSettings("mcp")}>MCP設定を開く</button>
          )}
        </Section>

        <Section
          title="プレビュー"
          right={
            <button className="icon-btn sm" title="ビューポートを取得" disabled={!connected || busy !== ""} onClick={() => void act("preview")}>
              {busy === "preview" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          {b.preview ? (
            <>
              <img
                className="preview-img"
                src={fileUrl(b.preview.attachment_id)}
                alt="Blenderビューポート"
                onClick={() => lightbox({ src: fileUrl(b.preview!.attachment_id), name: "Blender ビューポート" })}
              />
              <div className="muted small">{clockTime(b.preview.at)} に取得</div>
            </>
          ) : (
            <div className="placeholder">
              <Icon name="image" size={22} />
              <span>{connected ? "更新ボタンでビューポートを取得します" : "Blender接続後に表示できます"}</span>
            </div>
          )}
        </Section>

        <Section
          title="選択オブジェクト"
          right={
            <button className="icon-btn sm" title="シーン情報を更新" disabled={!connected || busy !== ""} onClick={() => void act("info")}>
              {busy === "info" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          }
        >
          {b.scene ? (
            <>
              <dl className="kv">
                <dt>シーン</dt>
                <dd>{b.scene.scene ?? "—"}</dd>
                <dt>アクティブ</dt>
                <dd>{b.scene.active ?? "なし"}</dd>
                <dt>選択</dt>
                <dd>{b.scene.selected?.length ? b.scene.selected.join(", ") : "なし"}{selectedExtra > 0 && ` ほか${selectedExtra}件`}</dd>
                <dt>モード</dt>
                <dd>{b.scene.mode ?? "—"}</dd>
                <dt>内容</dt>
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
              {fileLine && <div className="muted small wrap">{fileLine}{b.scene.is_dirty && " | 未保存の変更あり"}</div>}
              <details>
                <summary className="muted small">MCPの出力全文</summary>
                <pre className="mini-pre">{b.scene.text}</pre>
              </details>
              <div className="muted small">{relativeTime(b.scene.at)} 更新</div>
            </>
          ) : (
            <div className="muted small">{connected ? "更新ボタンでシーン情報を取得します" : "Blender接続後に表示できます"}</div>
          )}
        </Section>

        <Section title="AIの作業進捗">
          {run ? (
            <div className="progress">
              <div className="status-line wait">
                <span className="spinner" /> {run.status_text}
              </div>
              <div className="muted small">経過 {seconds(run.started_at)} ・ ツール {run.tool_calls.length} 件</div>
              <ol className="steps">
                {run.tool_calls.map((tc) => (
                  <li key={tc.id} className={tc.status}>
                    <span className="ellipsis">{tc.name}</span>
                    <span className="muted small">{tc.status === "running" ? "実行中" : tc.status === "ok" ? "完了" : tc.status}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <div className="muted small">この会話で実行中の処理はありません</div>
          )}
          {otherRuns.length > 0 && (
            <div className="muted small">他の会話で {otherRuns.length} 件実行中</div>
          )}
        </Section>

        <Section title={`MCPの結果${results.length ? `（${results.length}）` : ""}`}>
          {results.length === 0 && <div className="muted small">AIがBlender MCPのツールを使うと、結果がここに表示されます</div>}
          {results.slice(0, 10).map((tc) => (
            <ToolCallRow key={tc.id} tc={tc} defaultOpen={tc === results[0] && tc.images.length > 0} />
          ))}
        </Section>

        <Section title={`MCPツール（${b.tools.length}）`} defaultOpen={false}>
          {b.tools.length === 0 && <div className="muted small">MCPサーバー未接続</div>}
          <ul className="tool-list">
            {b.tools.map((t) => (
              <li key={t.name} title={t.description}>
                <code>{t.name}</code>
                {t.read_only && <span className="chip">読み取り</span>}
                {t.app_only && <span className="chip">GUI用</span>}
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </aside>
  );
}
