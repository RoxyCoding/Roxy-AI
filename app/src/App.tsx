import { t as tr } from "./lib/i18n";
import { useLanguage } from "./lib/i18n";
import { useEffect, useRef, useState } from "react";
import { discoverBackend, get, restartBackend } from "./lib/backend";
import { StoreProvider, useStore } from "./lib/store";
import type { Bootstrap } from "./lib/types";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { ChatView } from "./components/ChatView";
import { RightPanel } from "./components/RightPanel";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { SettingsDialog, type SettingsTab } from "./components/SettingsDialog";
import { Toasts } from "./components/Toasts";
import { Lightbox, LightboxProvider } from "./components/Lightbox";
import { cx } from "./lib/format";
import { useSlide } from "./components/useSlide";

function useTheme(theme: string) {
  const first = useRef(true);
  useEffect(() => {
    const root = document.documentElement;
    // Cross-fade colours when the user switches theme (not on first paint).
    if (!first.current) root.classList.add("theme-anim");
    first.current = false;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    const t = setTimeout(() => root.classList.remove("theme-anim"), 320);
    return () => clearTimeout(t);
  }, [theme]);
}

function Shell() {
  const { state, actions } = useStore();
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(state.settings.ui.sidebar_open);
  const [panelOpen, setPanelOpen] = useState(state.settings.ui.right_panel_open);
  const sidebar = useSlide(sidebarOpen ? true : null);
  const panel = useSlide(panelOpen ? true : null);
  const settings = useSlide(settingsTab);
  useTheme(state.settings.theme);

  const togglePanel = () => {
    const v = !panelOpen;
    setPanelOpen(v);
    void actions.saveSettings({ ui: { right_panel_open: v } });
  };
  const toggleSidebar = () => {
    const v = !sidebarOpen;
    setSidebarOpen(v);
    void actions.saveSettings({ ui: { sidebar_open: v } });
  };

  return (
    <LightboxProvider>
      {/* Columns follow the open state, so a closing panel animates its width to 0 while still mounted. */}
      <div className={cx("shell", sidebarOpen && "with-sidebar", panelOpen && "with-panel")}>
        {sidebar.value && <Sidebar exiting={sidebar.exiting} onSettings={(tab) => setSettingsTab(tab ?? "general")} onClose={toggleSidebar} />}
        <main className="main">
          <TopBar
            sidebarOpen={sidebarOpen}
            panelOpen={panelOpen}
            onToggleSidebar={toggleSidebar}
            onTogglePanel={togglePanel}
            onSettings={(tab) => setSettingsTab(tab)}
          />
          <ChatView onSettings={(tab) => setSettingsTab(tab)} />
        </main>
        {panel.value && <RightPanel exiting={panel.exiting} onClose={togglePanel} onSettings={(tab) => setSettingsTab(tab)} />}
        {sidebar.value && <div className={cx("scrim mobile-only", sidebar.exiting && "slide-out")} inert={sidebar.exiting} onClick={toggleSidebar} />}
      </div>
      {!state.wsConnected && <div className="offline-banner">{tr("バックエンドと再接続しています…")}</div>}
      <ApprovalDialog />
      {settings.value && <SettingsDialog exiting={settings.exiting} initialTab={settings.value} onClose={() => setSettingsTab(null)} />}
      <Toasts />
      <Lightbox />
    </LightboxProvider>
  );
}

export default function App() {
  useLanguage();
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    (async () => {
      try {
        await discoverBackend();
        const b = await get<Bootstrap>("/api/bootstrap");
        if (alive) setBoot(b);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (error) {
    return (
      <div className="splash">
        <div className="splash-card">
          <img className="brand-icon" src="/roxy-icon.png" alt="Roxy" />
          <h1>{tr("バックエンドに接続できません")}</h1>
          <p className="muted">{error}</p>
          <div className="row gap">
            <button
              className="btn primary"
              onClick={async () => {
                await restartBackend().catch(() => undefined);
                setAttempt((n) => n + 1);
              }}
            >
              {tr("再起動して再試行")}</button>
          </div>
        </div>
      </div>
    );
  }
  if (!boot) {
    return (
      <div className="splash">
        <div className="splash-card">
          <img className="brand-icon pulse" src="/roxy-icon.png" alt="Roxy" />
          <p className="muted">{tr("Roxy AI を起動しています…")}</p>
        </div>
      </div>
    );
  }
  return (
    <StoreProvider boot={boot}>
      <Shell />
    </StoreProvider>
  );
}
