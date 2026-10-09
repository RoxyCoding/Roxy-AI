import { useEffect, useRef, useState } from "react";
import { get } from "../lib/backend";
import { providerName, useStore } from "../lib/store";
import { cx } from "../lib/format";
import { Icon } from "./Icons";

interface UsageWindow {
  key: string;
  label: string;
  used_percent: number;
  resets_at: number | null;
  reset_text: string;
}

interface UsageInfo {
  provider: string;
  available: boolean;
  windows: UsageWindow[];
  note: string;
  extra: string;
  raw?: string;
  fetched_at: number;
}

const REFRESH_MS = 5 * 60 * 1000;

function resetLabel(w: UsageWindow): string {
  if (!w.resets_at) return w.reset_text ? `リセット: ${w.reset_text}` : "";
  const at = new Date(w.resets_at * 1000);
  const mins = Math.max(0, Math.round((w.resets_at * 1000 - Date.now()) / 60000));
  const left = mins >= 1440 ? `${Math.floor(mins / 1440)}日${Math.floor((mins % 1440) / 60)}時間` : mins >= 60 ? `${Math.floor(mins / 60)}時間${mins % 60}分` : `${mins}分`;
  const when = at.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${when} にリセット（あと${left}）`;
}

/** Remaining = 100 - used. The tightest window is what limits you next. */
function tightest(info: UsageInfo | undefined): UsageWindow | undefined {
  return info?.windows.reduce<UsageWindow | undefined>((a, w) => (!a || w.used_percent > a.used_percent ? w : a), undefined);
}

function level(remaining: number): string {
  return remaining <= 10 ? "bad" : remaining <= 30 ? "warn" : "ok";
}

export function UsageBadge() {
  const { state } = useStore();
  const [usage, setUsage] = useState<UsageInfo[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const runCount = Object.keys(state.runs).length;
  const prevRuns = useRef(runCount);

  const load = async (refresh = false) => {
    setLoading(true);
    try {
      setUsage(await get<UsageInfo[]>(`/api/usage${refresh ? "?refresh=1" : ""}`));
    } catch {
      /* keep the last numbers; the badge simply goes stale */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(true), REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  // A finished response used quota: re-read shortly after.
  useEffect(() => {
    if (runCount < prevRuns.current) {
      const t = setTimeout(() => void load(true), 2000);
      prevRuns.current = runCount;
      return () => clearTimeout(t);
    }
    prevRuns.current = runCount;
  }, [runCount]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!usage.length) return null;
  const current = usage.find((u) => u.provider === state.draftProvider) ?? usage[0];
  const w = tightest(current);
  const remaining = w ? Math.max(0, 100 - w.used_percent) : null;

  return (
    <div className="usage" ref={box}>
      <button
        className={cx("pill usage-pill", remaining !== null && level(remaining))}
        title="残り利用枠（クリックで詳細）"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="dot" />
        {providerName(state, current.provider)} 残り{remaining !== null ? `${Math.round(remaining)}%` : "—"}
      </button>
      {open && (
        <div className="usage-pop">
          <div className="row between">
            <span className="strong">残り利用枠</span>
            <button className="icon-btn sm" title="更新" disabled={loading} onClick={() => void load(true)}>
              {loading ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
            </button>
          </div>
          {usage.map((u) => (
            <div key={u.provider} className="usage-provider">
              <div className="strong small">{providerName(state, u.provider)}</div>
              {u.windows.map((win) => {
                const rem = Math.max(0, 100 - win.used_percent);
                return (
                  <div key={win.key} className="usage-window">
                    <div className="row between small">
                      <span>{win.label}</span>
                      <span className={cx("usage-rem", level(rem))}>残り {Math.round(rem)}%</span>
                    </div>
                    <div className="download-bar"><span className={level(rem)} style={{ width: `${rem}%` }} /></div>
                    <div className="muted small">{resetLabel(win)}</div>
                  </div>
                );
              })}
              {u.note && <div className="muted small">{u.note}</div>}
              {u.raw && <pre className="mini-pre usage-raw">{u.raw}</pre>}
              {u.extra && <div className="muted small">{u.extra}</div>}
            </div>
          ))}
          <div className="muted small">
            {new Date(Math.max(...usage.map((u) => u.fetched_at)) * 1000).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })} 時点
          </div>
        </div>
      )}
    </div>
  );
}
