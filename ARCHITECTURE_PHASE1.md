# System Architecture & OpenStack HA Gateway Data Flow

## 📌 Project: FelCloud Resilient IP Optimizer (CSTAM-FELCLOUD)
### **Phase 1: Core Functionalities MVP (50 Points)**

---

## 1. Global System Architecture

```mermaid
flowchart TB
    subgraph External_Traffic["1. Internet Traffic & Students"]
        User["User / Student Browser\n(http://team5.cstam.felcloud.tn)"]
        Admin["Admin / CI-CD API Client"]
    end

    subgraph Edge_HA_Gateways["2. Dual Redundant HA Edge Gateways"]
        FIP["Gateway Public Floating IP\n(Reachable from Internet)"]
        VIP["Virtual IP (VIP 10.0.0.5)\n(Keepalived / VRRP Protocol 112)"]
        GW1["gw1 (Primary Master)\nHAProxy + Keepalived"]
        GW2["gw2 (Standby Backup)\nHAProxy + Keepalived"]
        
        FIP --> VIP
        VIP -.->|Active| GW1
        VIP -.->|Failover| GW2
    end

    subgraph FelCloud_Control_Plane["3. FelCloud Resilient Control Plane"]
        FastAPI["FastAPI Orchestrator API\n(app/main.py)"]
        IPAM["IPAM & Reclamation Engine\n(SQLite + Short Leases)"]
        Worker["Async Lease Purge Worker\n(Runs every 10s)"]
        ComputeSvc["Compute Service\n(Nova & Neutron SDK)"]
        DNSSvc["DNS Service\n(Designate SDK)"]
        GWSvc["Gateway Hot-Reload &\n2-Phase Auto-Rollback Engine"]
    end

    subgraph OpenStack_Infra["4. OpenStack Cloud Infrastructure"]
        Neutron["Neutron Network\n(demo_network / demo_subnet)"]
        Designate["OpenStack Designate\n(cstam.felcloud.tn.)"]
        VM1["Student VM: sbx-team5\nPrivate IP: 10.0.0.10:80"]
        VM2["Student VM: sbx-team6\nPrivate IP: 10.0.0.11:80"]
    end

    Admin -->|REST API| FastAPI
    FastAPI <--> IPAM
    FastAPI --> Worker
    FastAPI --> ComputeSvc
    FastAPI --> DNSSvc
    FastAPI --> GWSvc

    DNSSvc -->|team5 -> Gateway Floating IP| Designate
    ComputeSvc -->|Nova / Neutron Port with Fixed IP| Neutron
    Neutron --> VM1
    Neutron --> VM2

    GWSvc -->|Zero-Downtime Hot-Reload| GW1
    GWSvc -->|Zero-Downtime Hot-Reload| GW2

    User --> FIP
    GW1 -->|Host: team5... -> Proxy to 10.0.0.10:80| VM1
    GW2 -->|Backup Proxy Route| VM1
```

---

## 2. End-to-End Data Flow: Sandbox Auto-Provisioning

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Client / Admin API
    participant API as FastAPI Orchestrator
    participant IPAM as IPAM Engine
    participant Nova as OpenStack Nova/Neutron
    participant DNS as OpenStack Designate
    participant GW as HA Edge Gateways

    Admin->>API: POST /sandboxes {"team_name": "team5", "lease_seconds": 3600}
    API->>IPAM: allocate_ip("team5", lease=3600s)
    IPAM-->>API: Allocated Private IP: 10.0.0.10 (expires in 1h)
    
    API->>Nova: create_sandbox_vm("sbx-team5", ip="10.0.0.10")
    Nova-->>API: VM Created with Fixed Private IP
    
    API->>DNS: create_record("team5", target="GATEWAY_FLOATING_IP", ttl=60)
    DNS-->>API: DNS Record Created (team5.cstam.felcloud.tn -> Floating IP)
    
    API->>GW: add_route("team5", "10.0.0.10:80")
    GW->>GW: Validate syntax (AST & haproxy -c)
    alt Case A: Syntax Rejection
        GW->>GW: Discard Candidate (Rollback Case A)
    else Syntax Valid
        GW->>GW: Atomic Swap & Daemon Reload
        alt Case B: Runtime Daemon Failure
            GW->>GW: Restore from .bak (Rollback Case B)
        else Reload Success
            GW->>GW: Zero-Downtime Reload Complete
        end
    end
    GW-->>API: Reload Successful (Routes Synchronized on gw1 & gw2)
    API-->>Admin: {"status": "ready", "fqdn": "team5.cstam.felcloud.tn", "private_ip": "10.0.0.10"}
```

---

## 3. High Availability & Failover Breakdown

1. **Floating IP & VRRP Allowed-Address-Pairs**:
   - The Public Floating IP forwards all web traffic to the internal Virtual IP (VIP `10.0.0.5`).
   - OpenStack Neutron port security allows VIP packets via `allowed-address-pairs` on `gw1` and `gw2`.
   - Security Group explicitly permits IP Protocol 112 (VRRP).
2. **Two-Phase Auto-Rollback Engine**:
   - **Case A (Pre-reload Rejection)**: Invalid syntax / IP / Port caught before reload. Candidate discarded, 0ms downtime.
   - **Case B (Post-reload Recovery)**: Runtime failure triggers active restoration of `/etc/haproxy/haproxy.cfg.bak`.
3. **Automated IPAM & Continuous Reclamation**:
   - Asynchronous background worker scans the database every 10 seconds.
   - Any expired lease immediately deletes the associated VM, clears the DNS record, updates the Gateways, and frees the IP address for new teams.
