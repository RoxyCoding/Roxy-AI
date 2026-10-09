import { t as tr } from "../lib/i18n";
import { useEffect, useState, type ReactNode } from "react";
import { del, get, patch, post } from "../lib/backend";
import { useStore } from "../lib/store";
import type { ExtraMcpServer, Project, ProviderStatus } from "../lib/types";
import { cx, effortLabel, findModel } from "../lib/format";
import { Icon } from "./Icons";
import { McpRestartButton } from "./McpRestartButton";

export type SettingsTab = "general" | "ai" | "mcp" | "unreal" | "extra" | "projects" | "logs";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "general", label: "一般" },
  { id: "ai", label: "AI" },
  { id: "mcp", label: "Blender MCP" },
  { id: "unreal", label: "Unreal Engine" },
  { id: "extra", label: "その他のMCP" },
  { id: "projects", label: "プロジェクト" },
  { id: "logs", label: "ログ・診断" },
];

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="muted small">{hint}</span>}
    </label>
  );
}

type BrowseKind = "file" | "folder" | "blend";

function BrowseButton({ kind, value, onSelect }: { kind: BrowseKind; value: string; onSelect: (path: string) => void }) {
  const { actions } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <button type="button" className="btn" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const result = await actions.guard(() => post<{ path: string | null }>("/api/system/browse", { kind, path: value }));
        if (result?.path) onSelect(result.path);
      } finally {
        setBusy(false);
      }
    }}>
      {busy ? <span className="spinner" /> : <Icon name="folder" size={13} />} {tr("参照")}</button>
  );
}

function TextSetting({ value, onSave, placeholder, type = "text", browse }: { value: string | number; onSave: (v: string) => void; placeholder?: string; type?: string; browse?: BrowseKind }) {
  const [v, setV] = useState(String(value ?? ""));
  useEffect(() => setV(String(value ?? "")), [value]);
  const input = <input className={cx("input", browse && "grow")} type={type} value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)}
                       onBlur={() => v !== String(value ?? "") && onSave(v)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />;
  return browse ? (
    <div className="row gap-sm">
      {input}
      <BrowseButton kind={browse} value={v} onSelect={(path) => { setV(path); onSave(path); }} />
    </div>
  ) : input;
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="toggle-row">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {hint && <span className="muted small block">{hint}</span>}
      </span>
    </label>
  );
}

function General() {
  const { state, actions } = useStore();
  const s = state.settings;
  return (
    <div className="form">
      <Field label={tr("言語")} hint={tr("画面表示と、次のメッセージからAIの回答言語に適用されます。")}>
        <select className="select" value={s.ui.language ?? "ja"} aria-label={tr("言語")}
                onChange={(e) => void actions.saveSettings({ ui: { language: e.target.value } })}>
          <option value="ja">日本語</option>
          <option value="en">English</option>
        </select>
      </Field>
      <Field label={tr("テーマ")}>
        <div className="segmented">
          {(["system", "light", "dark"] as const).map((t) => (
            <button key={t} className={cx(s.theme === t && "on")} onClick={() => void actions.saveSettings({ theme: t })}>
              {t === "system" ? tr("システム") : t === "light" ? tr("ライト") : tr("ダーク")}
            </button>
          ))}
        </div>
      </Field>
      <Field label={tr("新規チャットで使うAI")}>
        <select className="select" value={s.default_provider} onChange={(e) => void actions.saveSettings({ default_provider: e.target.value })}>
          {state.boot.providers.map((p) => <option key={p.id} value={p.id}>{tr(p.name)}</option>)}
        </select>
      </Field>
      <Field label={tr("AI切り替え時に引き継ぐ会話の上限（文字数）")} hint={tr("別のAIへ切り替えたとき、会話内容をこの文字数まで渡します（内部セッションは引き継げません）。")}>
        <TextSetting type="number" value={s.context.handoff_max_chars} onSave={(v) => void actions.saveSettings({ context: { handoff_max_chars: Math.max(2000, Number(v) || 40000) } })} />
      </Field>
    </div>
  );
}

interface ModelDownload {
  id: string;
  source: string;
  name: string;
  status: "preparing" | "downloading" | "verifying" | "done" | "error" | "cancelled";
  total: number;
  done: number;
  error: string;
  path: string;
}

const DL_STATUS: Record<ModelDownload["status"], string> = {
  preparing: "準備中",
  downloading: "ダウンロード中",
  verifying: "検証中",
  done: "完了",
  error: "失敗",
  cancelled: "中止",
};

function ModelDownloads() {
  const { actions } = useStore();
  const [source, setSource] = useState("");
  const [jobs, setJobs] = useState<ModelDownload[]>([]);
  const active = jobs.some((j) => ["preparing", "downloading", "verifying"].includes(j.status));
  const load = async () => setJobs((await actions.guard(() => get<ModelDownload[]>("/api/local-models/downloads"))) ?? []);
  useEffect(() => void load(), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!active) return;
    const t = setInterval(async () => {
      const next = (await actions.guard(() => get<ModelDownload[]>("/api/local-models/downloads"))) ?? [];
      setJobs(next);
      if (!next.some((j) => ["preparing", "downloading", "verifying"].includes(j.status))) void actions.refreshProviders(true);
    }, 1000);
    return () => clearInterval(t);
  }, [active, actions]);
  const start = async () => {
    if (!source.trim()) return;
    const job = await actions.guard(() => post<ModelDownload>("/api/local-models/downloads", { source: source.trim() }));
    if (job) {
      setSource("");
      await load();
    }
  };
  const gb = (n: number) => (n / 1e9).toFixed(2);
  return (
    <div className="form">
      <Field label={tr("モデルを追加（ダウンロード）")} hint={tr("ollama.com のモデルページのURL（例: https://ollama.com/<ユーザー>/<モデル>、:タグ で量子化を指定）か、Hugging Face の .gguf ファイルのリンク。Ollama のインストールは不要です。")}>
        <div className="row gap-sm">
          <input className="input grow" value={source} placeholder="https://ollama.com/richardyoung/qwythos-9b-abliterated"
                 onChange={(e) => setSource(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void start()} />
          <button className="btn" disabled={!source.trim()} onClick={() => void start()}>
            <Icon name="download" size={13} /> {tr("ダウンロード")}</button>
        </div>
      </Field>
      {jobs.map((j) => (
        <div key={j.id} className="download-row">
          <div className="row between gap-sm">
            <span className="ellipsis strong">{j.name}</span>
            <span className="muted small">
              {tr(DL_STATUS[j.status])}
              {j.total > 0 && j.status !== "done" ? ` ${gb(j.done)} / ${gb(j.total)} GB` : ""}
              {j.status === "done" ? ` ${gb(j.total)} GB` : ""}
            </span>
            {["preparing", "downloading"].includes(j.status) && (
              <button className="icon-btn sm" title={tr("中止")} onClick={async () => { await actions.guard(() => del(`/api/local-models/downloads/${j.id}`)); await load(); }}>
                <Icon name="x" size={13} />
              </button>
            )}
          </div>
          {j.total > 0 && j.status !== "error" && (
            <div className="download-bar"><span style={{ width: `${Math.min(100, (j.done / j.total) * 100)}%` }} /></div>
          )}
          {j.error && <div className="error-text">{tr(j.error)}</div>}
          {j.status === "done" && <div className="muted small">{tr("保存先:")}{j.path}{tr("（モデル一覧に追加されました）")}</div>}
          {j.status === "cancelled" && <div className="muted small">{tr("中止しました。同じURLで再開すると続きからダウンロードします。")}</div>}
        </div>
      ))}
    </div>
  );
}

function ProviderCard({ p }: { p: ProviderStatus }) {
  const { state, actions } = useStore();
  const conf = state.settings.providers[p.id] as Record<string, unknown> & { enabled: boolean; default_model: string };
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const save = (v: Record<string, unknown>) => actions.saveSettings({ providers: { [p.id]: v } });
  const login = async (method: string, api_key?: string) => {
    setBusy(true);
    const r = await actions.guard(() => post<{ ok: boolean; error?: string }>(`/api/providers/${p.id}/login`, { method, api_key }));
    setBusy(false);
    setKey("");
    if (r && !r.ok) actions.toast(r.error || tr("ログインできませんでした"), "error");
    if (r?.ok) actions.toast(method === "start_server" ? tr("ローカルサーバーを起動しました") : method === "unload" ? tr("モデルを解放しました") : tr("ログイン状態を更新しました"), "success");
    await actions.refreshProviders(true);
  };

  return (
    <div className="card">
      <div className="row between">
        <div className="row gap-sm">
          <span className={cx("dot", p.available && p.auth_ok ? "ok" : p.available ? "warn" : "bad")} />
          <span className="strong">{tr(p.name)}</span>
          {p.version && <span className="muted small">{p.version}</span>}
        </div>
        <Toggle checked={conf.enabled !== false} onChange={(v) => void save({ enabled: v })} label={tr("使用する")} />
      </div>
      <dl className="kv">
        <dt>{tr("アカウント")}</dt>
        <dd>{p.account || tr("未確認")}</dd>
      </dl>
      {p.detail && <p className="muted small">{tr(p.detail)}</p>}

      {p.id === "claude" && (
        <div className="form">
          <p className="muted small notice-inline">
            {tr("このアプリを他の人に配布して使ってもらう場合は、Anthropic の利用規約により、claude.ai のログイン（サブスクリプション）ではなく API キー方式を使ってください。配布用ビルド（scripts\\build.ps1 -Distribution）では API キー方式が既定になります。")}</p>
          <Field label={tr("認証方式")} hint={tr("既定は Claude Code CLI 自身のログイン（ご自身のサブスクリプションまたはAPI）。APIキーは Windows 資格情報マネージャーに保存され、ファイルには書き込まれません。")}>
            <div className="segmented">
              <button className={cx(conf.auth_mode !== "api_key" && "on")} onClick={() => void save({ auth_mode: "cli" })}>{tr("CLIのログインを使用")}</button>
              <button className={cx(conf.auth_mode === "api_key" && "on")} onClick={() => void save({ auth_mode: "api_key" })}>{tr("APIキー")}</button>
            </div>
          </Field>
          {conf.auth_mode === "api_key" ? (
            <div className="row gap-sm">
              <input className="input grow" type="password" placeholder={state.boot.secrets.anthropic ? tr("保存済み（変更する場合のみ入力）") : "sk-ant-..."} value={key} onChange={(e) => setKey(e.target.value)} />
              <button className="btn" disabled={!key || busy} onClick={() => void login("api_key", key)}>{tr("保存")}</button>
              <button className="btn ghost" onClick={async () => { await actions.guard(() => post(`/api/providers/claude/logout`)); await actions.refreshProviders(true); }}>{tr("削除")}</button>
            </div>
          ) : (
            <p className="muted small">{tr("ログインはターミナルで")} <code>claude</code> {tr("を起動し")} <code>/login</code> {tr("を実行してください（ご自身のアカウントで認証されます）。")}</p>
          )}
          <Field label={tr("権限モード")} hint={tr("ツール実行の承認方法。すべて確認なしで実行する場合は、上の「フルアクセスモード」を使います。")}>
            <select className="select" value={String(conf.permission_mode ?? "default")} onChange={(e) => void save({ permission_mode: e.target.value })}>
              <option value="default">{tr("標準（必要な操作ごとに確認）")}</option>
              <option value="acceptEdits">{tr("ファイル編集は自動承認")}</option>
              <option value="plan">{tr("計画モード（実行しない）")}</option>
            </select>
          </Field>
        </div>
      )}

      {p.id === "local" && (
        <div className="form">
          <p className="muted small">
            {tr("PC上のモデルで動かします。外部への送信はなく、ログインも不要です。AIの実行は Codex が担当するため、 Blender MCP・承認・中断はほかのAIと同じように使えます（Codex CLI のパス・サンドボックス・承認ポリシーは Codex の設定を共有）。")}</p>
          <Field label={tr("実行方法")}>
            <select className="select" value={String(conf.runtime ?? "builtin")} onChange={(e) => void save({ runtime: e.target.value, default_model: "" }).then(() => actions.refreshProviders(true))}>
              <option value="builtin">{tr("アプリ内蔵（llama.cpp）— 推奨")}</option>
              <option value="lmstudio">{tr("LM Studio のサーバーを使う")}</option>
              <option value="ollama">{tr("Ollama のサーバーを使う")}</option>
            </select>
          </Field>
          {(conf.runtime ?? "builtin") === "builtin" ? (
            <>
              <p className="muted small">
                {tr("GGUF 形式のモデルファイル（.gguf）をアプリが直接読み込みます。下のモデルフォルダのほか、LM Studio がダウンロード済みのモデルも自動で見つけます。 画像対応（mmproj ファイル付き）のモデルには画像も渡せます。")}</p>
              <div className="row gap-sm wrap">
                <button className="btn" onClick={() => void actions.guard(() => post("/api/system/reveal", { path: String(conf.models_dir || "") || `${state.boot.data_dir}\\models` }))}>
                  <Icon name="folder" size={13} /> {tr("モデルフォルダを開く")}</button>
                <button className="btn ghost" disabled={busy} onClick={() => void login("unload")} title={tr("GPUメモリを解放します（次の送信で再読み込み）")}>
                  {tr("モデルを解放")}</button>
                <button className="btn ghost" disabled={state.providersLoading} onClick={() => void actions.refreshProviders(true)}>
                  <Icon name="refresh" size={13} /> {tr("モデル一覧を更新")}</button>
              </div>
              <Field label={tr("モデルの保存場所")} hint={tr("指定した場合は、このフォルダと追加のモデルフォルダだけを検索します。空欄なら既定の保存先と LM Studio などを自動検索します。")}>
                <TextSetting browse="folder" value={String(conf.models_dir ?? "")} placeholder={`${state.boot.data_dir}\\models`}
                             onSave={(v) => void save({ models_dir: v.trim() }).then(() => actions.refreshProviders(true))} />
              </Field>
              <ModelDownloads />
              <Field label={tr("追加のモデルフォルダ（任意・1行に1つ）")}>
                <textarea className="input" rows={2} defaultValue={((conf.model_dirs as string[] | undefined) ?? []).join("\n")}
                          onBlur={(e) => void save({ model_dirs: e.target.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean) }).then(() => actions.refreshProviders(true))} />
              </Field>
              <Field label={tr("llama-server の場所（任意）")} hint={tr("空欄なら自動で探します。未インストールの場合は PowerShell で winget install ggml.llamacpp を実行してください。")}>
                <TextSetting browse="file" value={String(conf.llama_server_path ?? "")} placeholder={tr("自動検出")} onSave={(v) => void save({ llama_server_path: v.trim() }).then(() => actions.refreshProviders(true))} />
              </Field>
              <div className="row gap-sm">
                <Field label={tr("GPUに載せる層の数")} hint={tr("99 = すべて。VRAM不足で失敗する場合は下げる（0 = CPUのみ）")}>
                  <TextSetting type="number" value={Number(conf.gpu_layers ?? 99)} onSave={(v) => void save({ gpu_layers: Math.max(0, Number(v) || 0) })} />
                </Field>
                <Field label={tr("コンテキスト長")} hint={tr("Codex の指示とツール定義だけで約1万トークン使います")}>
                  <TextSetting type="number" value={Number(conf.ctx_size ?? 32768)} onSave={(v) => void save({ ctx_size: Math.max(8192, Number(v) || 32768) })} />
                </Field>
                <Field label={tr("自動解放（分）")} hint={tr("使わない時間がこれを超えるとGPUメモリを解放（0 = しない）")}>
                  <TextSetting type="number" value={Number(conf.idle_unload_minutes ?? 15)} onSave={(v) => void save({ idle_unload_minutes: Math.max(0, Number(v) || 0) })} />
                </Field>
              </div>
              <h3 className="sub">{tr("CPUオフロード")}</h3>
              <p className="muted small">
                {tr("VRAMに入りきらないモデルを、一部CPU（メインメモリ）で動かします。数値はすべて自由に指定でき、0 でオフです。 CPUに置くほど遅くなります。変更は次の送信時にモデルを読み込み直して反映します。")}</p>
              <div className="row gap-sm">
                <Field label={tr("CPUに置く層の数")} hint={tr("モデルの層のうち、この数をCPUで処理（指定すると「GPUに載せる層の数」より優先）")}>
                  <TextSetting type="number" value={Number(conf.cpu_layers ?? 0)} onSave={(v) => void save({ cpu_layers: Math.max(0, Math.floor(Number(v) || 0)) })} />
                </Field>
                <Field label={tr("MoEエキスパートをCPUに置く層の数")} hint={tr("MoEモデル用（--n-cpu-moe）。先頭からこの数の層のエキスパートをCPUへ")}>
                  <TextSetting type="number" value={Number(conf.n_cpu_moe ?? 0)} onSave={(v) => void save({ n_cpu_moe: Math.max(0, Math.floor(Number(v) || 0)) })} />
                </Field>
                <Field label={tr("CPUスレッド数")} hint={tr("0 = 自動")}>
                  <TextSetting type="number" value={Number(conf.threads ?? 0)} onSave={(v) => void save({ threads: Math.max(0, Math.floor(Number(v) || 0)) })} />
                </Field>
              </div>
              <Toggle checked={Boolean(conf.kv_on_cpu)} onChange={(v) => void save({ kv_on_cpu: v })}
                      label={tr("KVキャッシュをメインメモリに置く")}
                      hint={tr("長いコンテキストでVRAMが足りないときに使用（--no-kv-offload）。速度は落ちます。")} />
            </>
          ) : (
            <>
              <Field label={tr("サーバーのアドレス（任意）")} hint={tr("空欄の場合は既定（LM Studio: 127.0.0.1:1234 / Ollama: 127.0.0.1:11434）。")}>
                <TextSetting value={String(conf.base_url ?? "")} placeholder={conf.runtime === "ollama" ? "http://127.0.0.1:11434" : "http://127.0.0.1:1234"}
                             onSave={(v) => void save({ base_url: v.trim() }).then(() => actions.refreshProviders(true))} />
              </Field>
              <div className="row gap-sm wrap">
                {conf.runtime === "lmstudio" && (
                  <button className="btn" disabled={busy} onClick={() => void login("start_server")}>
                    {busy ? <span className="spinner" /> : <Icon name="plug" size={13} />} {tr("LM Studio サーバーを起動")}</button>
                )}
                <button className="btn ghost" disabled={state.providersLoading} onClick={() => void actions.refreshProviders(true)}>
                  <Icon name="refresh" size={13} /> {tr("モデル一覧を更新")}</button>
              </div>
            </>
          )}
          <Toggle checked={Boolean(conf.allow_shell)} onChange={(v) => void save({ allow_shell: v })}
                  label={tr("シェルコマンドの実行を許可する")}
                  hint={tr("小さいモデルは Blender MCP の代わりにシェルを使おうとして失敗しやすいため、既定ではオフです。")} />
          <p className="muted small">
            {tr("ツール呼び出しに対応したモデル（Qwen3 系 / gpt-oss など）を推奨します。小さいモデルはツールを呼ばずに結果を推測で答えたり、 Blender の操作を誤ったりすることがあります。重要な作業の前にはチェックポイントを保存してください。")}</p>
        </div>
      )}

      {p.id === "codex" && (
        <div className="form">
          <div className="row gap-sm wrap">
            <button className="btn" disabled={busy} onClick={() => void login("chatgpt")}>
              {busy ? <span className="spinner" /> : null} {tr("ChatGPTでログイン（ブラウザが開きます）")}</button>
            <button className="btn ghost" disabled={busy} onClick={async () => { await actions.guard(() => post(`/api/providers/codex/logout`)); await actions.refreshProviders(true); }}>
              {tr("ログアウト")}</button>
          </div>
          <div className="row gap-sm">
            <input className="input grow" type="password" placeholder={tr("OpenAI APIキーでログイン（Codexが保存します）")} value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn" disabled={!key || busy} onClick={() => void login("api_key", key)}>{tr("ログイン")}</button>
          </div>
          <Field label={tr("サンドボックス")} hint={tr("Codexがコマンドを実行するときの書き込み範囲。")}>
            <select className="select" value={String(conf.sandbox ?? "workspace-write")} onChange={(e) => void save({ sandbox: e.target.value })}>
              <option value="read-only">{tr("読み取りのみ")}</option>
              <option value="workspace-write">{tr("作業フォルダへの書き込みのみ（推奨）")}</option>
            </select>
          </Field>
          <Field label={tr("承認ポリシー")}>
            <select className="select" value={String(conf.approval_policy ?? "on-request")} onChange={(e) => void save({ approval_policy: e.target.value })}>
              <option value="on-request">{tr("必要に応じて確認（推奨）")}</option>
              <option value="untrusted">{tr("信頼されていない操作はすべて確認")}</option>
            </select>
          </Field>
        </div>
      )}

      <div className="form">
        <Field label={tr("既定のモデル")}>
          <select
            className="select"
            value={conf.default_model ?? ""}
            onChange={(e) => {
              const next = findModel(p.models, e.target.value);
              const effort = String(conf.default_effort ?? "");
              void save({ default_model: e.target.value, default_effort: next?.efforts.includes(effort) ? effort : "" });
            }}
          >
            {p.id === "codex" && <option value="">{tr("Codexの既定")}</option>}
            {p.id === "local" && <option value="">{tr("自動（最初のモデル）")}</option>}
            {p.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </Field>
        {(() => {
          const m = findModel(p.models, conf.default_model ?? "");
          if (!m?.efforts.length) return null;
          return (
            <Field label={tr("既定の思考レベル")} hint={tr("新しいチャットで使う思考レベル。入力欄の下で会話ごとに変更できます。")}>
              <select className="select" value={String(conf.default_effort ?? "")} onChange={(e) => void save({ default_effort: e.target.value })}>
                <option value="">{tr("モデルの既定")}{m.default_effort ? `（${effortLabel(m.default_effort)}）` : ""}</option>
                {m.efforts.map((e) => <option key={e} value={e}>{effortLabel(e)}</option>)}
              </select>
            </Field>
          );
        })()}
        {p.id !== "local" && (
          <Field label={tr("CLIのパス")} hint={tr("空欄の場合は PATH 上のコマンドを使用します。")}>
            <TextSetting browse="file" value={String(conf.cli_path ?? "")} placeholder={p.id === "claude" ? "claude" : "codex"} onSave={(v) => void save({ cli_path: v })} />
          </Field>
        )}
      </div>
    </div>
  );
}

export const FULL_ACCESS_WARNING = [
  "フルアクセスモードを有効にしますか？",
  "",
  "・Claude / Codex がコマンド実行・ファイル変更・Blender操作を、確認なしで行います",
  "・Codex のサンドボックスも解除され、PC上のあらゆるファイルにアクセスできます",
  "・AIの質問への回答や入力フォームだけは、引き続き確認が表示されます",
  "",
  "大切なファイルはバックアップし、必要に応じて Blender のチェックポイントを利用してください。",
].join("\n");

function FullAccessCard() {
  const { state, actions } = useStore();
  const on = Boolean(state.settings.approval.full_access);
  return (
    <div className={cx("card", on && "danger-card")}>
      <div className="row between">
        <div>
          <div className="strong">{tr("フルアクセスモード")}</div>
          <div className="muted small">
            {tr("AIのツール実行・コマンド・ファイル変更をすべて確認なしで許可し、Codex のサンドボックスを解除します （Claude: bypassPermissions / Codex: 承認なし・danger-full-access）。次の送信から適用されます。 「モデリングプロンプト」と「設定画生成」のチャットには適用されません。")}</div>
        </div>
        <Toggle
          checked={on}
          label={on ? tr("有効") : tr("無効")}
          onChange={(v) => {
            if (v && !window.confirm(FULL_ACCESS_WARNING.split("\n").map((line) => tr(line)).join("\n"))) return;
            void actions.saveSettings({ approval: { full_access: v } });
          }}
        />
      </div>
    </div>
  );
}

function AiTab() {
  const { state, actions } = useStore();
  return (
    <div className="form">
      <div className="row between">
        <p className="muted small">{tr("各AI（Claude / Codex）は公式CLIを通じて、ご自身のアカウントで利用します。")}</p>
        <button className="btn small" disabled={state.providersLoading} onClick={() => void actions.refreshProviders(true)}>
          {state.providersLoading ? <span className="spinner" /> : <Icon name="refresh" size={13} />} {tr("再確認")}</button>
      </div>
      <FullAccessCard />
      {state.providers.map((p) => <ProviderCard key={p.id} p={p} />)}
      {state.providers.length === 0 && <div className="muted">{tr("確認中…")}</div>}
    </div>
  );
}

function UnrealTab() {
  const { state, actions } = useStore();
  const u = (state.settings as unknown as { unreal?: Record<string, unknown> }).unreal ?? {};
  const save = (v: Record<string, unknown>) => actions.saveSettings({ unreal: v });
  return (
    <div className="form">
      <p className="muted small">
        {tr("Unreal Editor 内で動く MCP サーバー（エディタの MCP プラグイン）に接続します。ゲーム開発の操作は AI が直接この MCP を使って行い、 アプリは接続状態・ビューポート・選択中のアクタを表示します。Blender で作ったアセットは Blender MCP の書き出し機能で UE5 へ持ち込めます。")}</p>
      <div className="card">
        <dl className="kv">
          <dt>{tr("状態")}</dt><dd>{state.unreal.status}</dd>
          <dt>{tr("レベル")}</dt><dd>{state.unreal.editor?.level || "—"}</dd>
          <dt>{tr("ツールセット")}</dt><dd>{state.unreal.toolsets.length}</dd>
        </dl>
        {state.unreal.error && <div className="error-text">{tr(state.unreal.error)}</div>}
        <button className="btn small" onClick={() => void actions.guard(() => post("/api/unreal/reconnect"))}><Icon name="refresh" size={13} /> {tr("再接続")}</button>
      </div>
      <Toggle checked={u.enabled !== false} onChange={(v) => void save({ enabled: v })} label={tr("Unreal Engine 連携を使う")} />
      <Field label={tr("MCP サーバーのアドレス")} hint={tr("Unreal Editor の MCP プラグインのアドレス（既定: http://127.0.0.1:8000/mcp）")}>
        <TextSetting value={String(u.url ?? "")} placeholder="http://127.0.0.1:8000/mcp" onSave={(v) => void save({ url: v.trim() || "http://127.0.0.1:8000/mcp" })} />
      </Field>
      <Field label={tr("AIに渡すMCPサーバー名")} hint={tr("各CLIの設定と同じ名前にすると、ツールが重複しません")}>
        <TextSetting value={String(u.server_name ?? "unreal-mcp")} onSave={(v) => void save({ server_name: v.trim() || "unreal-mcp" })} />
      </Field>
      <Toggle checked={u.inject_into_ai !== false} onChange={(v) => void save({ inject_into_ai: v })} label={tr("Claude / Codex / ローカルLLM にこの MCP を渡す")} />
      <Toggle checked={u.monitor_enabled !== false} onChange={(v) => void save({ monitor_enabled: v })} label={tr("アプリから接続して状態・プレビューを表示する")} />
      <Toggle checked={u.auto_preview !== false} onChange={(v) => void save({ auto_preview: v })} label={tr("AI が UE を操作したらプレビューを自動更新する")} />
      <h3 className="sub">{tr("承認")}</h3>
      <Toggle checked={(state.settings.approval as Record<string, unknown>).auto_approve_unreal_readonly !== false}
              onChange={(v) => void actions.saveSettings({ approval: { auto_approve_unreal_readonly: v } })}
              label={tr("UE5 の読み取り操作は確認せずに実行")}
              hint={tr("ツールセットの一覧・説明と、Get / Is / Search / Find / Capture などで始まる読み取り専用の操作が対象です。選択の変更やスクリプト実行は含みません。")} />
      <Toggle checked={Boolean((state.settings.approval as Record<string, unknown>).auto_approve_unreal_mcp)}
              onChange={(v) => {
                if (v && !window.confirm(tr("UE5 MCP のフルアクセスを有効にしますか？\n\nAI が UE5 のレベル・アセット・ブループリントの作成、変更、削除やプレイ開始を、確認なしで行います。\n重要なプロジェクトはソース管理やバックアップを取ってから使ってください。"))) return;
                void actions.saveSettings({ approval: { auto_approve_unreal_mcp: v } });
              }}
              label={tr("UE5 MCP のツールはすべて確認せずに実行（UE5 フルアクセス）")}
              hint={tr("UE5 の操作だけを自動承認します。コマンド実行やファイル変更まで含めて承認を省く場合は、設定 → AI の「フルアクセスモード」を使います。")} />
    </div>
  );
}

function ExtraMcpCard({ s, index, servers }: { s: ExtraMcpServer; index: number; servers: ExtraMcpServer[] }) {
  const { state, actions } = useStore();
  const save = (v: Partial<ExtraMcpServer>) =>
    actions.saveSettings({ extra_mcp: { servers: servers.map((x, i) => (i === index ? { ...x, ...v } : x)) } });
  const status = state.extraMcp[s.name];
  return (
    <div className="card">
      <div className="row between">
        <strong>{s.label || s.name}</strong>
        <div className="row gap-sm">
          {status && <span className={cx("pill", status === "connected" ? "ok" : "off")}><span className="dot" /> {status === "connected" ? tr("接続中") : tr("未接続")}</span>}
          <button className="icon-btn sm" title={tr("削除")} onClick={() => {
            if (!window.confirm(tr("この MCP サーバーを一覧から削除しますか？（MCP 本体のファイルは削除しません）"))) return;
            void actions.saveSettings({ extra_mcp: { servers: servers.filter((_, i) => i !== index) } });
          }}><Icon name="trash" size={13} /></button>
        </div>
      </div>
      <Toggle checked={s.enabled !== false} onChange={(v) => void save({ enabled: v })} label={tr("Claude / Codex / ローカルLLM にこの MCP を渡す")} />
      <Field label={tr("表示名")}>
        <TextSetting value={s.label ?? ""} onSave={(v) => void save({ label: v.trim() })} />
      </Field>
      <Field label={tr("AIに渡すMCPサーバー名")} hint={tr("英数字・-・_ のみ。各CLIの設定と同じ名前にすると、ツールが重複しません")}>
        <TextSetting value={s.name} onSave={(v) => void save({ name: v.trim() })} />
      </Field>
      <Field label={tr("MCP のフォルダー")} hint={tr("フォルダー内の .venv にある実行ファイルを起動します（無ければ uv run）")}>
        <TextSetting value={s.server_dir ?? ""} browse="folder" onSave={(v) => void save({ server_dir: v.trim() })} />
      </Field>
      <Field label={tr("起動スクリプト名")} hint={tr("pyproject.toml の [project.scripts] の名前（例: roxy-painter-mcp）")}>
        <TextSetting value={s.script ?? ""} onSave={(v) => void save({ script: v.trim() })} />
      </Field>
      <Field label={tr("起動コマンド（任意）")} hint={tr("指定するとフォルダーより優先します。引数は空白区切りです")}>
        <div className="row gap-sm">
          <TextSetting value={s.command ?? ""} placeholder="C:\path\server.exe" onSave={(v) => void save({ command: v.trim() })} />
          <TextSetting value={(s.args ?? []).join(" ")} placeholder={tr("引数")} onSave={(v) => void save({ args: v.split(/\s+/).filter(Boolean) })} />
        </div>
      </Field>
      <Field label={tr("URL（HTTP の MCP の場合）")} hint={tr("指定すると、コマンドではなくこのアドレスに接続します")}>
        <TextSetting value={s.url ?? ""} placeholder="http://127.0.0.1:8000/mcp" onSave={(v) => void save({ url: v.trim() })} />
      </Field>
      <Field label={tr("状態確認ポート（任意）")} hint={tr("操作対象のアプリが待ち受けるポート。指定すると上部に接続状態を表示します")}>
        <TextSetting value={s.probe_port ?? ""} type="number" onSave={(v) => void save({ probe_port: v.trim() ? Number(v) : null })} />
      </Field>
      <Field label={tr("AIへの指示（任意）")} hint={tr("この MCP をいつ使うかを AI に伝える文です")}>
        <TextSetting value={s.prompt ?? ""} onSave={(v) => void save({ prompt: v.trim() })} />
      </Field>
      <Toggle checked={s.auto_approve_readonly !== false} onChange={(v) => void save({ auto_approve_readonly: v })}
              label={tr("読み取り操作は確認せずに実行")}
              hint={(s.read_only_prefixes ?? []).length
                ? tr`名前が ${(s.read_only_prefixes ?? []).join(" / ")} で始まるツールが対象です。`
                : tr("読み取り操作の判定ルールがありません。すべての操作で確認します。")} />
      <Toggle checked={Boolean(s.auto_approve_all)}
              onChange={(v) => {
                if (v && !window.confirm(tr("この MCP のツールをすべて確認なしで実行しますか？\n\nAI が対象アプリのデータの作成・変更・削除を確認なしで行います。"))) return;
                void save({ auto_approve_all: v });
              }}
              label={tr("この MCP のツールはすべて確認せずに実行")} />
    </div>
  );
}

function ExtraMcpTab() {
  const { state, actions } = useStore();
  const servers = state.settings.extra_mcp?.servers ?? [];
  return (
    <div className="form">
      <p className="muted small">
        {tr("Blender・UE5 以外の MCP サーバー（Substance 3D Painter など）を AI に渡します。操作は AI が直接 MCP を使って行います。 対象アプリ側のプラグインの導入は、各 MCP の説明に従ってください。変更は次の送信から反映されます。")}</p>
      {servers.map((s, i) => <ExtraMcpCard key={i} s={s} index={i} servers={servers} />)}
      {servers.length === 0 && <div className="muted">{tr("登録されている MCP サーバーはありません。")}</div>}
      <div>
        <button className="btn small" onClick={() => {
          let n = servers.length + 1;
          while (servers.some((s) => s.name === `mcp-${n}`)) n++;
          void actions.saveSettings({ extra_mcp: { servers: [...servers, { name: `mcp-${n}`, label: tr("新しい MCP"), enabled: false,
            read_only_prefixes: ["get_", "list_"], auto_approve_readonly: true }] } });
        }}><Icon name="plus" size={13} /> {tr("MCP サーバーを追加")}</button>
      </div>
    </div>
  );
}

function McpTab() {
  const { state, actions } = useStore();
  const m = state.settings.mcp;
  const a = state.settings.approval;
  const save = (v: Record<string, unknown>) => actions.saveSettings({ mcp: v });
  return (
    <div className="form">
      <div className="card">
        <dl className="kv">
          <dt>MCP</dt><dd>{state.blender.mcp}{state.blender.server?.version ? ` (${state.blender.server.name} ${state.blender.server.version})` : ""}</dd>
          <dt>Blender</dt><dd>{state.blender.blender}（{state.blender.endpoint}）</dd>
        </dl>
        {state.blender.error && <div className="error-text">{tr(state.blender.error)}</div>}
        <div className="row gap-sm wrap">
          <button className="btn small" onClick={() => void actions.guard(() => post("/api/blender/reconnect"))}><Icon name="refresh" size={13} /> {tr("再接続")}</button>
          <McpRestartButton />
        </div>
      </div>
      <Field label={tr("Roxy-BlenderMCP-v2 のフォルダ")} hint={tr("既存のMCPを優先します。未導入なら、アプリのデータフォルダへMCPと必要な依存関係を自動導入します。")}>
        <TextSetting browse="folder" value={m.server_dir} placeholder={tr("空欄なら自動検出・自動導入")} onSave={(v) => void save({ server_dir: v })} />
      </Field>
      <Field label={tr("起動コマンド（上級者向け・任意）")} hint={tr("指定すると上のフォルダ設定より優先します。引数はスペース区切り。")}>
        <TextSetting browse="file" value={m.command} placeholder={tr("例: uv")} onSave={(v) => void save({ command: v })} />
        <TextSetting value={m.args.join(" ")} placeholder={tr("例: run --no-sync --directory C:\path roxy-blender-mcp")} onSave={(v) => void save({ args: v.trim() ? v.trim().split(/\s+/) : [] })} />
      </Field>
      <Field label={tr("AIに渡すMCPサーバー名")}>
        <TextSetting value={m.server_name} onSave={(v) => void save({ server_name: v || "blender" })} />
      </Field>
      <Toggle checked={m.inject_into_ai} onChange={(v) => void save({ inject_into_ai: v })} label={tr("Claude / Codex にこのMCP設定を渡す")}
              hint={tr("オフにすると、各CLIに設定済みのMCPサーバー（~/.claude.json, ~/.codex/config.toml）をそのまま使います。")} />
      <Toggle checked={m.monitor_enabled} onChange={(v) => void save({ monitor_enabled: v })} label={tr("GUIからMCPに接続して状態・プレビューを表示する")} />
      <Toggle checked={m.auto_preview} onChange={(v) => void save({ auto_preview: v })} label={tr("シーンが変更されたらプレビューを自動更新する")} />
      <Field label={tr("プレビューの最大サイズ（px）")}>
        <TextSetting type="number" value={m.preview_max_size} onSave={(v) => void save({ preview_max_size: Math.min(2000, Math.max(200, Number(v) || 800)) })} />
      </Field>
      <h3 className="sub">{tr("承認")}</h3>
      <Toggle checked={a.auto_approve_readonly_mcp} onChange={(v) => void actions.saveSettings({ approval: { auto_approve_readonly_mcp: v } })}
              label={tr("MCPが読み取り専用と宣言したツールは確認せずに実行")} hint={tr("readOnlyHint が付いたツールのみ対象です。")} />
      <Toggle checked={a.auto_approve_blender_mcp} onChange={(v) => void actions.saveSettings({ approval: { auto_approve_blender_mcp: v } })}
              label={tr("Blender MCPのツールはすべて確認せずに実行")} hint={tr("シーンを変更するツールも自動で実行されます。必要に応じてチェックポイントを利用してください。")} />
    </div>
  );
}

function ProjectsTab() {
  const { state, actions } = useStore();
  const [selected, setSelected] = useState(state.activeProjectId);
  const [form, setForm] = useState<Partial<Project>>({});
  const p = state.projects.find((x) => x.id === selected);
  useEffect(() => setForm(p ?? {}), [p]);
  const set = (k: keyof Project, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    if (!p) return;
    await actions.guard(() => patch(`/api/projects/${p.id}`, {
      name: form.name, blend_path: form.blend_path, work_dir: form.work_dir,
      blender_host: form.blender_host, blender_port: Number(form.blender_port), notes: form.notes,
    }));
    await actions.refreshProjects();
    actions.toast(tr("プロジェクトを保存しました"), "success");
  };
  const create = async () => {
    const np = await actions.guard(() => post<Project>("/api/projects", { name: tr("新しいプロジェクト") }));
    if (np) {
      await actions.refreshProjects();
      setSelected(np.id);
    }
  };
  const remove = async () => {
    if (!p || !window.confirm(tr`プロジェクト「${tr(p.name)}」と、その会話をすべて削除しますか？`)) return;
    await actions.guard(() => del(`/api/projects/${p.id}`));
    await actions.refreshProjects();
    setSelected(state.activeProjectId);
  };

  return (
    <div className="split">
      <div className="split-list">
        {state.projects.map((x) => (
          <button key={x.id} className={cx("nav-item", x.id === selected && "active")} onClick={() => setSelected(x.id)}>
            <Icon name="folder" /> <span className="ellipsis">{x.name}</span>
            {x.id === state.activeProjectId && <span className="chip">{tr("使用中")}</span>}
          </button>
        ))}
        <button className="nav-item muted" onClick={() => void create()}><Icon name="plus" /> {tr("新規作成")}</button>
      </div>
      {p && (
        <div className="form grow">
          <Field label={tr("名前")}><input className="input" value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label={tr("関連付ける .blend ファイル")} hint={tr("AIに伝えるための関連情報です。ファイルの保存・読み込みは MCP（AI）が行います。")}>
            <div className="row gap-sm">
              <input className="input grow" value={form.blend_path ?? ""} placeholder="C:\path\to\scene.blend" onChange={(e) => set("blend_path", e.target.value)} />
              <BrowseButton kind="blend" value={form.blend_path ?? ""} onSelect={(path) => set("blend_path", path)} />
            </div>
          </Field>
          <Field label={tr("作業フォルダ")} hint={tr("AIのカレントディレクトリ。空欄の場合はアプリのデータフォルダ内に作成します。")}>
            <div className="row gap-sm">
              <input className="input grow" value={form.work_dir ?? ""} onChange={(e) => set("work_dir", e.target.value)} />
              <BrowseButton kind="folder" value={form.work_dir ?? ""} onSelect={(path) => set("work_dir", path)} />
            </div>
          </Field>
          <div className="row gap-sm">
            <Field label={tr("Blender ホスト")}><input className="input" value={form.blender_host ?? ""} onChange={(e) => set("blender_host", e.target.value)} /></Field>
            <Field label={tr("ポート")}><input className="input" type="number" value={form.blender_port ?? 9876} onChange={(e) => set("blender_port", Number(e.target.value))} /></Field>
          </div>
          <Field label={tr("メモ")}><textarea className="input" rows={3} value={form.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></Field>
          <div className="row gap-sm">
            <button className="btn primary" onClick={() => void save()}>{tr("保存")}</button>
            {p.id !== state.activeProjectId && <button className="btn" onClick={() => void actions.activateProject(p.id)}>{tr("このプロジェクトを開く")}</button>}
            <span className="grow" />
            <button className="btn ghost danger" onClick={() => void remove()}>{tr("削除")}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function LogsTab() {
  const { state, actions } = useStore();
  const [name, setName] = useState("app");
  const [lines, setLines] = useState<string[]>([]);
  const load = async () => {
    const r = await actions.guard(() => get<{ lines: string[] }>(`/api/logs/${name}?lines=300`));
    setLines(r?.lines ?? []);
  };
  useEffect(() => void load(), [name]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="form">
      <div className="row gap-sm">
        <div className="segmented">
          {[["app", tr("アプリ")], ["operations", tr("操作ログ")], ["mcp_monitor", "MCP"]].map(([id, label]) => (
            <button key={id} className={cx(name === id && "on")} onClick={() => setName(id)}>{label}</button>
          ))}
        </div>
        <button className="btn small" onClick={() => void load()}><Icon name="refresh" size={13} /> {tr("更新")}</button>
        <span className="grow" />
        <button className="btn small" onClick={() => void actions.guard(() => post("/api/system/reveal", { path: `${state.boot.data_dir}\\logs` }))}>
          <Icon name="folder" size={13} /> {tr("フォルダを開く")}</button>
      </div>
      <pre className="log-view">{lines.join("\n") || tr("（ログはまだありません）")}</pre>
      <p className="muted small">{tr("データフォルダ:")}{state.boot.data_dir} {tr("・ バージョン")}{state.boot.version}</p>
    </div>
  );
}

export function SettingsDialog({ initialTab, onClose, exiting = false }: { initialTab: SettingsTab; onClose: () => void; exiting?: boolean }) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className={cx("modal-scrim", exiting && "slide-out")} inert={exiting} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog settings" role="dialog" aria-modal="true">
        <div className="settings-nav">
          <div className="strong settings-title">{tr("設定")}</div>
          {TABS.map((t) => (
            <button key={t.id} className={cx("nav-item", tab === t.id && "active")} onClick={() => setTab(t.id)}>{tr(t.label)}</button>
          ))}
        </div>
        <div className="settings-body">
          <div className="row between settings-head">
            <h2>{tr(TABS.find((t) => t.id === tab)?.label)}</h2>
            <button className="icon-btn" onClick={onClose} title={tr("閉じる (Esc)")}><Icon name="x" /></button>
          </div>
          {tab === "general" && <General />}
          {tab === "ai" && <AiTab />}
          {tab === "mcp" && <McpTab />}
          {tab === "unreal" && <UnrealTab />}
          {tab === "extra" && <ExtraMcpTab />}
          {tab === "projects" && <ProjectsTab />}
          {tab === "logs" && <LogsTab />}
        </div>
      </div>
    </div>
  );
}
