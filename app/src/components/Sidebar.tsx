import { t as tr } from "../lib/i18n";
import { useEffect, useRef, useState } from "react";
import { get } from "../lib/backend";
import { useStore } from "../lib/store";
import type { Conversation, SearchHit } from "../lib/types";
import { cx, relativeTime } from "../lib/format";
import { Icon } from "./Icons";
import type { SettingsTab } from "./SettingsDialog";
import { useSlide } from "./useSlide";

function ConversationRow({ c, active }: { c: Conversation; active: boolean }) {
  const { state, actions } = useStore();
  const [menu, setMenu] = useState(false);
  const slidingMenu = useSlide(menu ? true : null);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(c.title);
  const running = Boolean(state.runs[c.id]);
  const waiting = state.approvals.some((a) => a.conversation_id === c.id);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menu]);

  if (renaming) {
    return (
      <form
        className="conv-row renaming"
        onSubmit={(e) => {
          e.preventDefault();
          setRenaming(false);
          if (title.trim() && title !== c.title) void actions.renameConversation(c.id, title.trim());
        }}
      >
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => setRenaming(false)} />
      </form>
    );
  }

  return (
    <div className={cx("conv-row", active && "active")} onClick={() => void actions.openConversation(c.id)}>
      <div className="conv-title">
        {(running || waiting) && <span className={cx("dot", waiting ? "warn" : "live")} title={waiting ? tr("承認待ち") : tr("応答中")} />}
        <span className="ellipsis">{c.title || tr("新しいチャット")}</span>
      </div>
      <div className="conv-meta">
        {c.mode === "prompt" && <span>{tr("プロンプト")}</span>}
        {c.mode === "design_prompt" && <span>{tr("設定画")}</span>}
        <span>{relativeTime(c.updated_at)}</span>
      </div>
      <div className="conv-actions" ref={menuRef} onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn sm" title={tr("メニュー")} onClick={() => setMenu((v) => !v)}>
          <Icon name="more" />
        </button>
        {slidingMenu.value && (
          <div className={cx("menu", slidingMenu.exiting && "slide-out")} inert={slidingMenu.exiting}>
            <button onClick={() => { setMenu(false); setTitle(c.title); setRenaming(true); }}>
              <Icon name="edit" /> {tr("名前を変更")}</button>
            <button onClick={() => { setMenu(false); void actions.exportConversation(c.id, "md"); }}>
              <Icon name="download" /> {tr("Markdownでエクスポート")}</button>
            <button onClick={() => { setMenu(false); void actions.exportConversation(c.id, "json"); }}>
              <Icon name="download" /> {tr("JSONでエクスポート")}</button>
            <button
              className="danger"
              onClick={() => {
                setMenu(false);
                if (window.confirm(tr`「${c.title || tr("新しいチャット")}」を削除しますか？添付ファイルも削除され、元に戻せません。`))
                  void actions.deleteConversation(c.id);
              }}
            >
              <Icon name="trash" /> {tr("削除")}</button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Sidebar({ onSettings, onClose, exiting = false }: { onSettings: (tab?: SettingsTab) => void; onClose: () => void; exiting?: boolean }) {
  const { state, actions } = useStore();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const project = state.projects.find((p) => p.id === state.activeProjectId);

  useEffect(() => {
    if (!query.trim()) {
      setHits(null);
      return;
    }
    const t = setTimeout(async () => {
      const r = await actions.guard(() => get<SearchHit[]>(`/api/search?q=${encodeURIComponent(query.trim())}`));
      setHits(r ?? []);
    }, 250);
    return () => clearTimeout(t);
  }, [query, actions]);

  const openHit = async (h: SearchHit) => {
    if (h.project_id !== state.activeProjectId) await actions.activateProject(h.project_id);
    await actions.openConversation(h.conversation_id);
    setQuery("");
  };

  return (
    <aside className={cx("sidebar", exiting && "slide-out")} inert={exiting}>
      <div className="sidebar-head">
        <div className="brand">
          <img className="brand-logo" src="/roxy-logo.png" alt="Roxy" />
        </div>
        <button className="icon-btn" title={tr("サイドバーを閉じる")} onClick={onClose}>
          <Icon name="sidebar" />
        </button>
      </div>

      <button className="nav-item strong" onClick={() => void actions.openConversation(null)}>
        <Icon name="plus" /> {tr("新規チャット")}</button>
      <button className="nav-item" onClick={() => void actions.openConversation(null, "prompt")}>
        <Icon name="edit" /> {tr("モデリングプロンプト")}</button>
      <button className="nav-item" onClick={() => void actions.openConversation(null, "design_prompt")}>
        <Icon name="image" /> {tr("設定画生成")}</button>

      <label className="search">
        <Icon name="search" />
        <input placeholder={tr("履歴を検索")} value={query} onChange={(e) => setQuery(e.target.value)} />
        {query && (
          <button className="icon-btn sm" onClick={() => setQuery("")} title={tr("クリア")}>
            <Icon name="x" />
          </button>
        )}
      </label>

      {hits !== null ? (
        <div className="sidebar-scroll">
          <div className="section-label">{tr("検索結果（")}{hits.length}）</div>
          {hits.length === 0 && <div className="empty-note">{tr("一致する会話はありません")}</div>}
          {hits.map((h) => (
            <button key={h.conversation_id} className="hit" onClick={() => void openHit(h)}>
              <span className="ellipsis strong">{h.title || tr("新しいチャット")}</span>
              {h.snippet && <span className="hit-snippet">{h.snippet.slice(0, 90)}</span>}
              <span className="hit-meta">
                {state.projects.find((p) => p.id === h.project_id)?.name} {tr("・")}{relativeTime(h.updated_at)}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className="section-label row between">
            <button className="link-btn" onClick={() => setProjectsOpen((v) => !v)}>
              <Icon name={projectsOpen ? "down" : "chevron"} size={12} /> {tr("プロジェクト")}</button>
            <button className="icon-btn sm" title={tr("プロジェクトを管理")} onClick={() => onSettings("projects")}>
              <Icon name="settings" size={14} />
            </button>
          </div>
          {projectsOpen ? (
            <div className="project-list">
              {state.projects.map((p) => (
                <button
                  key={p.id}
                  className={cx("nav-item", p.id === state.activeProjectId && "active")}
                  onClick={() => void actions.activateProject(p.id)}
                >
                  <Icon name="folder" />
                  <span className="ellipsis">{p.name}</span>
                </button>
              ))}
              <button className="nav-item muted" onClick={() => onSettings("projects")}>
                <Icon name="plus" /> {tr("新しいプロジェクト")}</button>
            </div>
          ) : (
            <button className="nav-item active-project" onClick={() => setProjectsOpen(true)}>
              <Icon name="folder" />
              <span className="ellipsis">{project?.name}</span>
            </button>
          )}

          <div className="section-label">{tr("会話履歴")}</div>
          <div className="sidebar-scroll">
            {state.conversations.length === 0 && <div className="empty-note">{tr("まだ会話はありません")}</div>}
            {state.conversations.map((c) => (
              <ConversationRow key={c.id} c={c} active={c.id === state.currentId} />
            ))}
          </div>
        </>
      )}

      <div className="sidebar-foot">
        <button className="nav-item" onClick={() => onSettings("general")}>
          <Icon name="settings" /> {tr("設定")}</button>
        <div className="foot-note">v{state.boot.version}</div>
      </div>
    </aside>
  );
}
