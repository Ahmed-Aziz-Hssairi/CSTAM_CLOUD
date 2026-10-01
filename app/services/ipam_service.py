import ipaddress
import sqlite3
import os
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dotenv import load_dotenv

load_dotenv()


class IPAMService:
    """
    IP Address Management (IPAM) Service.
    Manages dynamic IP pool allocation, short leases, auto-reclamation, and persistence using SQLite.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        cidr: Optional[str] = None,
        start_ip: Optional[str] = None,
        end_ip: Optional[str] = None,
        default_lease_seconds: int = 3600
    ):
        self.db_path = db_path or os.getenv("IPAM_DB_PATH", "ipam.db")
        self.cidr_str = cidr or os.getenv("IPAM_CIDR", "10.0.0.0/24")
        self.start_ip_str = start_ip or os.getenv("IPAM_START_IP", "10.0.0.10")
        self.end_ip_str = end_ip or os.getenv("IPAM_END_IP", "10.0.0.250")
        self.default_lease_seconds = int(os.getenv("IPAM_DEFAULT_LEASE", str(default_lease_seconds)))
        
        self.lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ip_pool (
                        ip_address TEXT PRIMARY KEY,
                        is_allocated INTEGER DEFAULT 0,
                        allocated_to TEXT UNIQUE,
                        allocated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        lease_duration_seconds INTEGER DEFAULT 3600,
                        expires_at TIMESTAMP,
                        last_activity_at TIMESTAMP,
                        shutoff_since TIMESTAMP
                    )
                """)
                conn.commit()

                # Migration checks for existing databases
                cursor.execute("PRAGMA table_info(ip_pool)")
                columns = [col[1] for col in cursor.fetchall()]
                if "lease_duration_seconds" not in columns:
                    cursor.execute("ALTER TABLE ip_pool ADD COLUMN lease_duration_seconds INTEGER DEFAULT 3600")
                if "expires_at" not in columns:
                    cursor.execute("ALTER TABLE ip_pool ADD COLUMN expires_at TIMESTAMP")
                if "last_activity_at" not in columns:
                    cursor.execute("ALTER TABLE ip_pool ADD COLUMN last_activity_at TIMESTAMP")
                if "shutoff_since" not in columns:
                    cursor.execute("ALTER TABLE ip_pool ADD COLUMN shutoff_since TIMESTAMP")
                conn.commit()

                # Seed IP pool if empty
                cursor.execute("SELECT COUNT(*) as count FROM ip_pool")
                row = cursor.fetchone()
                if row["count"] == 0:
                    self._seed_pool(cursor)
                    conn.commit()

    def _seed_pool(self, cursor: sqlite3.Cursor):
        """Populates the pool with usable IPs between start_ip and end_ip."""
        network = ipaddress.ip_network(self.cidr_str, strict=False)
        start_int = int(ipaddress.IPv4Address(self.start_ip_str))
        end_int = int(ipaddress.IPv4Address(self.end_ip_str))

        for host in network.hosts():
            host_int = int(host)
            if start_int <= host_int <= end_int:
                cursor.execute(
                    """INSERT OR IGNORE INTO ip_pool 
                       (ip_address, is_allocated, allocated_to, allocated_at, lease_duration_seconds, expires_at) 
                       VALUES (?, 0, NULL, NULL, NULL, NULL)""",
                    (str(host),)
                )

    def allocate_ip(self, team_name: str, lease_seconds: Optional[int] = None) -> Dict[str, any]:
        """
        Allocates an available IP to a team with a defined lease duration.
        If the team already has an allocated IP, extends its lease and returns it.
        """
        team_name = team_name.strip().lower()
        duration = lease_seconds or self.default_lease_seconds
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=duration)
        expires_at_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if team already has an IP
                cursor.execute(
                    "SELECT ip_address, expires_at FROM ip_pool WHERE allocated_to = ?",
                    (team_name,)
                )
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE ip_pool
                        SET lease_duration_seconds = ?, expires_at = ?
                        WHERE allocated_to = ?
                    """, (duration, expires_at_str, team_name))
                    conn.commit()
                    return {
                        "ip_address": existing["ip_address"],
                        "allocated_to": team_name,
                        "lease_duration_seconds": duration,
                        "expires_at": expires_at_str,
                        "status": "renewed"
                    }

                # Find first available IP (lowest IP address order)
                cursor.execute("""
                    SELECT ip_address FROM ip_pool
                    WHERE is_allocated = 0
                    ORDER BY ip_address ASC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if not row:
                    raise Exception("IPAM pool exhausted: No available IP addresses in the pool.")

                allocated_ip = row["ip_address"]
                cursor.execute("""
                    UPDATE ip_pool
                    SET is_allocated = 1, 
                        allocated_to = ?, 
                        allocated_at = ?, 
                        lease_duration_seconds = ?, 
                        expires_at = ?,
                        last_activity_at = ?,
                        shutoff_since = NULL
                    WHERE ip_address = ?
                """, (team_name, now_str, duration, expires_at_str, now_str, allocated_ip))
                conn.commit()

                return {
                    "ip_address": allocated_ip,
                    "allocated_to": team_name,
                    "lease_duration_seconds": duration,
                    "expires_at": expires_at_str,
                    "status": "allocated"
                }

    def record_activity(self, team_name: str) -> bool:
        """Updates last_activity_at timestamp to now (Heartbeat / HTTP traffic hit)."""
        team_name = team_name.strip().lower()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE ip_pool
                    SET last_activity_at = ?
                    WHERE allocated_to = ? AND is_allocated = 1
                """, (now_str, team_name))
                conn.commit()
                return cursor.rowcount > 0

    def update_shutoff_state(self, team_name: str, is_shutoff: bool) -> bool:
        """Tracks when a VM enters SHUTOFF state, or resets it when ACTIVE."""
        team_name = team_name.strip().lower()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if is_shutoff:
                    cursor.execute("""
                        UPDATE ip_pool
                        SET shutoff_since = COALESCE(shutoff_since, ?)
                        WHERE allocated_to = ? AND is_allocated = 1
                    """, (now_str, team_name))
                else:
                    cursor.execute("""
                        UPDATE ip_pool
                        SET shutoff_since = NULL
                        WHERE allocated_to = ? AND is_allocated = 1
                    """, (team_name,))
                conn.commit()
                return cursor.rowcount > 0

    def release_ip(self, team_name: str) -> Optional[str]:
        """
        Releases the IP assigned to a team so it can be reused immediately.
        """
        team_name = team_name.strip().lower()
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT ip_address FROM ip_pool WHERE allocated_to = ?",
                    (team_name,)
                )
                row = cursor.fetchone()
                if not row:
                    return None

                released_ip = row["ip_address"]
                cursor.execute("""
                    UPDATE ip_pool
                    SET is_allocated = 0, allocated_to = NULL, allocated_at = NULL, 
                        lease_duration_seconds = NULL, expires_at = NULL,
                        last_activity_at = NULL, shutoff_since = NULL
                    WHERE ip_address = ?
                """, (released_ip,))
                conn.commit()

                return released_ip

    def get_team_ip(self, team_name: str) -> Optional[Dict]:
        """Returns the IP details assigned to a team, or None if not assigned."""
        team_name = team_name.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ip_address, allocated_to, allocated_at, lease_duration_seconds, expires_at, last_activity_at, shutoff_since FROM ip_pool WHERE allocated_to = ?",
                (team_name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def reclaim_expired_leases(
        self,
        idle_timeout_seconds: Optional[int] = None,
        shutoff_timeout_seconds: Optional[int] = None
    ) -> List[Dict]:
        """
        Multi-Factor Reclamation Engine:
        1. Base Expiry: Lease duration expired (expires_at <= now).
        2. Method 1 (Idle Traffic): Inactive for > idle_timeout_seconds.
        3. Method 4 (Prolonged Shutoff): Stopped / SHUTOFF for > shutoff_timeout_seconds.
        """
        now = datetime.utcnow()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        idle_cutoff_str = None
        if idle_timeout_seconds and idle_timeout_seconds > 0:
            idle_cutoff_str = (now - timedelta(seconds=idle_timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")

        shutoff_cutoff_str = None
        if shutoff_timeout_seconds and shutoff_timeout_seconds > 0:
            shutoff_cutoff_str = (now - timedelta(seconds=shutoff_timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                query = """
                    SELECT ip_address, allocated_to, expires_at, last_activity_at, shutoff_since,
                           CASE 
                               WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 'lease_expired'
                               WHEN ? IS NOT NULL AND last_activity_at IS NOT NULL AND last_activity_at <= ? THEN 'idle_traffic_timeout'
                               WHEN ? IS NOT NULL AND shutoff_since IS NOT NULL AND shutoff_since <= ? THEN 'shutoff_timeout'
                               ELSE 'unknown'
                           END as reclamation_reason
                    FROM ip_pool
                    WHERE is_allocated = 1 AND (
                        (expires_at IS NOT NULL AND expires_at <= ?)
                        OR (? IS NOT NULL AND last_activity_at IS NOT NULL AND last_activity_at <= ?)
                        OR (? IS NOT NULL AND shutoff_since IS NOT NULL AND shutoff_since <= ?)
                    )
                """
                params = (
                    now_str,
                    idle_cutoff_str, idle_cutoff_str,
                    shutoff_cutoff_str, shutoff_cutoff_str,
                    now_str,
                    idle_cutoff_str, idle_cutoff_str,
                    shutoff_cutoff_str, shutoff_cutoff_str
                )

                cursor.execute(query, params)
                expired_rows = cursor.fetchall()
                reclaimed = [dict(r) for r in expired_rows]

                if reclaimed:
                    ips_to_free = [r["ip_address"] for r in reclaimed]
                    placeholders = ",".join("?" * len(ips_to_free))
                    cursor.execute(f"""
                        UPDATE ip_pool
                        SET is_allocated = 0, allocated_to = NULL, allocated_at = NULL,
                            lease_duration_seconds = NULL, expires_at = NULL,
                            last_activity_at = NULL, shutoff_since = NULL
                        WHERE ip_address IN ({placeholders})
                    """, ips_to_free)
                    conn.commit()

                return reclaimed

    def list_allocations(self) -> List[Dict]:
        """Lists all active allocations with lease status."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ip_address, allocated_to as team_name, allocated_at, lease_duration_seconds, expires_at, last_activity_at, shutoff_since
                FROM ip_pool
                WHERE is_allocated = 1
                ORDER BY ip_address ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_pool_status(self) -> Dict:
        """Returns pool statistics (total, allocated, available, range)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM ip_pool")
            total = cursor.fetchone()["total"]

            cursor.execute("SELECT COUNT(*) as allocated FROM ip_pool WHERE is_allocated = 1")
            allocated = cursor.fetchone()["allocated"]

            return {
                "total_ips": total,
                "allocated_ips": allocated,
                "available_ips": total - allocated,
                "cidr": self.cidr_str,
                "range": f"{self.start_ip_str} - {self.end_ip_str}",
                "default_lease_seconds": self.default_lease_seconds
            }
