import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { del, fileUrl, upload } from "../lib/backend";
import { useStore } from "../lib/store";
import type { Attachment } from "../lib/types";
import { cx, fileSize } from "../lib/format";
import { Icon } from "./Icons";
import { useLightbox } from "./Lightbox";
import { ComposerControls } from "./ComposerControls";

interface Pending {
  key: string;
  name: string;
  size: number;
  isImage: boolean;
  att?: Attachment;
  error?: string;
}

export interface ComposerHandle {
  addFiles: (files: File[]) => void;
}

let keySeq = 0;
const MAX_FILES = 10;

export const Composer = forwardRef<ComposerHandle>(function Composer(_, ref) {
  const { state, actions, dispatch } = useStore();
  const [text, setText] = useState("");
  const [items, setItems] = useState<Pending[]>([]);
  const [sending, setSending] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const lightbox = useLightbox();
  useEffect(() => {
    if (state.composerSeed === null) return;
    setText(state.composerSeed.text);
    setItems(state.composerSeed.attachments.map((att) => ({
      key: `p${keySeq++}`, name: att.name, size: att.size, isImage: att.mime.startsWith("image/"), att,
    })));
    dispatch({ type: "composerSeed", seed: null });
    textarea.current?.focus();
  }, [state.composerSeed, dispatch]);
  useEffect(() => {
    const input = textarea.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 260)}px`;
  }, [text]);

  const cid = state.currentId;
  const running = cid ? Boolean(state.runs[cid]) : false;
  const uploading = items.some((i) => !i.att && !i.error);
  const model = state.providers.find((p) => p.id === state.draftProvider)?.models.find((m) => m.id === state.draftModel);
  const imagesUnsupported = model && !model.supports_images && items.some((i) => i.isImage);

  const addFiles = (files: File[], replaceKey?: string | null) => {
    const room = MAX_FILES - items.length + (replaceKey ? 1 : 0);
    if (files.length > room) actions.toast(`添付は1回につき${MAX_FILES}件までです`, "error");
    const chosen = files.slice(0, Math.max(0, room));
    if (!chosen.length) return;
    const fresh: Pending[] = chosen.map((f) => ({
      key: `p${keySeq++}`,
      name: f.name || "貼り付け画像.png",
      size: f.size,
      isImage: f.type.startsWith("image/"),
    }));
    setItems((prev) => {
      if (!replaceKey) return [...prev, ...fresh];
      const i = prev.findIndex((p) => p.key === replaceKey);
      const old = prev[i];
      if (old?.att) void del(`/api/attachments/${old.att.id}`).catch(() => undefined);
      const copy = prev.slice();
      copy.splice(i < 0 ? copy.length : i, i < 0 ? 0 : 1, ...fresh);
      return copy;
    });
    chosen.forEach((f, idx) => {
      const key = fresh[idx].key;
      upload<Attachment[]>([new File([f], fresh[idx].name, { type: f.type })])
        .then(([att]) => setItems((prev) => prev.map((p) => (p.key === key ? { ...p, att } : p))))
        .catch((e) => setItems((prev) => prev.map((p) => (p.key === key ? { ...p, error: String(e.message ?? e) } : p))));
    });
  };

  useImperativeHandle(ref, () => ({ addFiles: (files) => addFiles(files) }));

  const remove = (key: string) => {
    const it = items.find((i) => i.key === key);
    if (it?.att) void del(`/api/attachments/${it.att.id}`).catch(() => undefined);
    setItems((prev) => prev.filter((p) => p.key !== key));
  };

  const submit = async () => {
    if (sending || running || uploading) return;
    const ready = items.filter((i) => i.att).map((i) => i.att!.id);
    if (!text.trim() && !ready.length) return;
    setSending(true);
    const ok = await actions.send(text, ready);
    setSending(false);
    if (ok) {
      setText("");
      setItems([]);
      textarea.current?.focus();
    }
  };

  return (
    <div className="composer-wrap">
      <div className={cx("composer", running && "busy")}>
        {items.length > 0 && (
          <div className="pending">
            {items.map((it) => (
              <div key={it.key} className={cx("pending-item", it.error && "failed")} title={it.error || it.name}>
                {it.isImage && it.att ? (
                  <img src={fileUrl(it.att.id, true)} alt={it.name} onClick={() => lightbox({ src: fileUrl(it.att!.id), name: it.name })} />
                ) : (
                  <div className="pending-file">
                    {!it.att && !it.error ? <span className="spinner" /> : <Icon name={it.error ? "alert" : "file"} />}
                  </div>
                )}
                <div className="pending-name">
                  <span className="ellipsis">{it.name}</span>
                  <span className="muted">{it.error ? "失敗" : fileSize(it.size)}</span>
                </div>
                <div className="pending-actions">
                  <button className="icon-btn sm" title="差し替え" onClick={() => { replaceTarget.current = it.key; fileInput.current?.click(); }}>
                    <Icon name="retry" size={13} />
                  </button>
                  <button className="icon-btn sm" title="削除" onClick={() => remove(it.key)}>
                    <Icon name="x" size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {imagesUnsupported && (
          <div className="composer-warn">
            <Icon name="alert" size={13} /> 選択中のモデル「{model?.name}」は画像入力に対応していないため、画像はファイルパスとしてのみ渡されます。
          </div>
        )}
        <textarea
          ref={textarea}
          value={text}
          rows={1}
          placeholder={state.draftMode === "prompt" ? "作りたいものや用途、好みを相談…（参考画像も添付できます）" : "Blenderで作りたいものや、やりたいことを入力…（Shift+Enterで改行）"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Never send while a Japanese IME is composing.
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
              e.preventDefault();
              void submit();
            }
          }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files);
            if (files.length) {
              e.preventDefault();
              addFiles(files);
            }
          }}
        />
        <div className="composer-bar">
          <button className="icon-btn" title="ファイルを添付（ドラッグ＆ドロップ・貼り付けも可）" onClick={() => { replaceTarget.current = null; fileInput.current?.click(); }}>
            <Icon name="clip" />
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (replaceTarget.current) addFiles(files.slice(0, 1), replaceTarget.current);
              else addFiles(files);
              replaceTarget.current = null;
            }}
          />
          <span className="composer-hint muted">{uploading ? "アップロード中…" : ""}</span>
          {running ? (
            <button className="send-btn stop" title="応答を中断" onClick={() => cid && void actions.cancel(cid)}>
              <Icon name="stop" />
            </button>
          ) : (
            <button
              className="send-btn"
              title="送信 (Enter)"
              disabled={sending || uploading || (!text.trim() && !items.some((i) => i.att))}
              onClick={() => void submit()}
            >
              {sending ? <span className="spinner light" /> : <Icon name="send" />}
            </button>
          )}
        </div>
      </div>
      <ComposerControls />
    </div>
  );
});
