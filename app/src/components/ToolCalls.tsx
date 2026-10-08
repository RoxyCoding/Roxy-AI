import { useState } from "react";
import { fileUrl } from "../lib/backend";
import type { ToolCall } from "../lib/types";
import { cx, pretty, seconds } from "../lib/format";
import { Icon } from "./Icons";
import { useLightbox } from "./Lightbox";
import { useSlide } from "./useSlide";

const STATUS: Record<ToolCall["status"], string> = {
  running: "実行中",
  ok: "完了",
  error: "エラー",
  cancelled: "中断",
  unknown: "不明",
};

export function ToolCallRow({ tc, defaultOpen = false }: { tc: ToolCall; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const body = useSlide(open ? true : null);
  const lightbox = useLightbox();
  const input = pretty(tc.input);
  return (
    <div className={cx("tool", tc.status)}>
      <button className="tool-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={cx("tool-state", tc.status)}>
          {tc.status === "running" ? <span className="spinner" /> : <Icon name={tc.status === "ok" ? "check" : tc.status === "error" ? "alert" : "x"} size={12} />}
        </span>
        <Icon name="tool" size={13} />
        <span className="tool-name ellipsis">{tc.name}</span>
        <span className="tool-meta">
          {STATUS[tc.status]}
          {tc.ended_at ? ` ・ ${seconds(tc.started_at, tc.ended_at)}` : ""}
          {tc.images.length ? ` ・ 画像${tc.images.length}` : ""}
        </span>
        <Icon name={open ? "down" : "chevron"} size={12} />
      </button>
      {body.value && (
        <div className={cx("tool-body", body.exiting && "slide-out")} inert={body.exiting}>
          {input && input !== "{}" && (
            <>
              <div className="tool-label">入力</div>
              <pre className="tool-pre">{input}</pre>
            </>
          )}
          {tc.output && (
            <>
              <div className="tool-label">結果</div>
              <pre className="tool-pre">{tc.output}</pre>
            </>
          )}
        </div>
      )}
      {tc.images.length > 0 && (
        <div className="thumbs tool-images">
          {tc.images.map((id) => (
            <button key={id} className="icon-btn" title="画像を拡大" onClick={() => lightbox({ src: fileUrl(id), name: tc.name })}>
              <img src={fileUrl(id, true)} alt="画像プレビュー" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ToolCalls({ calls }: { calls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!calls.length) return null;
  const visible = expanded || calls.length <= 4 ? calls : calls.filter((tc, i) => i >= calls.length - 3 || tc.images.length > 0);
  return (
    <div className="tools">
      {calls.length > 4 && (
        <button className="link-btn small" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "折りたたむ" : `ツール実行 ${calls.length}件をすべて表示`}
        </button>
      )}
      {visible.map((tc) => (
        <ToolCallRow key={tc.id} tc={tc} />
      ))}
    </div>
  );
}
