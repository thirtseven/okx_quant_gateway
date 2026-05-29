"""Model Manager - Containerized LightGBM model lifecycle management.

Scans models/ directory for model containers, supports hot-swap by replacing files.
Each model folder contains: model.txt, params.json, preprocessor.joblib (optional)
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

import lightgbm as lgb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
ACTIVE_MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "active_model.json")


class ModelContainer:
    """Single model container with lazy loading and file change detection."""
    
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.model: Optional[lgb.Booster] = None
        self.preprocessor = None
        self.params: Optional[Dict] = None
        self.features: Optional[List[str]] = None
        self.loaded = False
        self.error: Optional[str] = None
        self._file_mtimes: Dict[str, float] = {}
        self._lock = threading.Lock()
    
    def _get_model_file(self) -> str:
        return os.path.join(self.path, "model.txt")
    
    def _get_params_file(self) -> str:
        return os.path.join(self.path, "params.json")
    
    def _get_preprocessor_file(self) -> str:
        return os.path.join(self.path, "preprocessor.joblib")
    
    def _check_files_changed(self) -> bool:
        """Check if any model files have been modified."""
        files = {
            "model": self._get_model_file(),
            "params": self._get_params_file(),
            "preprocessor": self._get_preprocessor_file(),
        }
        changed = False
        for key, filepath in files.items():
            if os.path.exists(filepath):
                mtime = os.path.getmtime(filepath)
                if key not in self._file_mtimes or self._file_mtimes[key] != mtime:
                    self._file_mtimes[key] = mtime
                    changed = True
        return changed
    
    def load(self, force: bool = False) -> bool:
        """Load or reload model if files changed."""
        with self._lock:
            if self.loaded and not force and not self._check_files_changed():
                return True
            
            try:
                model_file = self._get_model_file()
                params_file = self._get_params_file()
                
                if not os.path.exists(model_file):
                    self.error = f"Model file not found: {model_file}"
                    logger.error("[ModelManager] %s", self.error)
                    return False
                
                if not os.path.exists(params_file):
                    self.error = f"Params file not found: {params_file}"
                    logger.error("[ModelManager] %s", self.error)
                    return False
                
                self.model = lgb.Booster(model_file=model_file)
                
                with open(params_file, 'r') as f:
                    self.params = json.load(f)
                
                features_raw = self.params.get("features", [])
                if isinstance(features_raw, dict):
                    self.features = features_raw.get("names", [])
                else:
                    self.features = features_raw if isinstance(features_raw, list) else []
                
                preprocessor_file = self._get_preprocessor_file()
                if os.path.exists(preprocessor_file):
                    try:
                        import joblib
                        self.preprocessor = joblib.load(preprocessor_file)
                    except Exception as e:
                        logger.warning("[ModelManager] Failed to load preprocessor: %s", e)
                        self.preprocessor = None
                
                self.loaded = True
                self.error = None
                logger.info("[ModelManager] Loaded model '%s' with %d features", 
                           self.name, len(self.features))
                return True
                
            except Exception as e:
                self.error = str(e)
                self.loaded = False
                logger.error("[ModelManager] Failed to load model '%s': %s", self.name, e)
                return False
    
    def predict(self, X: np.ndarray) -> Optional[float]:
        """Run prediction on preprocessed features."""
        if not self.loaded or self.model is None:
            if not self.load():
                return None
        
        with self._lock:
            try:
                if self.preprocessor is not None:
                    scaler = self.preprocessor if hasattr(self.preprocessor, "transform") else None
                    if scaler is not None:
                        X = scaler.transform(X)
                
                raw = float(self.model.predict(X, num_iteration=self.model.best_iteration)[-1])
                return raw
            except Exception as e:
                logger.error("[ModelManager] Prediction error for '%s': %s", self.name, e)
                return None
    
    def get_info(self) -> Dict[str, Any]:
        """Get model metadata."""
        self.load()
        return {
            "name": self.name,
            "path": self.path,
            "loaded": self.loaded,
            "error": self.error,
            "version": self.params.get("version", "unknown") if self.params else "unknown",
            "n_features": len(self.features) if self.features else 0,
            "features": self.features or [],
            "fitness": self.params.get("best_fitness") if self.params else None,
            "test_metrics": self.params.get("test", {}) if self.params else {},
        }


class ModelManager:
    """Manages multiple model containers with hot-swap support."""
    
    def __init__(self, models_dir: str = MODELS_DIR):
        self.models_dir = models_dir
        self._containers: Dict[str, ModelContainer] = {}
        self._active_name: Optional[str] = None
        self._lock = threading.Lock()
        self._watch_thread: Optional[threading.Thread] = None
        self._running = False
        
        os.makedirs(models_dir, exist_ok=True)
        self._load_active_model_config()
        self.scan_models()
    
    def _load_active_model_config(self):
        """Load which model is currently active."""
        try:
            if os.path.exists(ACTIVE_MODEL_FILE):
                with open(ACTIVE_MODEL_FILE, 'r') as f:
                    config = json.load(f)
                    self._active_name = config.get("active_model")
        except Exception:
            pass
    
    def _save_active_model_config(self):
        """Persist active model selection."""
        try:
            os.makedirs(os.path.dirname(ACTIVE_MODEL_FILE), exist_ok=True)
            with open(ACTIVE_MODEL_FILE, 'w') as f:
                json.dump({"active_model": self._active_name}, f)
        except Exception as e:
            logger.error("[ModelManager] Failed to save active model config: %s", e)
    
    def scan_models(self) -> List[str]:
        """Scan models directory for available model containers."""
        found = []
        if not os.path.isdir(self.models_dir):
            return found
        
        for entry in os.listdir(self.models_dir):
            entry_path = os.path.join(self.models_dir, entry)
            if os.path.isdir(entry_path):
                model_file = os.path.join(entry_path, "model.txt")
                params_file = os.path.join(entry_path, "params.json")
                if os.path.exists(model_file) and os.path.exists(params_file):
                    if entry not in self._containers:
                        self._containers[entry] = ModelContainer(entry, entry_path)
                    found.append(entry)
        
        if self._active_name is None and found:
            self._active_name = found[0]
            self._save_active_model_config()
        
        return found
    
    def get_active_model(self) -> Optional[ModelContainer]:
        """Get the currently active model container."""
        if self._active_name and self._active_name in self._containers:
            container = self._containers[self._active_name]
            container.load()
            return container
        
        available = self.list_models()
        if available:
            self._active_name = available[0]["name"]
            self._save_active_model_config()
            return self._containers.get(self._active_name)
        
        return None
    
    def set_active_model(self, name: str) -> bool:
        """Switch active model by name."""
        self.scan_models()
        if name in self._containers:
            with self._lock:
                self._active_name = name
                self._save_active_model_config()
            logger.info("[ModelManager] Switched active model to '%s'", name)
            return True
        return False
    
    def reload_model(self, name: Optional[str] = None) -> bool:
        """Force reload a model (or active model if name is None)."""
        target = name or self._active_name
        if target and target in self._containers:
            return self._containers[target].load(force=True)
        return False
    
    def list_models(self) -> List[Dict[str, Any]]:
        """List all available models with their info."""
        self.scan_models()
        result = []
        for name, container in sorted(self._containers.items()):
            info = container.get_info()
            info["is_active"] = (name == self._active_name)
            result.append(info)
        return result
    
    def delete_model(self, name: str) -> bool:
        """Delete a model container (cannot delete active model)."""
        if name == self._active_name:
            logger.error("[ModelManager] Cannot delete active model")
            return False
        
        if name not in self._containers:
            return False
        
        container = self._containers[name]
        try:
            import shutil
            shutil.rmtree(container.path)
            del self._containers[name]
            logger.info("[ModelManager] Deleted model '%s'", name)
            return True
        except Exception as e:
            logger.error("[ModelManager] Failed to delete model '%s': %s", name, e)
            return False
    
    def get_model_path(self, name: Optional[str] = None) -> Optional[str]:
        """Get the directory path for a model."""
        target = name or self._active_name
        if target and target in self._containers:
            return self._containers[target].path
        return None
    
    def start_watcher(self, interval: int = 30):
        """Start background thread to watch for model file changes."""
        if self._watch_thread and self._watch_thread.is_alive():
            return
        
        self._running = True
        
        def _watch_loop():
            while self._running:
                try:
                    self.scan_models()
                    for container in self._containers.values():
                        container.load()
                except Exception as e:
                    logger.error("[ModelManager] Watcher error: %s", e)
                time.sleep(interval)
        
        self._watch_thread = threading.Thread(target=_watch_loop, daemon=True)
        self._watch_thread.start()
        logger.info("[ModelManager] Started model watcher (interval=%ds)", interval)
    
    def stop_watcher(self):
        """Stop the background watcher thread."""
        self._running = False


_manager: Optional[ModelManager] = None


def get_manager() -> ModelManager:
    """Get or create the singleton ModelManager instance."""
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager


def get_active_model() -> Optional[ModelContainer]:
    """Get the active model container."""
    return get_manager().get_active_model()


def list_models() -> List[Dict[str, Any]]:
    """List all available models."""
    return get_manager().list_models()


def set_active_model(name: str) -> bool:
    """Switch active model."""
    return get_manager().set_active_model(name)


def reload_model(name: Optional[str] = None) -> bool:
    """Reload a model."""
    return get_manager().reload_model(name)
