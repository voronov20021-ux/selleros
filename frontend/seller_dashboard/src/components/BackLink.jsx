import { useNavigate } from "react-router-dom";

let previousPathname = null;
let currentPathname = null;

export function trackRoute(pathname) {
  if (currentPathname === pathname) return;
  previousPathname = currentPathname;
  currentPathname = pathname;
}

function isLogicalParent(prev, parent) {
  if (!prev || !parent) return false;
  const p = parent.replace(/\/+$/, "") || "/";
  if (p === "/") return prev === "/";
  return prev === p || prev.startsWith(`${p}/`);
}

export default function BackLink({ to }) {
  const navigate = useNavigate();

  function onClick() {
    if (isLogicalParent(previousPathname, to)) {
      navigate(-1);
      return;
    }
    navigate(to);
  }

  return (
    <button type="button" className="back-link" onClick={onClick}>
      ← Назад
    </button>
  );
}
