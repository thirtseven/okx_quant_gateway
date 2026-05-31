#!/usr/bin/env python3
"""Proxy Manager for OKX Quantitative Trading Gateway."""

import os
import logging
import time
import threading
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)


class ProxyManager:
    """Manages proxy configuration with health checks and failover."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._lock = threading.RLock()
        self._proxy_list: List[str] = []
        self._current_index: int = 0
        self._health_cache: Dict[str, Dict[str, Any]] = {}
        self._health_cache_ttl: int = self.config.get("health_cache_ttl", 300)
        self._failover_enabled: bool = self.config.get("failover_enabled", True)
        self._direct_fallback: bool = self.config.get("direct_fallback", True)
        self._initialize_proxies()

    def _initialize_proxies(self):
        proxy_list = self.config.get("proxy_list", [])
        single_proxy = self.config.get("proxy")
        
        if proxy_list:
            self._proxy_list = proxy_list
        elif single_proxy:
            self._proxy_list = [single_proxy]
        else:
            env_proxy = (
                os.environ.get("HTTPS_PROXY") or 
                os.environ.get("https_proxy") or 
                os.environ.get("HTTP_PROXY") or 
                os.environ.get("http_proxy")
            )
            if env_proxy:
                self._proxy_list = [env_proxy]
            else:
                wsl_proxy = self._detect_wsl2_gateway()
                if wsl_proxy:
                    self._proxy_list = [wsl_proxy]
        
        if self._direct_fallback and "" not in self._proxy_list:
            self._proxy_list.append("")
        
        logger.info(f"Initialized proxy list: {self._proxy_list}")

    def _detect_wsl2_gateway(self) -> Optional[str]:
        # User has set up socat to forward 0.0.0.0:7890 -> clash proxy
        # Use localhost:7890 for reliable proxy access
        return "http://127.0.0.1:7890"

    def _check_proxy_connectivity(self, proxy: str, timeout: float = 5.0) -> bool:
        try:
            if not proxy:
                response = httpx.get(
                    "https://www.okx.com/api/v5/public/time",
                    timeout=timeout,
                    follow_redirects=True
                )
                return response.status_code == 200
            
            response = httpx.get(
                "https://www.okx.com/api/v5/public/time",
                proxy=proxy,
                timeout=timeout,
                follow_redirects=True
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Proxy connectivity check failed for {proxy}: {e}")
            return False

    def _update_health_cache(self, proxy: str, is_healthy: bool):
        self._health_cache[proxy] = {
            "healthy": is_healthy,
            "last_check": time.time(),
            "consecutive_failures": 0 if is_healthy else self._health_cache.get(proxy, {}).get("consecutive_failures", 0) + 1
        }

    def _is_proxy_healthy(self, proxy: str) -> bool:
        if proxy not in self._health_cache:
            return True
        
        cache = self._health_cache[proxy]
        if time.time() - cache["last_check"] > self._health_cache_ttl:
            return True
        
        return cache["healthy"]

    def get_proxy(self) -> Optional[str]:
        with self._lock:
            if not self._proxy_list:
                return None
            
            current_proxy = self._proxy_list[self._current_index]
            if self._is_proxy_healthy(current_proxy):
                return current_proxy if current_proxy else None
            
            if self._failover_enabled:
                for _ in range(len(self._proxy_list) - 1):
                    self._current_index = (self._current_index + 1) % len(self._proxy_list)
                    next_proxy = self._proxy_list[self._current_index]
                    if self._is_proxy_healthy(next_proxy):
                        logger.warning(f"Failover to proxy: {next_proxy or 'direct'}")
                        return next_proxy if next_proxy else None
            
            return current_proxy if current_proxy else None

    def report_success(self, proxy: Optional[str] = None):
        proxy_str = proxy or ""
        with self._lock:
            self._update_health_cache(proxy_str, True)

    def report_failure(self, proxy: Optional[str] = None):
        proxy_str = proxy or ""
        with self._lock:
            self._update_health_cache(proxy_str, False)
            
            if proxy_str in self._health_cache:
                if self._health_cache[proxy_str]["consecutive_failures"] >= 3:
                    logger.warning(f"Proxy {proxy_str or 'direct'} has too many failures, rotating")
                    self._current_index = (self._current_index + 1) % len(self._proxy_list)

    def get_proxies_dict(self) -> Optional[Dict[str, str]]:
        proxy = self.get_proxy()
        if proxy is None:
            return None
        return {"https://": proxy, "http://": proxy}

    def get_all_proxies(self) -> List[str]:
        with self._lock:
            return self._proxy_list.copy()

    def add_proxy(self, proxy: str):
        with self._lock:
            if proxy not in self._proxy_list:
                self._proxy_list.insert(0, proxy)
                logger.info(f"Added proxy: {proxy}")

    def remove_proxy(self, proxy: str):
        with self._lock:
            if proxy in self._proxy_list:
                self._proxy_list.remove(proxy)
                if self._current_index >= len(self._proxy_list):
                    self._current_index = 0
                logger.info(f"Removed proxy: {proxy}")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "proxy_list": self._proxy_list,
                "current_index": self._current_index,
                "current_proxy": self._proxy_list[self._current_index] if self._proxy_list else None,
                "health_cache": self._health_cache.copy(),
                "failover_enabled": self._failover_enabled,
                "direct_fallback": self._direct_fallback,
            }


_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager(config: Optional[Dict[str, Any]] = None) -> ProxyManager:
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager(config)
    return _proxy_manager


def get_proxy() -> Optional[str]:
    return get_proxy_manager().get_proxy()


def get_proxies_dict() -> Optional[Dict[str, str]]:
    return get_proxy_manager().get_proxies_dict()


def report_success(proxy: Optional[str] = None):
    get_proxy_manager().report_success(proxy)


def report_failure(proxy: Optional[str] = None):
    get_proxy_manager().report_failure(proxy)


def get_status() -> Dict[str, Any]:
    return get_proxy_manager().get_status()


def create_client(**kwargs) -> httpx.AsyncClient:
    proxy = get_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def create_sync_client(**kwargs) -> httpx.Client:
    proxy = get_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("Testing ProxyManager...")
    manager = get_proxy_manager()
    
    print(f"Status: {manager.get_status()}")
    print(f"Current proxy: {manager.get_proxy()}")
    print(f"Proxies dict: {manager.get_proxies_dict()}")
    
    print("\nTesting proxy health...")
    for proxy in manager.get_all_proxies():
        is_healthy = manager._check_proxy_connectivity(proxy, timeout=10.0)
        print(f"Proxy {proxy or 'direct'}: {'healthy' if is_healthy else 'unhealthy'}")
