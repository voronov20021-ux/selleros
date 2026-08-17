import { Link } from "react-router-dom";
import { humanArgus } from "../labels";
import ProductImage from "./ProductImage";

export default function ProductCard({ product }) {
  const status = product.argus_status || null;
  const diagnosis = (product.problems || [])[0] || (product.first_screen?.verdict || "");

  return (
    <Link to={`/products/${product.article}`} className="card product-card">
      <ProductImage src={product.image} alt="" />
      <div>
        <h3 className="meta-title">{product.title}</h3>
        <div className="meta-line">
          <span>nmID {product.article}</span>
          {product.price != null && <span>{product.price} ₽</span>}
          {product.rating != null && <span>★ {product.rating}</span>}
          {product.feedback_count != null && <span>{product.feedback_count} отз.</span>}
        </div>
        {status ? (
          <div className={`badge status-${status}`}>
            <span className={`status-dot bg-${status}`} />
            {humanArgus(status)}
            {product.argus_score != null ? ` · ${product.argus_score}` : ""}
          </div>
        ) : (
          <div className="badge status-YELLOW">Пока недостаточно данных</div>
        )}
        {diagnosis ? (
          <p className="muted clamp-2" style={{ margin: "8px 0 0" }}>
            {diagnosis}
          </p>
        ) : (
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Короткого диагноза пока нет
          </p>
        )}
      </div>
    </Link>
  );
}
