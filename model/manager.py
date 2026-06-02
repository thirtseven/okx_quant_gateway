"""Model Manager - Dynamic model lifecycle management.

Auto-detects model files in folders. Supports hot-swap by replacing files.
No hardcoded filenames - scans for common model formats.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(PROJECT_DIR, "models")
ACTIVE_MODEL_FILE = os.path.join(PROJECT_DIR, "data", "active_model.json")

MODEL_EXTENSIONS = {".txt", ".model", ".bin", ".pkl", ".joblib", ".onnx", ".pmml"}
PARAMS_EXTENSIONS = {".json", ".yaml", ".yml"}
PREPROCESSOR_EXTENSIONS = {".joblib", ".pkl", ".pickle", ".pt", ".pth"}


def _find_files_by_extensions(directory: str, extensions: set) -> List[str]:
    found = []
    if not os.path.isdir(directory):
        return found
    for f in os.listdir(directory):
        if Path(f).suffix.lower() in extensions:
            found.append(os.path.join(directory, f))
    return found


def _is_lightgbm_file(filepath: str) -> bool:
    try:
        with open(filepath, 'r') as f:
            first_line = f.readline().strip()
            return first_line.startswith("tree") or "version" in first_line.lower()
    except (UnicodeDecodeError, PermissionError):
        return False


class ModelContainer:
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.model = None
        self.model_type = None
        self.preprocessor = None
        self.params: Optional[Dict] = None
        self.features: Optional[List[str]] = None
        self.loaded = False
        self.error: Optional[str] = None
        self._file_mtimes: Dict[str, float] = {}
        self._lock = threading.Lock()
    
    def _detect_model_file(self) -> Optional[str]:
        candidates = _find_files_by_extensions(self.path, MODEL_EXTENSIONS)
        for f in candidates:
            if _is_lightgbm_file(f):
                return f
        for f in candidates:
            fname = Path(f).stem.lower()
            if "model" in fname or "lgb" in fname or "lightgbm" in fname:
                return f
        return candidates[0] if candidates else None
    
    def _detect_params_file(self) -> Optional[str]:
        candidates = _find_files_by_extensions(self.path, PARAMS_EXTENSIONS)
        for f in candidates:
            fname = Path(f).stem.lower()
            if "param" in fname or "config" in fname or "meta" in fname:
                return f
        return candidates[0] if candidates else None
    
    def _detect_preprocessor_file(self) -> Optional[str]:
        candidates = _find_files_by_extensions(self.path, PREPROCESSOR_EXTENSIONS)
        for f in candidates:
            fname = Path(f).stem.lower()
            if "preproc" in fname or "scaler" in fname or "transform" in fname:
                return f
        return candidates[0] if candidates else None
    
    def _check_files_changed(self) -> bool:
        model_file = self._detect_model_file()
        params_file = self._detect_params_file()
        preprocessor_file = self._detect_preprocessor_file()
        
        changed = False
        for key, filepath in [("model", model_file), ("params", params_file), ("preproc", preprocessor_file)]:
            if filepath and os.path.exists(filepath):
                mtime = os.path.getmtime(filepath)
                if key not in self._file_mtimes or self._file_mtimes[key] != mtime:
                    self._file_mtimes[key] = mtime
                    changed = True
        return changed
    
    def load(self, force: bool = False) -> bool:
        with self._lock:
            if self.loaded and not force and not self._check_files_changed():
                return True
            
            try:
                model_file = self._detect_model_file()
                params_file = self._detect_params_file()
                
                if not model_file:
                    self.error = "No model file found"
                    return False
                
                if not params_file:
                    self.error = "No params file found"
                    return False
                
                self.model = self._load_model(model_file)
                if self.model is None:
                    self.error = f"Failed to load model: {model_file}"
                    return False
                
                with open(params_file, 'r') as f:
                    self.params = json.load(f)
                
                features_raw = self.params.get("features", [])
                if isinstance(features_raw, dict):
                    self.features = features_raw.get("names", [])
                else:
                    self.features = features_raw if isinstance(features_raw, list) else []
                
                preprocessor_file = self._detect_preprocessor_file()
                if preprocessor_file:
                    self.preprocessor = self._load_preprocessor(preprocessor_file)
                
                self.loaded = True
                self.error = None
                logger.info("[ModelManager] Loaded '%s' (%s, %d features)", 
                           self.name, self.model_type or "unknown", len(self.features))
                return True
                
            except Exception as e:
                self.error = str(e)
                self.loaded = False
                logger.error("[ModelManager] Failed to load '%s': %s", self.name, e)
                return False
    
    def _load_model(self, filepath: str):
        ext = Path(filepath).suffix.lower()
        
        if ext in (".txt", ".model", ".bin"):
            try:
                import lightgbm as lgb
                self.model_type = "lightgbm"
                return lgb.Booster(model_file=filepath)
            except Exception as e:
                logger.debug("[ModelManager] Not LightGBM: %s", e)
        
        if ext == ".onnx":
            try:
                import onnxruntime as ort
                self.model_type = "onnx"
                return ort.InferenceSession(filepath)
            except Exception as e:
                logger.debug("[ModelManager] Not ONNX: %s", e)
        
        if ext in (".pkl", ".joblib", ".pickle"):
            try:
                import joblib
                self.model_type = "sklearn"
                return joblib.load(filepath)
            except Exception as e:
                logger.debug("[ModelManager] Not sklearn: %s", e)
        
        try:
            import lightgbm as lgb
            self.model_type = "lightgbm"
            return lgb.Booster(model_file=filepath)
        except Exception:
            pass
        
        try:
            import joblib
            self.model_type = "sklearn"
            return joblib.load(filepath)
        except Exception:
            pass
        
        return None
    
    def _load_preprocessor(self, filepath: str):
        try:
            import joblib
            return joblib.load(filepath)
        except Exception as e:
            logger.warning("[ModelManager] Failed to load preprocessor: %s", e)
            return None
    
    def predict(self, X: np.ndarray) -> Optional[float]:
        if not self.loaded or self.model is None:
            if not self.load():
                return None
        
        with self._lock:
            try:
                if self.preprocessor is not None:
                    scaler = self.preprocessor if hasattr(self.preprocessor, "transform") else None
                    if scaler is not None:
                        X = scaler.transform(X)
                
                if self.model_type == "lightgbm":
                    return float(self.model.predict(X, num_iteration=self.model.best_iteration)[-1])
                elif self.model_type == "onnx":
                    input_name = self.model.get_inputs()[0].name
                    result = self.model.run(None, {input_name: X.astype(np.float32)})
                    return float(result[0][-1])
                elif self.model_type == "sklearn":
                    return float(self.model.predict(X)[-1])
                else:
                    return float(self.model.predict(X)[-1])
            except Exception as e:
                logger.error("[ModelManager] Predict error for '%s': %s", self.name, e)
                return None
    
    def get_info(self) -> Dict[str, Any]:
        self.load()
        return {
            "name": self.name,
            "path": self.path,
            "loaded": self.loaded,
            "error": self.error,
            "model_type": self.model_type,
            "version": self.params.get("version", "unknown") if self.params else "unknown",
            "n_features": len(self.features) if self.features else 0,
            "features": self.features or [],
            "fitness": self.params.get("best_fitness") if self.params else None,
            "test_metrics": self.params.get("test", {}) if self.params else {},
        }


class ModelManager:
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
        try:
            if os.path.exists(ACTIVE_MODEL_FILE):
                with open(ACTIVE_MODEL_FILE, 'r') as f:
                    config = json.load(f)
                    self._active_name = config.get("active_model")
        except Exception:
            pass
    
    def _save_active_model_config(self):
        try:
            os.makedirs(os.path.dirname(ACTIVE_MODEL_FILE), exist_ok=True)
            with open(ACTIVE_MODEL_FILE, 'w') as f:
                json.dump({"active_model": self._active_name}, f)
        except Exception as e:
            logger.error("[ModelManager] Failed to save config: %s", e)
    
    def scan_models(self) -> List[str]:
        found = []
        if not os.path.isdir(self.models_dir):
            return found
        
        for entry in os.listdir(self.models_dir):
            entry_path = os.path.join(self.models_dir, entry)
            if os.path.isdir(entry_path):
                has_model = len(_find_files_by_extensions(entry_path, MODEL_EXTENSIONS)) > 0
                has_params = len(_find_files_by_extensions(entry_path, PARAMS_EXTENSIONS)) > 0
                
                if has_model and has_params:
                    if entry not in self._containers:
                        self._containers[entry] = ModelContainer(entry, entry_path)
                    found.append(entry)
        
        if self._active_name is None and found:
            self._active_name = found[0]
            self._save_active_model_config()
        
        return found
    
    def get_active_model(self) -> Optional[ModelContainer]:
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
        self.scan_models()
        if name in self._containers:
            with self._lock:
                self._active_name = name
                self._save_active_model_config()
            logger.info("[ModelManager] Switched to '%s'", name)
            return True
        return False
    
    def reload_model(self, name: Optional[str] = None) -> bool:
        target = name or self._active_name
        if target and target in self._containers:
            return self._containers[target].load(force=True)
        return False
    
    def list_models(self) -> List[Dict[str, Any]]:
        self.scan_models()
        result = []
        for name, container in sorted(self._containers.items()):
            info = container.get_info()
            info["is_active"] = (name == self._active_name)
            result.append(info)
        return result
    
    def delete_model(self, name: str) -> bool:
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
            logger.info("[ModelManager] Deleted '%s'", name)
            return True
        except Exception as e:
            logger.error("[ModelManager] Failed to delete '%s': %s", name, e)
            return False
    
    def get_model_path(self, name: Optional[str] = None) -> Optional[str]:
        target = name or self._active_name
        if target and target in self._containers:
            return self._containers[target].path
        return None
    
    def start_watcher(self, interval: int = 30):
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
        logger.info("[ModelManager] Watcher started (interval=%ds)", interval)
    
    def stop_watcher(self):
        self._running = False


_manager: Optional[ModelManager] = None


def get_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager


def get_active_model():
    return get_manager().get_active_model()


def list_models() -> List[Dict[str, Any]]:
    return get_manager().list_models()


def set_active_model(name: str) -> bool:
    return get_manager().set_active_model(name)


def reload_model(name: Optional[str] = None) -> bool:
    return get_manager().reload_model(name)
