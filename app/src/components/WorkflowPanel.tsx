import { t as tr } from "../lib/i18n";
import { useState } from "react";
import { useStore } from "../lib/store";
import type { ModelingBrief, ModelingStage } from "../lib/types";
import { cx } from "../lib/format";
import { Icon } from "./Icons";

const BRIEF_FIELDS: [keyof ModelingBrief, string][] = [
  ["target", "作るもの"], ["use", "用途"], ["shape", "形・構造"],
  ["dimensions", "寸法"], ["materials", "素材"], ["colors", "色"],
];
const STAGES: [ModelingStage, string][] = [
  ["shape", "形状"], ["details", "細部"], ["materials", "素材"], ["finish", "仕上げ"],
];

export function WorkflowPanel() {
  const { state, actions } = useStore();
  const conv = state.detail?.conversation;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ModelingBrief>({});
  const [saving, setSaving] = useState(false);
  if (!conv) return null;
  const brief = conv.brief ?? {};
  const run = state.runs[conv.id];
  const last = state.detail?.messages.filter((m) => m.role === "assistant" && m.meta.progress).at(-1);
  const progress = run ? run.progress : last?.meta.progress;
  const showBrief = conv.mode !== "modeling" || Object.values(brief).some(Boolean);
  const progressLabel = run ? tr("制作中") : last?.status === "error" ? tr("エラーで終了") : last?.status === "cancelled" ? tr("中断") : progress?.stage === "done" ? tr("制作完了") : tr("前回の工程報告");

  return (
    <div className="workflow-panel">
      {showBrief && <details className="brief-panel">
        <summary><Icon name="edit" size={14} /><span>{tr("制作条件")}</span>{brief.target && <span className="muted ellipsis">{brief.target}</span>}</summary>
        <div className="brief-fields">
          {BRIEF_FIELDS.map(([key, label]) => editing ? (
            <label className="field" key={key}>
              <span>{tr(label)}</span>
              <textarea className="input" aria-label={tr(label)} rows={2} maxLength={2000} value={draft[key] ?? ""}
                        disabled={saving} onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))} />
            </label>
          ) : (
            <div className="brief-value" key={key}><span className="muted small">{tr(label)}</span><span>{brief[key] || tr("未指定")}</span></div>
          ))}
        </div>
        <div className="row gap-sm end brief-actions">
          {editing ? <>
            <button className="btn small" disabled={saving} onClick={() => setEditing(false)}>{tr("キャンセル")}</button>
            <button className="btn primary small" disabled={saving || Boolean(run)} onClick={async () => {
              setSaving(true);
              try { if (await actions.saveBrief(draft)) setEditing(false); } finally { setSaving(false); }
            }}>{tr("条件を保存")}</button>
          </> : <button className="btn small" disabled={Boolean(run)} onClick={() => { setDraft(brief); setEditing(true); }}>{tr("条件を編集")}</button>}
        </div>
      </details>}
      {conv.mode === "modeling" && progress && <div className="modeling-progress" role="status" aria-live="polite">
        <div className="row between gap-sm"><span className="strong">{progressLabel}</span>
          {progress?.detail && <span className="muted progress-detail">{progress.detail}</span>}
        </div>
        <ol className="progress-stages" aria-label={tr("制作の進捗")}>
          {STAGES.map(([key, label], index) => {
            const done = progress?.completed.includes(key);
            const active = Boolean(run) && progress?.stage === key && !done;
            return <li key={key} className={cx(done && "done", active && "current")} aria-current={active ? "step" : undefined}>
              <span className="stage-dot">{done ? <Icon name="check" size={12} /> : index + 1}</span>
              <span>{tr(label)}</span><span className="sr-only">{done ? tr("完了") : active ? tr("作業中") : tr("未完了")}</span>
            </li>;
          })}
        </ol>
      </div>}
    </div>
  );
}
