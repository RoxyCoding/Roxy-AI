import { useState } from "react";
import { fileUrl } from "../lib/backend";
import { providerName, useStore } from "../lib/store";
import type { Attachment, Message, RunSnapshot } from "../lib/types";
import { clockTime, cx, fileSize, seconds } from "../lib/format";
import { Icon } from "./Icons";
import { Markdown } from "./Markdown";
import { ToolCalls } from "./ToolCalls";
import { useLightbox } from "./Lightbox";

function AttachmentList({ ids, map }: { ids: string[]; map: Record<string, Attachment> }) {
  const lightbox = useLightbox();
  if (!ids.length) return null;
  return (
    <div className="msg-attachments">
      {ids.map((id) => {
        const a = map[id];
        if (!a) return null;
        return a.mime.startsWith("image/") ? (
          <img key={id} className="msg-thumb" src={fileUrl(id, true)} alt={a.name} title={a.name}
               onClick={() => lightbox({ src: fileUrl(id), name: a.name })} />
        ) : (
          <div key={id} className="file-chip" title={a.name}>
            <Icon name="file" /> <span className="ellipsis">{a.name}</span> <span className="muted">{fileSize(a.size)}</span>
          </div>
        );
      })}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="icon-btn sm"
      title="コピー"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      <Icon name={done ? "check" : "copy"} />
    </button>
  );
}

export function MessageItem({ m, run, isLast, attachments }: {
  m: Message;
  run: RunSnapshot | null;
  isLast: boolean;
  attachments: Record<string, Attachment>;
}) {
  const { state, actions } = useStore();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(m.content);
  const busy = Boolean(state.runs[m.conversation_id]);

  if (m.role === "user") {
    return (
      <div className="msg user">
        {editing ? (
          <div className="edit-box">
            <textarea value={draft} autoFocus rows={Math.min(10, draft.split("\n").length + 1)} onChange={(e) => setDraft(e.target.value)} />
            <div className="edit-note">
              保存すると、このメッセージ以降の会話は削除され、AI内部のセッションはリセットされます（会話内容は引き継がれます）。
            </div>
            <div className="row gap-sm end">
              <button className="btn" onClick={() => { setEditing(false); setDraft(m.content); }}>キャンセル</button>
              <button
                className="btn primary"
                disabled={busy || !draft.trim()}
                onClick={async () => {
                  if (await actions.editMessage(m, draft)) setEditing(false);
                }}
              >
                保存して再送信
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="bubble">
              <AttachmentList ids={m.attachments} map={attachments} />
              {m.content && <div className="user-text">{m.content}</div>}
            </div>
            <div className="msg-tools">
              <CopyButton text={m.content} />
              <button className="icon-btn sm" title="編集して再送信" disabled={busy} onClick={() => { setDraft(m.content); setEditing(true); }}>
                <Icon name="edit" />
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  const live = m.status === "streaming" && run && run.assistant_message_id === m.id ? run : null;
  const text = live ? live.text : m.content;
  const tools = live ? live.tool_calls : m.tool_calls;
  const notices = live ? live.notices : m.meta?.notices ?? [];
  const label = `${providerName(state, m.provider)}${m.model ? ` ・ ${m.model}` : ""}`;

  return (
    <div className="msg assistant">
      <div className="msg-head">
        <span className={cx("avatar", m.provider)}>{m.provider === "codex" ? "C" : "A"}</span>
        <span className="msg-who">{label}</span>
        <span className="muted small">{clockTime(m.created_at)}</span>
      </div>
      {notices.length > 0 && (
        <div className="notices">
          {notices.map((n, i) => (
            <div key={i} className="notice">
              <Icon name="alert" size={13} /> {n}
            </div>
          ))}
        </div>
      )}
      <ToolCalls calls={tools} />
      {text && <Markdown text={text} />}
      {live && (
        <div className="run-status">
          <span className="spinner" /> {live.status_text} <span className="muted">（{seconds(live.started_at)}）</span>
        </div>
      )}
      {m.status === "error" && (
        <div className="msg-error">
          <Icon name="alert" /> <span>{m.error || "エラーが発生しました"}</span>
        </div>
      )}
      {m.status === "cancelled" && <div className="muted small">応答は中断されました</div>}
      {!live && (
        <div className="msg-tools left">
          {text && <CopyButton text={text} />}
          {isLast && (m.status === "error" || m.status === "cancelled") && (
            <button className="btn small" disabled={busy} onClick={() => void actions.retry(m.conversation_id)}>
              <Icon name="retry" size={13} /> 再試行
            </button>
          )}
          {m.meta?.duration ? <span className="muted small">{seconds(0, m.meta.duration)}</span> : null}
        </div>
      )}
    </div>
  );
}
