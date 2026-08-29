import { Link } from "react-router-dom";
import {
  formatHealth,
  formatRatingParts,
  humanArgus,
  humanizeText,
  inferProblemState,
  looksLikeNoProblem,
  problemStateCopy,
} from "../labels";
import ProductImage from "./ProductImage";

export default function ProductCard({ product }) {
  const status = product.argus_status || null;
  const health = formatHealth(product.argus_score);
  const rating = formatRatingParts(
    product.rating,
    product.feedback_count ?? product.reviews_count
  );
  const problemState = inferProblemState({
    status,
    score: product.argus_score,
    figures: product.first_screen?.figures,
    verdictKind: product.first_screen?.verdict_kind,
    cardHealthy: product.first_screen?.card_healthy,
    funnel: product.first_screen?.funnel_consistency,
  });
  const rawDiagnosis = (product.problems || [])[0] || product.first_screen?.verdict || "";
  const diagnosis = humanizeText(rawDiagnosis, "");
  let diagnosisLine = diagnosis;
  if (!diagnosisLine || looksLikeNoProblem(diagnosisLine)) {
    diagnosisLine = problemState === "confirmed" ? diagnosisLine || "Короткого диагноза пока нет" : problemStateCopy(problemState);
  }

  return (
    <Link to={`/products/${product.article}`} className="card product-card">
      <ProductImage src={product.image} alt="" />
      <div className="product-card-body">
        <h3 className="meta-title">{product.title}</h3>
        <div className="meta-line">
          <span>nmID {product.article}</span>
          {product.price != null && <span>{product.price} ₽</span>}
        </div>
        <div className="meta-line">
          <span>{rating.rating}</span>
          <span>{rating.reviews}</span>
        </div>
        {status ? (
          <div className={`badge status-${status}`}>
            <span className={`status-dot bg-${status}`} />
            {humanArgus(status)}
            {health.missing ? "" : ` · ${health.value}`}
          </div>
        ) : (
          <div className="badge status-YELLOW">Пока недостаточно данных</div>
        )}
        <p className="muted" style={{ margin: "6px 0 0" }}>
          {health.text}
        </p>
        {diagnosisLine ? (
          <p className="muted clamp-2" style={{ margin: "8px 0 0" }}>
            {diagnosisLine}
          </p>
        ) : null}
      </div>
    </Link>
  );
}
