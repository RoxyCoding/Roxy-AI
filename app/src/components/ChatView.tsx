import { useEffect, useRef, useState } from "react";
import { providerName, useStore } from "../lib/store";
import { Composer, type ComposerHandle } from "./Composer";
import { MessageItem } from "./MessageItem";
import { Icon } from "./Icons";
import type { SettingsTab } from "./SettingsDialog";

export function useTick(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

const SUGGESTIONS = [
  "今のBlenderシーンの状態を確認して教えて",
  "添付した三面図をもとに、モデリングの進め方を相談したい",
  "選択中のオブジェクトの問題点をチェックして",
];

export function ChatView({ onSettings }: { onSettings: (tab: SettingsTab) => void }) {
  const { state } = useStore();
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  const stick = useRef(true);
  const [dragging, setDragging] = useState(false);
  const detail = state.currentId ? state.detail : null;
  const run = state.currentId ? state.runs[state.currentId] ?? null : null;
  const messages = detail?.messages ?? [];
  const provider = state.providers.find((p) => p.id === state.draftProvider);
  useTick(Boolean(run));

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  useEffect(() => {
    stick.current = true;
  }, [state.currentId]);

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
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome">
            <img className="welcome-logo" src="/roxy-logo.png" alt="Roxy" />
            <h1>何を作りましょうか？</h1>
            <p className="muted">
              {providerName(state, state.draftProvider)} が Roxy-BlenderMCP を通して Blender を操作します。
              参考画像や三面図はドラッグ＆ドロップで添付できます。
            </p>
            {provider && !provider.auth_ok && !state.providersLoading && (
              <button className="notice-card" onClick={() => onSettings("ai")}>
                <Icon name="alert" /> {provider.name}: {provider.detail || "ログイン状態を確認してください"}
              </button>
            )}
            {state.blender.blender !== "connected" && (
              <button className="notice-card" onClick={() => onSettings("mcp")}>
                <Icon name="plug" /> Blenderに接続されていません。Blenderの3Dビューで N キー →「Roxy Blender MCP」タブから接続してください。
              </button>
            )}
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion" onClick={() => {
                  const ta = document.querySelector<HTMLTextAreaElement>(".composer textarea");
                  if (ta) {
                    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
                    setter?.call(ta, s);
                    ta.dispatchEvent(new Event("input", { bubbles: true }));
                    ta.focus();
                  }
                }}>
                  {s}
                </button>
              ))}
            </div>
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
      <Composer ref={composer} />
      {dragging && (
        <div className="dropzone">
          <div>
            <Icon name="image" size={28} />
            <p>ここにドロップして添付</p>
          </div>
        </div>
      )}
    </div>
  );
}
