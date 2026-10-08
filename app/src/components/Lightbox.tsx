import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icons";
import { cx } from "../lib/format";
import { useSlide } from "./useSlide";

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
  const preview = useSlide(c?.current ?? null);
  const image = preview.value;
  const [zoom, setZoom] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  useEffect(() => {
    if (!c?.current) return;
    setZoom(false);
    setDownloadError("");
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && c.close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [c?.current]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!c || !image) return null;
  return (
    <div className={cx("lightbox", preview.exiting && "slide-out")} inert={preview.exiting} onClick={c.close}>
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <span className="ellipsis">{image.name}</span>
        {downloadError && <span role="alert">{downloadError}</span>}
        <button className="icon-btn inverse" title="ダウンロード" onClick={async () => {
          try {
            const response = await fetch(image.src, { cache: "no-store" });
            if (!response.ok) throw new Error("画像を取得できません");
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `roxy-image.${blob.type.split("/")[1]?.replace("jpeg", "jpg") || "png"}`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch {
            setDownloadError("画像を取得できません。プレビューの保持期間が終了した可能性があります。");
          }
        }}>
          <Icon name="download" />
        </button>
        <button className="icon-btn inverse" onClick={c.close} title="閉じる (Esc)">
          <Icon name="x" />
        </button>
      </div>
      <div className={zoom ? "lightbox-scroll" : "lightbox-fit"} onClick={(e) => e.stopPropagation()}>
        <img
          src={image.src}
          alt={image.name ?? ""}
          className={zoom ? "zoomed" : ""}
          onClick={() => setZoom((z) => !z)}
          title={zoom ? "クリックで全体表示" : "クリックで等倍表示"}
        />
      </div>
    </div>
  );
}
