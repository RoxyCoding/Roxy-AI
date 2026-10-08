import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icons";

type Img = { src: string; name?: string };
const Ctx = createContext<{ open: (img: Img) => void; current: Img | null; close: () => void } | null>(null);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<Img | null>(null);
  return <Ctx.Provider value={{ open: setCurrent, current, close: () => setCurrent(null) }}>{children}</Ctx.Provider>;
}

export function useLightbox() {
  const c = useContext(Ctx);
  if (!c) throw new Error("LightboxProvider missing");
  return c.open;
}

export function Lightbox() {
  const c = useContext(Ctx);
  const [zoom, setZoom] = useState(false);
  useEffect(() => {
    if (!c?.current) return;
    setZoom(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && c.close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [c?.current]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!c?.current) return null;
  return (
    <div className="lightbox" onClick={c.close}>
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <span className="ellipsis">{c.current.name}</span>
        <button className="icon-btn inverse" onClick={c.close} title="閉じる (Esc)">
          <Icon name="x" />
        </button>
      </div>
      <div className={zoom ? "lightbox-scroll" : "lightbox-fit"} onClick={(e) => e.stopPropagation()}>
        <img
          src={c.current.src}
          alt={c.current.name ?? ""}
          className={zoom ? "zoomed" : ""}
          onClick={() => setZoom((z) => !z)}
          title={zoom ? "クリックで全体表示" : "クリックで等倍表示"}
        />
      </div>
    </div>
  );
}
