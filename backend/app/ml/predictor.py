import logging
from typing import Any

import numpy as np

from app.ml.model_loader import model_manager

logger = logging.getLogger("sentinelx.ml.predictor")


def predict_fraud_score(features: dict[str, Any]) -> tuple[int | None, list[dict[str, Any]], str | None]:
    """
    Produce an ML fraud likelihood score (0-100) and top contributing feature explanations.

    Returns:
        tuple: (ml_score [0-100] or None, list of top contributing features, explanation_method)
    """
    if not model_manager.is_loaded or model_manager.bundle is None:
        return None, [], None

    bundle = model_manager.bundle
    clf = bundle["classifier"]
    feature_names = bundle["feature_names"]
    version = bundle.get("version", "ml_v1.0.0")

    # Vectorize input according to feature order
    vector = []
    for name in feature_names:
        val = features.get(name, 0.0)
        vector.append(float(val) if val is not None else 0.0)

    X = np.array([vector], dtype=np.float32)

    try:
        prob = float(clf.predict_proba(X)[0][1])
        ml_score = int(round(prob * 100))

        # Explainability: Feature contribution ranking
        # Explanation Method: Global Tree Feature Importance scaled by relative feature signal
        importances = clf.feature_importances_
        contributions: list[dict[str, Any]] = []

        for name, imp, val in zip(feature_names, importances, vector):
            if val != 0.0 and imp > 0.01:
                contributions.append({
                    "feature": name,
                    "importance_weight": round(float(imp), 4),
                    "value": round(float(val), 2),
                    "signal_contribution": round(float(imp * min(val, 10.0)), 4),
                })

        # Top 3 contributing features
        top_contributions = sorted(
            contributions, key=lambda x: x["signal_contribution"], reverse=True
        )[:3]

        method = "Feature importance weighted by feature value (RandomForest ensemble)"
        return ml_score, top_contributions, method
    except Exception as exc:
        logger.error(f"Error during ML prediction: {exc}. Falling back to rules only.")
        return None, [], None
