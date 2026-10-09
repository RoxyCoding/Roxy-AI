import { useState } from "react";
import { patch, post } from "../lib/backend";
import { useStore } from "../lib/store";
import { cx, effortLabel, findModel } from "../lib/format";
import { Icon } from "./Icons";

export function ComposerControls() {
  const { state, actions } = useStore();
  const [folderBusy, setFolderBusy] = useState(false);
  const provider = state.draftProvider;
  const models = state.providers.find((p) => p.id === provider)?.models ?? [];
  const current = findModel(models, state.draftModel);
  const efforts = current?.efforts ?? [];
  const project = state.projects.find((p) => p.id === state.activeProjectId);
  const running = state.currentId ? Boolean(state.runs[state.currentId]) : false;
  const enabled = state.boot.providers.filter((p) => state.settings.providers[p.id]?.enabled !== false);

  const saveFolder = async (path: string) => {
    if (!project) return;
    const saved = await actions.guard(() => patch(`/api/projects/${project.id}`, { work_dir: path }));
    if (saved) await actions.guard(() => actions.refreshProjects());
  };
  const chooseFolder = async () => {
    setFolderBusy(true);
    try {
      const result = await actions.guard(() => post<{ path: string | null }>("/api/system/browse", {
        kind: "folder", path: project?.work_dir ?? "",
      }));
      if (result?.path) await saveFolder(result.path);
    } finally {
      setFolderBusy(false);
    }
  };

  return (
    <div className="composer-controls" aria-label="チャットの設定">
      <div className="segmented" role="tablist" aria-label="AIの切り替え">
        {enabled.map((p) => (
          <button key={p.id} role="tab" aria-selected={p.id === provider} className={cx(p.id === provider && "on")}
                  disabled={running} onClick={() => {
                    if (p.id === provider) return;
                    const conf = state.settings.providers[p.id];
                    void actions.setDraft(p.id, conf?.default_model ?? "", conf?.default_effort ?? "");
                  }}>
            {p.name}
          </button>
        ))}
      </div>
      <select className="select" value={state.draftModel} disabled={running} aria-label="モデル" onChange={(e) => {
        const model = e.target.value;
        const next = findModel(models, model);
        void actions.setDraft(provider, model, next?.efforts.includes(state.draftEffort) ? state.draftEffort : "");
      }}>
        {provider === "codex" && <option value="">既定のモデル</option>}
        {provider === "local" && <option value="">自動（最初のモデル）</option>}
        {models.map((m) => <option key={m.id} value={m.id} title={m.description}>{m.name}{m.supports_images ? "" : "（画像非対応）"}</option>)}
        {state.draftModel && !models.some((m) => m.id === state.draftModel) && <option value={state.draftModel}>{state.draftModel}</option>}
      </select>
      <select className="select effort" value={efforts.includes(state.draftEffort) ? state.draftEffort : ""}
              disabled={running || !efforts.length} aria-label="思考レベル"
              title={efforts.length ? "高い思考レベルほど時間とトークンを多く使います" : "このモデルは思考レベルの変更に対応していません"}
              onChange={(e) => void actions.setDraft(provider, state.draftModel, e.target.value)}>
        <option value="">思考: 既定{current?.default_effort ? `（${effortLabel(current.default_effort)}）` : ""}</option>
        {efforts.map((effort) => <option key={effort} value={effort}>思考: {effortLabel(effort)}</option>)}
      </select>
      {state.draftMode !== "prompt" && <div className="composer-folder">
        <button className="btn folder-picker" disabled={running || folderBusy || !project}
                title={project?.work_dir || "作業フォルダは未指定でも利用できます"} onClick={() => void chooseFolder()}>
          {folderBusy ? <span className="spinner" /> : <Icon name="folder" size={14} />}
          <span className="ellipsis">{project?.work_dir ? project.work_dir.split(/[\\/]/).pop() : "作業フォルダ（任意）"}</span>
        </button>
        {project?.work_dir && (
          <button className="icon-btn sm" disabled={running || folderBusy} title="作業フォルダの指定を解除"
                  onClick={async () => {
                    setFolderBusy(true);
                    try { await saveFolder(""); } finally { setFolderBusy(false); }
                  }}>
            <Icon name="x" size={13} />
          </button>
        )}
      </div>}
    </div>
  );
}
