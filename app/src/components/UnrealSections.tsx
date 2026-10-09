import { useState } from "react";
import { fileUrl, post } from "../lib/backend";
import { useStore } from "../lib/store";
import type { UnrealState } from "../lib/types";
import { clockTime, cx, relativeTime } from "../lib/format";
import { Icon } from "./Icons";
import { useLightbox } from "./Lightbox";
import { Section } from "./PanelSection";
import type { SettingsTab } from "./SettingsDialog";

export function unrealBadge(u: UnrealState): { cls: string; label: string } {
  switch (u.status) {
    case "connected":
      return { cls: "ok", label: u.editor?.pie ? "UE5 プレイ中" : "UE5接続中" };
    case "connecting":
      return { cls: "wait", label: "UE5接続処理中" };
    case "waiting":
      return { cls: "off", label: "UE5未起動" };
    case "error":
      return { cls: "bad", label: "UE5エラー" };
    default:
      return { cls: "off", label: "UE5無効" };
  }
}

/** Unreal Editor status: connection, viewport preview, level/selection, toolsets. */
export function UnrealSections({ onSettings }: { onSettings: (tab: SettingsTab) => void }) {
  const { state, actions } = useStore();
  const u = state.unreal;
  const badge = unrealBadge(u);
  const lightbox = useLightbox();
  const [busy, setBusy] = useState<"" | "preview" | "info" | "reconnect">("");
  const connected = u.status === "connected";

  const act = async (kind: "preview" | "info" | "reconnect") => {
    setBusy(kind);
    if (kind === "reconnect") await actions.guard(() => post("/api/unreal/reconnect"));
    else {
      const r = await actions.guard(() => post<{ ok: boolean; errors: string[] }>("/api/unreal/refresh", { preview: kind === "preview" }));
      if (r && !r.ok) actions.toast(r.errors.join("\n"), "error");
    }
    setBusy("");
  };

  return (
    <>
      <Section
        title="接続状況"
        right={
          <button className="icon-btn sm" title="再接続" disabled={busy !== ""} onClick={() => void act("reconnect")}>
            {busy === "reconnect" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
          </button>
        }
      >
        <div className={cx("status-line", badge.cls)}>
          <span className="dot" /> {badge.label}
        </div>
        <dl className="kv">
          <dt>接続先</dt>
          <dd>{u.url || "—"}</dd>
        </dl>
        {u.status === "waiting" && (
          <p className="muted small">Unreal Editor を起動し、MCP プラグイン（エディタ内 MCP サーバー）を有効にしてください。起動すると自動で接続します。</p>
        )}
        {u.status === "disabled" && (
          <button className="btn small" onClick={() => onSettings("unreal")}>Unreal Engine の設定を開く</button>
        )}
        {u.error && u.status !== "waiting" && <div className="error-text">{u.error}</div>}
      </Section>

      <Section
        title="プレビュー"
        right={
          <button className="icon-btn sm" title="ビューポートを取得" disabled={!connected || busy !== ""} onClick={() => void act("preview")}>
            {busy === "preview" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
          </button>
        }
      >
        {u.preview ? (
          <>
            <img className="preview-img" src={fileUrl(u.preview.attachment_id)} alt="Unreal Editor ビューポート"
                 onClick={() => lightbox({ src: fileUrl(u.preview!.attachment_id), name: "Unreal Editor ビューポート" })} />
            <div className="muted small">{clockTime(u.preview.at)} に取得</div>
          </>
        ) : (
          <div className="placeholder">
            <Icon name="image" size={22} />
            <span>{connected ? "更新ボタンでビューポートを取得します" : "Unreal Editor 接続後に表示できます"}</span>
          </div>
        )}
      </Section>

      <Section
        title="エディタの状態"
        right={
          <button className="icon-btn sm" title="状態を更新" disabled={!connected || busy !== ""} onClick={() => void act("info")}>
            {busy === "info" ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
          </button>
        }
      >
        {u.editor ? (
          <>
            <dl className="kv">
              <dt>レベル</dt>
              <dd>{u.editor.level || "—"}</dd>
              <dt>プレイ</dt>
              <dd>{u.editor.pie ? "PIE 実行中" : "停止中"}</dd>
              <dt>選択</dt>
              <dd>{u.editor.selected.length ? `${u.editor.selected.length} 件` : "なし"}</dd>
            </dl>
            {u.editor.selected.length > 0 && (
              <div className="rows">
                {u.editor.selected.slice(0, 20).map((name, i) => (
                  <div key={i} className="row-line" title={u.editor!.selected_paths[i]}>{name}</div>
                ))}
              </div>
            )}
            <div className="muted small">{relativeTime(u.editor.at)} 更新</div>
          </>
        ) : (
          <div className="muted small">{connected ? "更新ボタンで状態を取得します" : "Unreal Editor 接続後に表示できます"}</div>
        )}
      </Section>

      <Section title={`ツールセット（${u.toolsets.length}）`} defaultOpen={false}>
        {u.toolsets.length === 0 && <div className="muted small">未接続</div>}
        <ul className="tool-list">
          {u.toolsets.map((t) => (
            <li key={t}><code>{t}</code></li>
          ))}
        </ul>
      </Section>
    </>
  );
}

/** Ask the AI to move the Blender selection into UE5 with the MCPs' own export/import flow. */
export function SendToUnreal() {
  const { state, actions } = useStore();
  const [sending, setSending] = useState(false);
  const scene = state.blender.scene;
  const target = scene?.active || scene?.selected?.[0] || "";
  const busy = state.currentId ? Boolean(state.runs[state.currentId]) : false;
  const ready = state.blender.blender === "connected" && state.unreal.status === "connected";
  const why = !ready
    ? "Blender と Unreal Editor の両方に接続すると使えます"
    : !target
      ? "Blender でオブジェクトを選択してください"
      : state.draftMode === "prompt"
        ? "モデリングプロンプトのチャットでは使えません（通常のチャットで実行してください）"
        : busy
          ? "応答の完了を待ってください"
          : "";
  const send = async () => {
    setSending(true);
    const folder = "/Game/Roxy";
    const text = [
      `Blender の「${target}」を UE5 に持ち込んでください。`,
      `1. Blender MCP の export_to_unreal（action="export"、name="${target}"、ue_folder="${folder}"）で UE5 向けに書き出す。`,
      "2. 返ってきた手順に従い、Unreal Editor の MCP でインポートする。",
      `3. Unreal 側で取得したバウンズを export_to_unreal（action="verify"）に渡し、サイズ・ピボット・向きが正しいか確認する。`,
      "4. 問題なければ、現在のレベルのワールド原点付近に配置する。",
      "最後に、インポート先のアセットパスと確認結果を短く報告してください。問題があれば修正せずに報告してください。",
    ].join("\n");
    await actions.send(text, []);
    setSending(false);
  };
  return (
    <button className="btn small send-to-ue" disabled={Boolean(why) || sending} title={why || `「${target}」を UE5 に送る`} onClick={() => void send()}>
      {sending ? <span className="spinner" /> : <Icon name="send" size={13} />} UE5へ送る{target ? `（${target}）` : ""}
    </button>
  );
}
