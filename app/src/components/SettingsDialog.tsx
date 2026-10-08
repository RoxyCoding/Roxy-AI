import { useEffect, useState, type ReactNode } from "react";
import { del, get, patch, post } from "../lib/backend";
import { useStore } from "../lib/store";
import type { Project, ProviderStatus } from "../lib/types";
import { cx, effortLabel, findModel } from "../lib/format";
import { Icon } from "./Icons";
import { McpRestartButton } from "./McpRestartButton";

export type SettingsTab = "general" | "ai" | "mcp" | "projects" | "logs";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "general", label: "一般" },
  { id: "ai", label: "AI" },
  { id: "mcp", label: "Blender MCP" },
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
      {busy ? <span className="spinner" /> : <Icon name="folder" size={13} />} 参照
    </button>
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
      <Field label="テーマ">
        <div className="segmented">
          {(["system", "light", "dark"] as const).map((t) => (
            <button key={t} className={cx(s.theme === t && "on")} onClick={() => void actions.saveSettings({ theme: t })}>
              {t === "system" ? "システム" : t === "light" ? "ライト" : "ダーク"}
            </button>
          ))}
        </div>
      </Field>
      <Field label="新規チャットで使うAI">
        <select className="select" value={s.default_provider} onChange={(e) => void actions.saveSettings({ default_provider: e.target.value })}>
          {state.boot.providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </Field>
      <Field label="AI切り替え時に引き継ぐ会話の上限（文字数）" hint="別のAIへ切り替えたとき、会話内容をこの文字数まで渡します（内部セッションは引き継げません）。">
        <TextSetting type="number" value={s.context.handoff_max_chars} onSave={(v) => void actions.saveSettings({ context: { handoff_max_chars: Math.max(2000, Number(v) || 40000) } })} />
      </Field>
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
    if (r && !r.ok) actions.toast(r.error || "ログインできませんでした", "error");
    if (r?.ok) actions.toast("ログイン状態を更新しました", "success");
    await actions.refreshProviders(true);
  };

  return (
    <div className="card">
      <div className="row between">
        <div className="row gap-sm">
          <span className={cx("dot", p.available && p.auth_ok ? "ok" : p.available ? "warn" : "bad")} />
          <span className="strong">{p.name}</span>
          {p.version && <span className="muted small">{p.version}</span>}
        </div>
        <Toggle checked={conf.enabled !== false} onChange={(v) => void save({ enabled: v })} label="使用する" />
      </div>
      <dl className="kv">
        <dt>アカウント</dt>
        <dd>{p.account || "未確認"}</dd>
      </dl>
      {p.detail && <p className="muted small">{p.detail}</p>}

      {p.id === "claude" && (
        <div className="form">
          <Field label="認証方式" hint="既定は Claude Code CLI 自身のログイン（ご自身のサブスクリプションまたはAPI）。APIキーは Windows 資格情報マネージャーに保存され、ファイルには書き込まれません。">
            <div className="segmented">
              <button className={cx(conf.auth_mode !== "api_key" && "on")} onClick={() => void save({ auth_mode: "cli" })}>CLIのログインを使用</button>
              <button className={cx(conf.auth_mode === "api_key" && "on")} onClick={() => void save({ auth_mode: "api_key" })}>APIキー</button>
            </div>
          </Field>
          {conf.auth_mode === "api_key" ? (
            <div className="row gap-sm">
              <input className="input grow" type="password" placeholder={state.boot.secrets.anthropic ? "保存済み（変更する場合のみ入力）" : "sk-ant-..."} value={key} onChange={(e) => setKey(e.target.value)} />
              <button className="btn" disabled={!key || busy} onClick={() => void login("api_key", key)}>保存</button>
              <button className="btn ghost" onClick={async () => { await actions.guard(() => post(`/api/providers/claude/logout`)); await actions.refreshProviders(true); }}>削除</button>
            </div>
          ) : (
            <p className="muted small">ログインはターミナルで <code>claude</code> を起動し <code>/login</code> を実行してください（ご自身のアカウントで認証されます）。</p>
          )}
          <Field label="権限モード" hint="ツール実行の承認方法。すべて確認なしで実行する場合は、上の「フルアクセスモード」を使います。">
            <select className="select" value={String(conf.permission_mode ?? "default")} onChange={(e) => void save({ permission_mode: e.target.value })}>
              <option value="default">標準（必要な操作ごとに確認）</option>
              <option value="acceptEdits">ファイル編集は自動承認</option>
              <option value="plan">計画モード（実行しない）</option>
            </select>
          </Field>
        </div>
      )}

      {p.id === "codex" && (
        <div className="form">
          <div className="row gap-sm wrap">
            <button className="btn" disabled={busy} onClick={() => void login("chatgpt")}>
              {busy ? <span className="spinner" /> : null} ChatGPTでログイン（ブラウザが開きます）
            </button>
            <button className="btn ghost" disabled={busy} onClick={async () => { await actions.guard(() => post(`/api/providers/codex/logout`)); await actions.refreshProviders(true); }}>
              ログアウト
            </button>
          </div>
          <div className="row gap-sm">
            <input className="input grow" type="password" placeholder="OpenAI APIキーでログイン（Codexが保存します）" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn" disabled={!key || busy} onClick={() => void login("api_key", key)}>ログイン</button>
          </div>
          <Field label="サンドボックス" hint="Codexがコマンドを実行するときの書き込み範囲。">
            <select className="select" value={String(conf.sandbox ?? "workspace-write")} onChange={(e) => void save({ sandbox: e.target.value })}>
              <option value="read-only">読み取りのみ</option>
              <option value="workspace-write">作業フォルダへの書き込みのみ（推奨）</option>
            </select>
          </Field>
          <Field label="承認ポリシー">
            <select className="select" value={String(conf.approval_policy ?? "on-request")} onChange={(e) => void save({ approval_policy: e.target.value })}>
              <option value="on-request">必要に応じて確認（推奨）</option>
              <option value="untrusted">信頼されていない操作はすべて確認</option>
            </select>
          </Field>
        </div>
      )}

      <div className="form">
        <Field label="既定のモデル">
          <select
            className="select"
            value={conf.default_model ?? ""}
            onChange={(e) => {
              const next = findModel(p.models, e.target.value);
              const effort = String(conf.default_effort ?? "");
              void save({ default_model: e.target.value, default_effort: next?.efforts.includes(effort) ? effort : "" });
            }}
          >
            {p.id === "codex" && <option value="">Codexの既定</option>}
            {p.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </Field>
        {(() => {
          const m = findModel(p.models, conf.default_model ?? "");
          if (!m?.efforts.length) return null;
          return (
            <Field label="既定の思考レベル" hint="新しいチャットで使う思考レベル。入力欄の下で会話ごとに変更できます。">
              <select className="select" value={String(conf.default_effort ?? "")} onChange={(e) => void save({ default_effort: e.target.value })}>
                <option value="">モデルの既定{m.default_effort ? `（${effortLabel(m.default_effort)}）` : ""}</option>
                {m.efforts.map((e) => <option key={e} value={e}>{effortLabel(e)}</option>)}
              </select>
            </Field>
          );
        })()}
        <Field label="CLIのパス" hint="空欄の場合は PATH 上のコマンドを使用します。">
          <TextSetting browse="file" value={String(conf.cli_path ?? "")} placeholder={p.id === "claude" ? "claude" : "codex"} onSave={(v) => void save({ cli_path: v })} />
        </Field>
      </div>
    </div>
  );
}

export const FULL_ACCESS_WARNING = [
  "フルアクセスモードを有効にしますか？",
  "",
  "・Claude Code / Codex がコマンド実行・ファイル変更・Blender操作を、確認なしで行います",
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
          <div className="strong">フルアクセスモード</div>
          <div className="muted small">
            AIのツール実行・コマンド・ファイル変更をすべて確認なしで許可し、Codex のサンドボックスを解除します
            （Claude: bypassPermissions / Codex: 承認なし・danger-full-access）。次の送信から適用されます。
            「モデリングプロンプト」チャットには適用されません。
          </div>
        </div>
        <Toggle
          checked={on}
          label={on ? "有効" : "無効"}
          onChange={(v) => {
            if (v && !window.confirm(FULL_ACCESS_WARNING)) return;
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
        <p className="muted small">各AIは公式CLI（Claude Code / Codex）を通じて、ご自身のアカウントで利用します。</p>
        <button className="btn small" disabled={state.providersLoading} onClick={() => void actions.refreshProviders(true)}>
          {state.providersLoading ? <span className="spinner" /> : <Icon name="refresh" size={13} />} 再確認
        </button>
      </div>
      <FullAccessCard />
      {state.providers.map((p) => <ProviderCard key={p.id} p={p} />)}
      {state.providers.length === 0 && <div className="muted">確認中…</div>}
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
        {state.blender.error && <div className="error-text">{state.blender.error}</div>}
        <div className="row gap-sm wrap">
          <button className="btn small" onClick={() => void actions.guard(() => post("/api/blender/reconnect"))}><Icon name="refresh" size={13} /> 再接続</button>
          <McpRestartButton />
        </div>
      </div>
      <Field label="Roxy-BlenderMCP-v2 のフォルダ" hint="このフォルダの .venv にある roxy-blender-mcp を起動します（無い場合は uv run）。MCPのソースは変更しません。">
        <TextSetting browse="folder" value={m.server_dir} placeholder="I:\Blender\MCP\Roxy-BlenderMCP-v2" onSave={(v) => void save({ server_dir: v })} />
      </Field>
      <Field label="起動コマンド（上級者向け・任意）" hint="指定すると上のフォルダ設定より優先します。引数はスペース区切り。">
        <TextSetting browse="file" value={m.command} placeholder="例: uv" onSave={(v) => void save({ command: v })} />
        <TextSetting value={m.args.join(" ")} placeholder="例: run --no-sync --directory C:\path roxy-blender-mcp" onSave={(v) => void save({ args: v.trim() ? v.trim().split(/\s+/) : [] })} />
      </Field>
      <Field label="AIに渡すMCPサーバー名">
        <TextSetting value={m.server_name} onSave={(v) => void save({ server_name: v || "blender" })} />
      </Field>
      <Toggle checked={m.inject_into_ai} onChange={(v) => void save({ inject_into_ai: v })} label="Claude Code / Codex にこのMCP設定を渡す"
              hint="オフにすると、各CLIに設定済みのMCPサーバー（~/.claude.json, ~/.codex/config.toml）をそのまま使います。" />
      <Toggle checked={m.monitor_enabled} onChange={(v) => void save({ monitor_enabled: v })} label="GUIからMCPに接続して状態・プレビューを表示する" />
      <Toggle checked={m.auto_preview} onChange={(v) => void save({ auto_preview: v })} label="シーンが変更されたらプレビューを自動更新する" />
      <Field label="プレビューの最大サイズ（px）">
        <TextSetting type="number" value={m.preview_max_size} onSave={(v) => void save({ preview_max_size: Math.min(2000, Math.max(200, Number(v) || 800)) })} />
      </Field>
      <h3 className="sub">承認</h3>
      <Toggle checked={a.auto_approve_readonly_mcp} onChange={(v) => void actions.saveSettings({ approval: { auto_approve_readonly_mcp: v } })}
              label="MCPが読み取り専用と宣言したツールは確認せずに実行" hint="readOnlyHint が付いたツールのみ対象です。" />
      <Toggle checked={a.auto_approve_blender_mcp} onChange={(v) => void actions.saveSettings({ approval: { auto_approve_blender_mcp: v } })}
              label="Blender MCPのツールはすべて確認せずに実行" hint="シーンを変更するツールも自動で実行されます。必要に応じてチェックポイントを利用してください。" />
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
    actions.toast("プロジェクトを保存しました", "success");
  };
  const create = async () => {
    const np = await actions.guard(() => post<Project>("/api/projects", { name: "新しいプロジェクト" }));
    if (np) {
      await actions.refreshProjects();
      setSelected(np.id);
    }
  };
  const remove = async () => {
    if (!p || !window.confirm(`プロジェクト「${p.name}」と、その会話をすべて削除しますか？`)) return;
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
            {x.id === state.activeProjectId && <span className="chip">使用中</span>}
          </button>
        ))}
        <button className="nav-item muted" onClick={() => void create()}><Icon name="plus" /> 新規作成</button>
      </div>
      {p && (
        <div className="form grow">
          <Field label="名前"><input className="input" value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="関連付ける .blend ファイル" hint="AIに伝えるための関連情報です。ファイルの保存・読み込みは MCP（AI）が行います。">
            <div className="row gap-sm">
              <input className="input grow" value={form.blend_path ?? ""} placeholder="C:\path\to\scene.blend" onChange={(e) => set("blend_path", e.target.value)} />
              <BrowseButton kind="blend" value={form.blend_path ?? ""} onSelect={(path) => set("blend_path", path)} />
            </div>
          </Field>
          <Field label="作業フォルダ" hint="AIのカレントディレクトリ。空欄の場合はアプリのデータフォルダ内に作成します。">
            <div className="row gap-sm">
              <input className="input grow" value={form.work_dir ?? ""} onChange={(e) => set("work_dir", e.target.value)} />
              <BrowseButton kind="folder" value={form.work_dir ?? ""} onSelect={(path) => set("work_dir", path)} />
            </div>
          </Field>
          <div className="row gap-sm">
            <Field label="Blender ホスト"><input className="input" value={form.blender_host ?? ""} onChange={(e) => set("blender_host", e.target.value)} /></Field>
            <Field label="ポート"><input className="input" type="number" value={form.blender_port ?? 9876} onChange={(e) => set("blender_port", Number(e.target.value))} /></Field>
          </div>
          <Field label="メモ"><textarea className="input" rows={3} value={form.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></Field>
          <div className="row gap-sm">
            <button className="btn primary" onClick={() => void save()}>保存</button>
            {p.id !== state.activeProjectId && <button className="btn" onClick={() => void actions.activateProject(p.id)}>このプロジェクトを開く</button>}
            <span className="grow" />
            <button className="btn ghost danger" onClick={() => void remove()}>削除</button>
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
          {[["app", "アプリ"], ["operations", "操作ログ"], ["mcp_monitor", "MCP"]].map(([id, label]) => (
            <button key={id} className={cx(name === id && "on")} onClick={() => setName(id)}>{label}</button>
          ))}
        </div>
        <button className="btn small" onClick={() => void load()}><Icon name="refresh" size={13} /> 更新</button>
        <span className="grow" />
        <button className="btn small" onClick={() => void actions.guard(() => post("/api/system/reveal", { path: `${state.boot.data_dir}\\logs` }))}>
          <Icon name="folder" size={13} /> フォルダを開く
        </button>
      </div>
      <pre className="log-view">{lines.join("\n") || "（ログはまだありません）"}</pre>
      <p className="muted small">データフォルダ: {state.boot.data_dir} ・ バージョン {state.boot.version}</p>
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
          <div className="strong settings-title">設定</div>
          {TABS.map((t) => (
            <button key={t.id} className={cx("nav-item", tab === t.id && "active")} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </div>
        <div className="settings-body">
          <div className="row between settings-head">
            <h2>{TABS.find((t) => t.id === tab)?.label}</h2>
            <button className="icon-btn" onClick={onClose} title="閉じる (Esc)"><Icon name="x" /></button>
          </div>
          {tab === "general" && <General />}
          {tab === "ai" && <AiTab />}
          {tab === "mcp" && <McpTab />}
          {tab === "projects" && <ProjectsTab />}
          {tab === "logs" && <LogsTab />}
        </div>
      </div>
    </div>
  );
}
