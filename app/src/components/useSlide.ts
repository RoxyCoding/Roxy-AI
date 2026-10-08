import { useEffect, useState } from "react";

// Keep closing panels mounted until their slide animation finishes.
export function useSlide<T>(value: T | null) {
  const [retained, setRetained] = useState(value);
  useEffect(() => {
    if (value !== null) {
      setRetained(value);
      return;
    }
    const delay = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180;
    const timer = setTimeout(() => setRetained(null), delay);
    return () => clearTimeout(timer);
  }, [value]);
  return { value: value ?? retained, exiting: value === null };
}
