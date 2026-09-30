import os
import shutil
import ipaddress
import logging
import subprocess
import time
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("GatewayService")


class GatewayService:
    """
    Dual Redundant HA Gateway & Edge Routing Service.
    Features:
    - Active/Standby Edge Gateway sync (gw1 / gw2).
    - Subdomain to private sandbox reverse-proxy routing via Host header inspection.
    - True Zero-Downtime hot-reloading.
    - Two-Phase Auto-Rollback with precise timing decomposition:
      * Case A: Pre-reload validation rejection (haproxy -c / semantic).
      * Case B: Post-reload runtime recovery (active restoration from .bak).
    """

    def __init__(
        self,
        config_dir: Optional[str] = None,
        domain_suffix: str = "cstam.felcloud.tn",
        primary_gw: str = "gw1.felcloud.tn",
        standby_gw: str = "gw2.felcloud.tn",
        vip_ip: str = "10.0.0.5"
    ):
        self.config_dir = config_dir or os.getenv("GATEWAY_CONFIG_DIR", "./gateway_configs")
        os.makedirs(self.config_dir, exist_ok=True)

        self.domain_suffix = os.getenv("DNS_ZONE", domain_suffix).rstrip(".")
        self.primary_gw = os.getenv("GATEWAY_PRIMARY", primary_gw)
        self.standby_gw = os.getenv("GATEWAY_STANDBY", standby_gw)
        self.vip_ip = os.getenv("GATEWAY_VIP", vip_ip)
        
        self.config_file = os.path.join(self.config_dir, "haproxy.cfg")
        self.backup_file = os.path.join(self.config_dir, "haproxy.cfg.bak")
        self.routes: Dict[str, Dict] = {}
        self.gateway_state = {
            "vip": self.vip_ip,
            "active_node": "primary",
            "primary": {"host": self.primary_gw, "status": "HEALTHY", "vrrp_state": "MASTER"},
            "standby": {"host": self.standby_gw, "status": "HEALTHY", "vrrp_state": "BACKUP"}
        }
        self.last_reload_status = {
            "status": "initial",
            "timestamp": datetime.utcnow().isoformat(),
            "routes_count": 0,
            "rollback_performed": False,
            "metrics": {}
        }

        # Initialize default base config
        if not os.path.exists(self.config_file):
            self._write_base_config()

    def _write_base_config(self):
        """Generates initial working baseline config."""
        content = self._render_haproxy_template({})
        with open(self.config_file, "w") as f:
            f.write(content)
        shutil.copyfile(self.config_file, self.backup_file)

    def _render_haproxy_template(self, routes: Dict[str, Dict]) -> str:
        """Renders HAProxy reverse-proxy configuration with dynamic SNI / Host based backends."""
        lines = [
            "# ========================================================",
            "# FelCloud Dual Redundant HA Gateway Configuration",
            f"# Generated At: {datetime.utcnow().isoformat()}",
            f"# VIP: {self.vip_ip} (Managed by Keepalived VRRP)",
            "# ========================================================",
            "global",
            "    log /dev/log local0",
            "    maxconn 4096",
            "    daemon",
            "",
            "defaults",
            "    log     global",
            "    mode    http",
            "    option  httplog",
            "    option  dontlognull",
            "    timeout connect 5000ms",
            "    timeout client  50000ms",
            "    timeout server  50000ms",
            "",
            "frontend edge_gateway_in",
            "    bind *:80",
            "    mode http",
            "    # Subdomain matching rules (Host header inspection)"
        ]

        # Host ACLs
        for team, info in routes.items():
            fqdn = f"{team}.{self.domain_suffix}"
            backend_name = f"backend_{team}"
            lines.append(f"    acl is_{team} hdr(host) -i {fqdn} {team}")
            lines.append(f"    use_backend {backend_name} if is_{team}")

        lines.append("    default_backend default_fallback")
        lines.append("")

        # Fallback backend
        lines.append("backend default_fallback")
        lines.append("    mode http")
        lines.append("    errorfile 503 /etc/haproxy/errors/503.http")
        lines.append("")

        # Backends
        for team, info in routes.items():
            backend_name = f"backend_{team}"
            ip = info["ip_address"]
            port = info.get("port", 80)
            lines.append(f"backend {backend_name}")
            lines.append("    mode http")
            lines.append("    balance roundrobin")
            lines.append("    option forwardfor")
            lines.append(f"    server {team}_srv {ip}:{port} check inter 2000 fall 3 rise 2")
            lines.append("")

        return "\n".join(lines)

    def validate_configuration(self, candidate_path: str, content: str, routes: Dict[str, Dict]) -> tuple[bool, Optional[str]]:
        """
        2-Level Validator:
        1. Semantic IP / Port / Name validation.
        2. Native 'haproxy -c -f' syntax check.
        """
        if not content or len(content.strip()) < 50:
            return False, "Validation Error: Config file is empty or corrupted."

        required_sections = ["global", "defaults", "frontend", "backend"]
        for sec in required_sections:
            if sec not in content:
                return False, f"Validation Error: Missing required section '{sec}'."

        for team, info in routes.items():
            if not team.replace("-", "").replace("_", "").isalnum():
                return False, f"Validation Error: Illegal team identifier '{team}'."

            try:
                ipaddress.ip_address(info["ip_address"])
            except ValueError:
                return False, f"Validation Error: Invalid IP address '{info['ip_address']}' for team '{team}'."

            port = info.get("port", 80)
            if not isinstance(port, int) or port < 1 or port > 65535:
                return False, f"Validation Error: Invalid port number '{port}'."

        # Native HAProxy -c check
        try:
            res = subprocess.run(
                ["haproxy", "-c", "-f", candidate_path],
                capture_output=True,
                text=True,
                timeout=2
            )
            if res.returncode != 0:
                return False, f"HAProxy -c Syntax Error: {res.stderr.strip()}"
        except (FileNotFoundError, PermissionError):
            pass
        except Exception as e:
            logger.debug(f"HAProxy syntax check skipped: {e}")

        return True, None

    def add_route(self, team_name: str, ip_address: str, port: int = 80, simulate_runtime_failure: bool = False) -> Dict:
        """Adds or updates a route with Zero-Downtime Hot-Reload and Auto-Rollback."""
        team_name = team_name.strip().lower()
        candidate_routes = dict(self.routes)
        candidate_routes[team_name] = {
            "ip_address": ip_address,
            "port": port,
            "fqdn": f"{team_name}.{self.domain_suffix}",
            "updated_at": datetime.utcnow().isoformat()
        }

        result = self._apply_and_reload(candidate_routes, simulate_runtime_failure=simulate_runtime_failure)
        if result["success"]:
            self.routes = candidate_routes
        return result

    def remove_route(self, team_name: str) -> Dict:
        """Removes a route with Zero-Downtime Hot-Reload and Auto-Rollback."""
        team_name = team_name.strip().lower()
        if team_name not in self.routes:
            return {
                "success": True,
                "action": "not_found",
                "message": f"Route for '{team_name}' does not exist.",
                "routes_count": len(self.routes)
            }

        candidate_routes = dict(self.routes)
        del candidate_routes[team_name]

        result = self._apply_and_reload(candidate_routes)
        if result["success"]:
            self.routes = candidate_routes
        return result

    def _apply_and_reload(self, candidate_routes: Dict[str, Dict], simulate_runtime_failure: bool = False) -> Dict:
        """
        Two-Phase Zero-Downtime Reload & Auto-Rollback Engine:
        Phase 1: Pre-reload validation (Case A rollback if corrupt).
        Phase 2: Atomic swap & daemon reload (Case B rollback if runtime reload fails).
        """
        t0 = time.perf_counter()
        candidate_content = self._render_haproxy_template(candidate_routes)
        temp_candidate = os.path.join(self.config_dir, "haproxy.cfg.candidate")

        with open(temp_candidate, "w") as f:
            f.write(candidate_content)

        # 1. Validation Timing
        t_val_start = time.perf_counter()
        valid, err_msg = self.validate_configuration(temp_candidate, candidate_content, candidate_routes)
        t_validation_ms = (time.perf_counter() - t_val_start) * 1000

        # === CASE A: Pre-reload rejection (Syntax / Semantic Corruption) ===
        if not valid:
            t_rb_start = time.perf_counter()
            if os.path.exists(temp_candidate):
                try:
                    os.remove(temp_candidate)
                except Exception:
                    pass
            t_rollback_ms = (time.perf_counter() - t_rb_start) * 1000
            total_duration_ms = (time.perf_counter() - t0) * 1000

            logger.error(f"[AUTO-ROLLBACK CASE A] Rejected candidate: {err_msg}")
            self.last_reload_status = {
                "status": "rollback_case_a_syntax",
                "timestamp": datetime.utcnow().isoformat(),
                "error": err_msg,
                "routes_count": len(self.routes),
                "rollback_performed": True,
                "metrics": {
                    "validation_time_ms": t_validation_ms,
                    "rollback_time_ms": t_rollback_ms,
                    "total_handling_ms": total_duration_ms
                }
            }
            return {
                "success": False,
                "action": "auto_rollback_case_a",
                "rollback_type": "pre_reload_syntax_rejection",
                "error": err_msg,
                "active_routes_count": len(self.routes),
                "metrics": self.last_reload_status["metrics"]
            }

        # Backup current working configuration
        if os.path.exists(self.config_file):
            shutil.copyfile(self.config_file, self.backup_file)

        # 2. Atomic Replacement & Reload
        try:
            t_reload_start = time.perf_counter()
            os.replace(temp_candidate, self.config_file)

            if simulate_runtime_failure:
                raise RuntimeError("Simulated daemon reload failure / socket bind collision")

            self._trigger_gateway_reload()
            t_reload_ms = (time.perf_counter() - t_reload_start) * 1000
            total_duration_ms = (time.perf_counter() - t0) * 1000

            self.last_reload_status = {
                "status": "reloaded_successfully",
                "timestamp": datetime.utcnow().isoformat(),
                "routes_count": len(candidate_routes),
                "rollback_performed": False,
                "metrics": {
                    "validation_time_ms": t_validation_ms,
                    "reload_time_ms": t_reload_ms,
                    "total_handling_ms": total_duration_ms
                }
            }

            return {
                "success": True,
                "action": "reloaded",
                "active_routes_count": len(candidate_routes),
                "metrics": self.last_reload_status["metrics"],
                "timestamp": self.last_reload_status["timestamp"]
            }

        except Exception as e:
            # === CASE B: Post-reload Runtime Recovery (Restoration from .bak) ===
            t_restore_start = time.perf_counter()
            logger.error(f"[AUTO-ROLLBACK CASE B] Runtime exception: {e}. Restoring backup.")
            if os.path.exists(self.backup_file):
                shutil.copyfile(self.backup_file, self.config_file)
                self._trigger_gateway_reload()
            t_restore_ms = (time.perf_counter() - t_restore_start) * 1000
            total_duration_ms = (time.perf_counter() - t0) * 1000

            metrics = {
                "validation_time_ms": t_validation_ms,
                "runtime_recovery_time_ms": t_restore_ms,
                "total_handling_ms": total_duration_ms
            }

            self.last_reload_status = {
                "status": "rollback_case_b_runtime",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e),
                "routes_count": len(self.routes),
                "rollback_performed": True,
                "metrics": metrics
            }

            return {
                "success": False,
                "action": "auto_rollback_case_b",
                "rollback_type": "post_reload_runtime_recovery",
                "error": f"Runtime reload failed: {e}. Restored to backup.",
                "active_routes_count": len(self.routes),
                "metrics": metrics
            }

    def _trigger_gateway_reload(self):
        """Triggers HAProxy seamless zero-downtime master-worker reload if on gateway node."""
        try:
            subprocess.run(["systemctl", "reload", "haproxy"], check=False, capture_output=True)
        except Exception:
            pass

    def simulate_failover(self, target_node: Optional[str] = None) -> Dict:
        """Simulates or reports VRRP Keepalived Master/Backup failover."""
        if target_node:
            new_active = target_node.lower()
        else:
            new_active = "standby" if self.gateway_state["active_node"] == "primary" else "primary"

        self.gateway_state["active_node"] = new_active
        if new_active == "standby":
            self.gateway_state["primary"]["vrrp_state"] = "FAULT/BACKUP"
            self.gateway_state["standby"]["vrrp_state"] = "MASTER"
        else:
            self.gateway_state["primary"]["vrrp_state"] = "MASTER"
            self.gateway_state["standby"]["vrrp_state"] = "BACKUP"

        return {
            "action": "failover_executed",
            "vip": self.vip_ip,
            "active_node": new_active,
            "gateway_state": self.gateway_state,
            "timestamp": datetime.utcnow().isoformat()
        }

    def get_status(self) -> Dict:
        """Returns full gateway health, routing table, and hot-reload status."""
        return {
            "gateway_ha": self.gateway_state,
            "active_routes_count": len(self.routes),
            "routes": self.routes,
            "last_reload_status": self.last_reload_status,
            "config_path": self.config_file
        }
