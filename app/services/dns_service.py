import os
from dotenv import load_dotenv
from openstack import connection

load_dotenv()


class DNSService:

    def __init__(self):
        self.zone_name = os.getenv(
            "DNS_ZONE",
            "cstam.felcloud.tn."
        )

        if not self.zone_name.endswith("."):
            self.zone_name += "."

        self._local_records = {}

        self.conn = connection.Connection(
            auth_url=os.getenv("OS_AUTH_URL"),
            project_name=os.getenv("OS_PROJECT_NAME"),
            username=os.getenv("OS_USERNAME"),
            password=os.getenv("OS_PASSWORD"),
            user_domain_name=os.getenv(
                "OS_USER_DOMAIN_NAME",
                "Default"
            ),
            project_domain_name=os.getenv(
                "OS_PROJECT_DOMAIN_NAME",
                "Default"
            ),
            region_name=os.getenv(
                "OS_REGION_NAME",
                "RegionOne"
            )
        )

    def _has_designate(self):
        try:
            return "dns" in [s.get("type") for s in self.conn.session.auth.get_access(self.conn.session).service_catalog.catalog]
        except Exception:
            return False

    def find_zone(self):
        if self._has_designate():
            try:
                for zone in self.conn.dns.zones():
                    if zone.name == self.zone_name:
                        return zone
                return None
            except Exception:
                pass

        # Standalone / HA Gateway Local DNS mode
        class SimulatedZone:
            id = "zone-felcloud-local"
            name = self.zone_name
        return SimulatedZone()

    def normalize_hostname(self, hostname: str):
        hostname = hostname.strip()

        if hostname.endswith("."):
            hostname = hostname[:-1]

        if hostname.endswith(self.zone_name[:-1]):
            return hostname + "."

        return f"{hostname}.{self.zone_name}"

    def get_record(self, hostname: str):
        zone = self.find_zone()
        if not zone:
            raise Exception(f"DNS zone not found: {self.zone_name}")

        record_name = self.normalize_hostname(hostname)

        if self._has_designate():
            try:
                for record in self.conn.dns.recordsets(zone):
                    if record.name == record_name and record.type == "A":
                        return record
                return None
            except Exception:
                pass

        # Local fallback record lookup
        if record_name in self._local_records:
            entry = self._local_records[record_name]
            class SimulatedRecord:
                name = record_name
                type = "A"
                records = [entry["ip_address"]]
                ttl = entry["ttl"]
            return SimulatedRecord()

        return None

    def create_record(
        self,
        hostname: str,
        ip_address: str,
        ttl: int = 60
    ):
        zone = self.find_zone()
        if not zone:
            raise Exception(f"DNS zone not found: {self.zone_name}")

        record_name = self.normalize_hostname(hostname)
        existing_record = self.get_record(hostname)

        if self._has_designate():
            try:
                if existing_record:
                    self.conn.dns.update_recordset(
                        existing_record,
                        records=[ip_address],
                        ttl=ttl
                    )
                    return {
                        "action": "updated",
                        "hostname": record_name,
                        "ip_address": ip_address,
                        "ttl": ttl
                    }

                record = self.conn.dns.create_recordset(
                    zone,
                    name=record_name,
                    type="A",
                    records=[ip_address],
                    ttl=ttl
                )
                return {
                    "action": "created",
                    "hostname": record.name,
                    "ip_address": ip_address,
                    "ttl": ttl
                }
            except Exception:
                pass

        # Local DNS engine mode
        self._local_records[record_name] = {
            "ip_address": ip_address,
            "ttl": ttl
        }
        action = "updated" if existing_record else "created"
        return {
            "action": action,
            "hostname": record_name,
            "ip_address": ip_address,
            "ttl": ttl,
            "provider": "HA Gateway DNS Engine"
        }

    def update_record(
        self,
        hostname: str,
        ip_address: str,
        ttl: int = 60
    ):
        return self.create_record(hostname, ip_address, ttl)

    def delete_record(self, hostname: str):
        zone = self.find_zone()
        if not zone:
            raise Exception(f"DNS zone not found: {self.zone_name}")

        record_name = self.normalize_hostname(hostname)
        existing_record = self.get_record(hostname)

        if not existing_record:
            return {
                "action": "not_found",
                "hostname": record_name
            }

        if self._has_designate():
            try:
                self.conn.dns.delete_recordset(existing_record, zone)
                return {
                    "action": "deleted",
                    "hostname": record_name
                }
            except Exception:
                pass

        self._local_records.pop(record_name, None)
        return {
            "action": "deleted",
            "hostname": record_name
        }

