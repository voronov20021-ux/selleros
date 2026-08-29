import { useEffect, useState } from "react";

export default function ProductImage({ src, alt = "", className = "" }) {
  const [state, setState] = useState(src ? "loading" : "empty");

  useEffect(() => {
    setState(src ? "loading" : "empty");
  }, [src]);

  if (state === "empty" || state === "error") {
    return (
      <div className={`thumb-wrap branded-fallback ${className}`} aria-hidden="true">
        <span>ARGUS</span>
      </div>
    );
  }

  return (
    <div className={`thumb-wrap ${className}`}>
      {state === "loading" && <div className="thumb skeleton-thumb" aria-hidden="true" />}
      <img
        className={`thumb ${state === "loading" ? "thumb-hidden" : ""}`}
        src={src}
        alt={alt}
        loading="lazy"
        onLoad={() => setState("ok")}
        onError={() => setState("error")}
      />
    </div>
  );
}
