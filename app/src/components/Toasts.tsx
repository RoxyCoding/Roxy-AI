import { useEffect, useState } from "react";
import { useStore, type Toast } from "../lib/store";
import { cx } from "../lib/format";
import { Icon } from "./Icons";

type Shown = Toast & { leaving?: boolean };
const EXIT_MS = 180; // matches .toast.leaving in styles.css

export function Toasts() {
  const { state, dispatch } = useStore();
  // Keep removed toasts briefly so they can slide out instead of vanishing.
  const [shown, setShown] = useState<Shown[]>(state.toasts);
  useEffect(() => {
    setShown((prev) => {
      const live = new Set(state.toasts.map((t) => t.id));
      const next: Shown[] = prev.map((t) => (live.has(t.id) ? t : { ...t, leaving: true }));
      for (const t of state.toasts) if (!prev.some((p) => p.id === t.id)) next.push(t);
      return next;
    });
    const timer = setTimeout(() => setShown((prev) => prev.filter((t) => !t.leaving)), EXIT_MS);
    return () => clearTimeout(timer);
  }, [state.toasts]);

  return (
    <div className="toasts" aria-live="polite">
      {shown.map((t) => (
        <div key={t.id} className={cx("toast", t.kind, t.leaving && "leaving")} inert={t.leaving}>
          <Icon name={t.kind === "error" ? "alert" : t.kind === "success" ? "check" : "chevron"} />
          <span className="toast-text">{t.text}</span>
          {t.action && (
            <button className="link-btn" onClick={t.action.run}>
              {t.action.label}
            </button>
          )}
          <button className="icon-btn sm" onClick={() => dispatch({ type: "untoast", id: t.id })} title="閉じる">
            <Icon name="x" />
          </button>
        </div>
      ))}
    </div>
  );
}
