import { Link } from "react-router-dom";
import { presentMission } from "../labels";

export default function MissionBar({ missions, article }) {
  const raw = missions?.next;
  const next = presentMission(raw, { article });
  const done = (missions?.items || []).filter((m) => m.done).length;
  const total = (missions?.items || []).length || 7;
  if (!next) return null;
  return (
    <div className="card mission-bar">
      <div>
        <strong style={{ fontSize: "0.9rem" }}>
          Миссия {done + 1}/{total}
        </strong>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          {next.title}: {next.hint}
        </p>
      </div>
      <Link className="btn" to={next.to || "/products"}>
        {next.id === "wb_connect" ? "Позже" : next.cta || "Дальше"}
      </Link>
    </div>
  );
}
