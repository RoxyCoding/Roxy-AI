import { useStore } from "../lib/store";
import { Icon } from "./Icons";

export function Toasts() {
  const { state, dispatch } = useStore();
  return (
    <div className="toasts" aria-live="polite">
      {state.toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
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
