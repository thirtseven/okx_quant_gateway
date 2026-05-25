"""Network subnet management for OKX Quantitative Trading Gateway.

Creates isolated virtual network interfaces for gateway operations.
Uses ip link add link to create MACVLAN subinterfaces.
Supports automatic subnet detection and creation on startup.
"""

import logging
import os
import subprocess
import ipaddress
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

GATEWAY_INTERFACE = "okx-gw0"
GATEWAY_SUBNET = "10.99.0.0/24"
GATEWAY_IP = "10.99.0.1"
PARENT_INTERFACE = "eth0"
GATEWAY_PORT = 5000


class NetworkManager:
    """Manages virtual network interface for gateway isolation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.interface = self.config.get("interface", GATEWAY_INTERFACE)
        self.parent = self.config.get("parent", self._detect_parent_interface())
        self.subnet = self.config.get("subnet", GATEWAY_SUBNET)
        self.gateway_ip = self.config.get("gateway_ip", GATEWAY_IP)
        self.port = self.config.get("port", GATEWAY_PORT)
        self._created = False
        self._available_ips: List[str] = []

    def _detect_parent_interface(self) -> str:
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 5 and parts[0] == "default":
                    return parts[4]
        except Exception as e:
            logger.warning("Failed to detect parent interface: %s", e)
        return PARENT_INTERFACE

    def _validate_parent_interface(self) -> None:
        """Validate parent interface name to prevent command injection."""
        import re
        if not re.match(r'^[a-zA-Z0-9_.@-]+$', self.parent):
            raise ValueError(f"Invalid parent interface name: {self.parent}")

    def _run_command(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        logger.debug("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        if check and result.returncode != 0:
            logger.error("Command failed: %s\nstderr: %s", " ".join(cmd), result.stderr)
            raise RuntimeError(f"Command failed: {' '.join(cmd)}: {result.stderr}")
        return result

    def interface_exists(self) -> bool:
        result = self._run_command(
            ["ip", "link", "show", self.interface],
            check=False
        )
        return result.returncode == 0

    def get_host_ip(self) -> str:
        try:
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ips = result.stdout.strip().split()
                if ips:
                    return ips[0]
        except Exception as e:
            logger.warning("Failed to get host IP: %s", e)
        return "127.0.0.1"

    def get_available_ips(self) -> List[str]:
        network = ipaddress.ip_network(self.subnet, strict=False)
        available = []
        for ip in network.hosts():
            ip_str = str(ip)
            if ip_str != self.gateway_ip:
                available.append(ip_str)
        return available[:10]

    def create_interface(self) -> bool:
        if self.interface_exists():
            logger.info("Interface %s already exists", self.interface)
            self._created = True
            return True

        if os.geteuid() != 0:
            logger.warning("Network creation requires root privileges - skipping")
            logger.info("Gateway will use localhost:5000")
            return False

        self._validate_parent_interface()

        try:
            self._run_command([
                "ip", "link", "add", "link", self.parent,
                "name", self.interface,
                "type", "macvlan",
                "mode", "bridge"
            ])

            network = ipaddress.ip_network(self.subnet, strict=False)
            ip_with_prefix = f"{self.gateway_ip}/{network.prefixlen}"

            self._run_command(["ip", "addr", "add", ip_with_prefix, "dev", self.interface])
            self._run_command(["ip", "link", "set", self.interface, "up"])

            self._created = True
            self._available_ips = self.get_available_ips()
            logger.info("Created interface %s with IP %s", self.interface, ip_with_prefix)
            logger.info("Gateway accessible at http://%s:%d", self.gateway_ip, self.port)
            return True

        except Exception as e:
            logger.error("Failed to create interface: %s", e)
            self.cleanup()
            return False

    def add_ip_alias(self, ip: str) -> bool:
        try:
            network = ipaddress.ip_network(self.subnet, strict=False)
            ip_with_prefix = f"{ip}/{network.prefixlen}"
            self._run_command(["ip", "addr", "add", ip_with_prefix, "dev", self.interface])
            logger.info("Added IP alias %s to %s", ip, self.interface)
            return True
        except Exception as e:
            logger.error("Failed to add IP alias: %s", e)
            return False

    def remove_ip_alias(self, ip: str) -> bool:
        try:
            network = ipaddress.ip_network(self.subnet, strict=False)
            ip_with_prefix = f"{ip}/{network.prefixlen}"
            self._run_command(["ip", "addr", "del", ip_with_prefix, "dev", self.interface], check=False)
            logger.info("Removed IP alias %s from %s", ip, self.interface)
            return True
        except Exception as e:
            logger.error("Failed to remove IP alias: %s", e)
            return False

    def check_connectivity(self, target_ip: str = None) -> bool:
        if target_ip is None:
            target_ip = self.gateway_ip
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", target_ip],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_network_info(self) -> Dict[str, Any]:
        network = ipaddress.ip_network(self.subnet, strict=False)
        return {
            "network": str(network),
            "broadcast": str(network.broadcast_address),
            "netmask": str(network.netmask),
            "prefixlen": network.prefixlen,
            "num_hosts": network.num_addresses - 2,
            "gateway_ip": self.gateway_ip,
            "host_ip": self.get_host_ip(),
        }

    def cleanup(self):
        if not self._created:
            return

        try:
            if self.interface_exists():
                self._run_command(["ip", "link", "set", self.interface, "down"], check=False)
                self._run_command(["ip", "link", "delete", self.interface], check=False)
                logger.info("Cleaned up interface %s", self.interface)
        except Exception as e:
            logger.warning("Cleanup failed: %s", e)
        finally:
            self._created = False

    def get_status(self) -> Dict[str, Any]:
        return {
            "interface": self.interface,
            "parent": self.parent,
            "subnet": self.subnet,
            "gateway_ip": self.gateway_ip,
            "port": self.port,
            "exists": self.interface_exists(),
            "created_by_us": self._created,
            "host_ip": self.get_host_ip(),
            "available_ips": self._available_ips[:5] if self._available_ips else [],
        }


_network_manager: Optional[NetworkManager] = None


def get_network_manager() -> NetworkManager:
    global _network_manager
    if _network_manager is None:
        _network_manager = NetworkManager()
    return _network_manager


def init_network() -> bool:
    manager = get_network_manager()
    return manager.create_interface()


def cleanup_network():
    manager = get_network_manager()
    manager.cleanup()
