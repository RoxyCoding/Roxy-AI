import { t as tr } from "../lib/i18n";
import { useState } from "react";
import { patch, post } from "../lib/backend";
import { useStore } from "../lib/store";
import { effortLabel, findModel } from "../lib/format";
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
    <div className="composer-controls" aria-label={tr("チャットの設定")}>
      {state.draftMode === "design_prompt" && provider !== "codex" && <span className="muted small">{tr("画像生成にはCodexを選択してください。このAIでは条件相談とプロンプト作成を利用できます。")}</span>}
      <select className="select provider-select" value={provider} disabled={running} aria-label={tr("AIの切り替え")}
              title={running ? tr("応答生成中は切り替えできません") : tr("使用するAI")}
              onChange={(e) => {
                const id = e.target.value;
                if (id === provider) return;
                const conf = state.settings.providers[id];
                void actions.setDraft(id, conf?.default_model ?? "", conf?.default_effort ?? "");
              }}>
        {enabled.map((p) => (
          <option key={p.id} value={p.id}>{tr(p.name)}</option>
        ))}
      </select>
      <select className="select" value={state.draftModel} disabled={running} aria-label={tr("モデル")} onChange={(e) => {
        const model = e.target.value;
        const next = findModel(models, model);
        void actions.setDraft(provider, model, next?.efforts.includes(state.draftEffort) ? state.draftEffort : "");
      }}>
        {provider === "codex" && <option value="">{tr("既定のモデル")}</option>}
        {provider === "local" && <option value="">{tr("自動（最初のモデル）")}</option>}
        {models.map((m) => <option key={m.id} value={m.id} title={m.description}>{tr(m.name)}{m.supports_images ? "" : tr("（画像非対応）")}</option>)}
        {state.draftModel && !models.some((m) => m.id === state.draftModel) && <option value={state.draftModel}>{state.draftModel}</option>}
      </select>
      <select className="select effort" value={efforts.includes(state.draftEffort) ? state.draftEffort : ""}
              disabled={running || !efforts.length} aria-label={tr("思考レベル")}
              title={efforts.length ? tr("高い思考レベルほど時間とトークンを多く使います") : tr("このモデルは思考レベルの変更に対応していません")}
              onChange={(e) => void actions.setDraft(provider, state.draftModel, e.target.value)}>
        <option value="">{tr("既定")}{current?.default_effort ? `（${effortLabel(current.default_effort)}）` : ""}</option>
        {efforts.map((effort) => <option key={effort} value={effort}>{effortLabel(effort)}</option>)}
      </select>
      {state.draftMode === "modeling" && <div className="composer-folder">
        <button className="btn folder-picker" disabled={running || folderBusy || !project}
                title={project?.work_dir || tr("作業フォルダは未指定でも利用できます")} onClick={() => void chooseFolder()}>
          {folderBusy ? <span className="spinner" /> : <Icon name="folder" size={14} />}
          <span className="ellipsis">{project?.work_dir ? project.work_dir.split(/[\\/]/).pop() : tr("作業フォルダ（任意）")}</span>
        </button>
        {project?.work_dir && (
          <button className="icon-btn sm" disabled={running || folderBusy} title={tr("作業フォルダの指定を解除")}
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
