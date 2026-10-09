import { useState, type ReactNode } from "react";
import { Icon } from "./Icons";

/** Collapsible section of the right panel. */
export function Section({ title, children, right, defaultOpen = true }: { title: string; children: ReactNode; right?: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="panel-section">
      <div className="panel-section-head">
        <button className="link-btn strong" onClick={() => setOpen((v) => !v)}>
          <Icon name={open ? "down" : "chevron"} size={12} /> {title}
        </button>
        {right}
      </div>
      {open && <div className="panel-section-body">{children}</div>}
    </section>
  );
}
