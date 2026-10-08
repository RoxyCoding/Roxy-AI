import { useEffect, useState } from "react";
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
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
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
      <div className={cx("shell", sidebar.value && "with-sidebar", panel.value && "with-panel")}>
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
      {!state.wsConnected && <div className="offline-banner">バックエンドと再接続しています…</div>}
      <ApprovalDialog />
      {settings.value && <SettingsDialog exiting={settings.exiting} initialTab={settings.value} onClose={() => setSettingsTab(null)} />}
      <Toasts />
      <Lightbox />
    </LightboxProvider>
  );
}

export default function App() {
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
          <h1>バックエンドに接続できません</h1>
          <p className="muted">{error}</p>
          <div className="row gap">
            <button
              className="btn primary"
              onClick={async () => {
                await restartBackend().catch(() => undefined);
                setAttempt((n) => n + 1);
              }}
            >
              再起動して再試行
            </button>
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
          <p className="muted">Roxy Blender AI を起動しています…</p>
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
