import json
import logging
from pathlib import Path
from typing import Any

import joblib

import os

logger = logging.getLogger("sentinelx.ml")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_default_artifact_dir() -> Path:
    override = os.environ.get("SENTINELX_ML_ARTIFACT_DIR")
    if override:
        return Path(override)
    output_dir = os.environ.get("SENTINELX_ML_OUTPUT_DIR")
    if output_dir:
        return Path(output_dir) / "artifacts"
    return _PROJECT_ROOT / "ml" / "artifacts"


class ModelManager:
    def __init__(self, artifact_dir: Path | None = None) -> None:
        self.artifact_dir = artifact_dir or get_default_artifact_dir()
        self._bundle: dict[str, Any] | None = None
        self._metadata: dict[str, Any] | None = None
        self.load_model()

    def load_model(self, version: str = "ml_v1.0.0") -> bool:
        """Load model artifact and metadata from disk, falling back gracefully if missing."""
        model_path = self.artifact_dir / f"sentinelx_{version}.joblib"
        meta_path = self.artifact_dir / f"sentinelx_{version}.json"

        if not model_path.exists():
            logger.warning(
                f"ML model artifact '{model_path}' not found. Falling back to rules-only scoring."
            )
            self._bundle = None
            self._metadata = None
            return False

        try:
            self._bundle = joblib.load(str(model_path))
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
            else:
                self._metadata = {"version": version, "status": "loaded_without_metadata"}
            logger.info(f"Loaded ML model version '{version}' successfully.")
            return True
        except Exception as exc:
            logger.error(
                f"Failed to load ML artifact '{model_path}': {exc}. Operating in rules-only fallback mode."
            )
            self._bundle = None
            self._metadata = None
            return False

    @property
    def is_loaded(self) -> bool:
        return self._bundle is not None

    @property
    def bundle(self) -> dict[str, Any] | None:
        return self._bundle

    @property
    def metadata(self) -> dict[str, Any] | None:
        return self._metadata


model_manager = ModelManager()
