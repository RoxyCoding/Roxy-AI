import { t as tr } from "../lib/i18n";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../lib/store";
import { Composer, type ComposerHandle } from "./Composer";
import { MessageItem } from "./MessageItem";
import { Icon } from "./Icons";
import type { SettingsTab } from "./SettingsDialog";
import { WorkflowPanel } from "./WorkflowPanel";

export function useTick(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

export function ChatView({ onSettings }: { onSettings: (tab: SettingsTab) => void }) {
  const { state } = useStore();
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  const stick = useRef(true);
  const viewSize = useRef({ width: 0, height: 0 });
  const [dragging, setDragging] = useState(false);
  const detail = state.currentId ? state.detail : null;
  const run = state.currentId ? state.runs[state.currentId] ?? null : null;
  const messages = detail?.messages ?? [];
  const provider = state.providers.find((p) => p.id === state.draftProvider);
  const promptMode = state.draftMode === "prompt";
  const designMode = state.draftMode === "design_prompt";
  useTick(Boolean(run));

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  useEffect(() => {
    stick.current = true;
  }, [state.currentId]);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      viewSize.current = { width: el.clientWidth, height: el.clientHeight };
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) composer.current?.addFiles(files);
  };

  return (
    <div
      className="chat"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div
        className="chat-scroll"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          if (el.clientWidth !== viewSize.current.width || el.clientHeight !== viewSize.current.height) return;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome">
            <img className="welcome-logo" src="/roxy-logo.png" alt="Roxy" />
            <h1>{designMode ? tr("モデリングに使う設定画を考えましょう") : promptMode ? tr("何を作るか相談しましょう") : tr("何を作りましょうか？")}</h1>
            {designMode && <p className="muted">{tr("四面図・寸法・細部・構造・素材を整理して設定画を生成します。参考画像も添付できます。プロンプトの作成だけでも利用できます。")}</p>}
            {provider && !provider.auth_ok && !state.providersLoading && (
              <button className="notice-card" onClick={() => onSettings("ai")}>
                <Icon name="alert" /> {provider.name}: {provider.detail || tr("ログイン状態を確認してください")}
              </button>
            )}
            {state.draftMode === "modeling" && state.blender.blender !== "connected" && (
              <button className="notice-card" onClick={() => onSettings("mcp")}>
                <Icon name="plug" /> {tr("Blenderに接続されていません。Blenderの3Dビューで N キー →「Roxy Blender MCP」タブから接続してください。")}</button>
            )}
          </div>
        ) : (
          <div className="thread">
            {messages.map((m, i) => (
              <MessageItem
                key={m.id}
                m={m}
                run={run}
                isLast={i === messages.length - 1}
                attachments={detail?.attachments ?? {}}
              />
            ))}
          </div>
        )}
      </div>
      <WorkflowPanel key={`workflow:${state.currentId}`} />
      <Composer key={`composer:${state.currentId ?? state.draftMode}`} ref={composer} />
      {dragging && (
        <div className="dropzone">
          <div>
            <Icon name="image" size={28} />
            <p>{tr("ここにドロップして添付")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
