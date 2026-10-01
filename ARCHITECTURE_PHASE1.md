# System Architecture & OpenStack HA Gateway Data Flow

## 📌 Project: FelCloud Resilient IP Optimizer (CSTAM-FELCLOUD)
### **Phase 1: Core Functionalities MVP (50 Points Submission Document)**

---

## 1. Global System Architecture

```mermaid
flowchart TB
    subgraph External_Traffic["1. Internet Traffic & Student Subdomains"]
        User["User / Student Browser\n(http://team5.cstam.felcloud.tn)"]
        Admin["Admin / Evaluator API Client"]
    end

    subgraph Edge_HA_Gateways["2. Dual Redundant HA Edge Gateways"]
        FIP["Gateway Public Floating IP\n(197.5.133.216)"]
        VIP["Virtual IP (VIP 10.0.10.5)\n(Keepalived / VRRP Protocol 112)"]
        GW1["gw1 (Primary Master)\nHAProxy + Keepalived"]
        GW2["gw2 (Standby Backup)\nHAProxy + Keepalived"]
        
        FIP --> VIP
        VIP -.->|Active Node| GW1
        VIP -.->|Sub-second Failover| GW2
        GW1 <-->|Heartbeat VRRP| GW2
    end

    subgraph FelCloud_Control_Plane["3. FelCloud Resilient Control Plane"]
        FastAPI["FastAPI Orchestrator API\n(app/main.py)"]
        IPAM["IPAM & Multi-Factor Reclamation Engine\n(SQLite + Short Leases + Idle/Shutoff Tracking)"]
        Worker["Async Lease Purge Worker\n(Continuous 10s Scans)"]
        ComputeSvc["Compute Service\n(Nova & Neutron SDK + Power Controls)"]
        DNSSvc["DNS Service\n(OpenStack Designate Provider)"]
        GWSvc["Gateway Hot-Reload &\n2-Phase Auto-Rollback Engine"]
    end

    subgraph OpenStack_Infra["4. OpenStack Cloud Infrastructure (demo_network)"]
        Neutron["Neutron demo_network (10.0.10.0/24)\nAllowed-Address-Pairs Configured"]
        Designate["OpenStack Designate\n(cstam.felcloud.tn. Zone)"]
        VM1["Student VM: sbx-team5\nPrivate IP: 10.0.10.10:80"]
        VM2["Student VM: sbx-alpha\nPrivate IP: 10.0.10.11:80"]
    end

    Admin -->|REST API Requests| FastAPI
    FastAPI <--> IPAM
    FastAPI --> Worker
    FastAPI --> ComputeSvc
    FastAPI --> DNSSvc
    FastAPI --> GWSvc

    DNSSvc -->|team5 -> Gateway Floating IP| Designate
    ComputeSvc -->|Nova / Dedicated Neutron Port with Fixed IP| Neutron
    Neutron --> VM1
    Neutron --> VM2

    GWSvc -->|Zero-Downtime Hot-Reload| GW1
    GWSvc -->|Zero-Downtime Hot-Reload| GW2

    User --> FIP
    GW1 -->|Host: team5... -> Reverse Proxy to 10.0.10.10:80| VM1
    GW1 -->|Host: alpha... -> Reverse Proxy to 10.0.10.11:80| VM2
    GW2 -.->|Backup Reverse Proxy| VM1
```

---

## 2. End-to-End Data Flow: Sandbox Auto-Provisioning

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Client / Admin API
    participant API as FastAPI Orchestrator
    participant IPAM as IPAM Engine (ipam.db)
    participant Nova as OpenStack Nova/Neutron
    participant DNS as OpenStack Designate
    participant GW as HA Edge Gateways (gw1/gw2)

    Admin->>API: POST /sandboxes {"team_name": "team5", "lease_seconds": 3600}
    
    rect rgb(235, 245, 255)
    Note over API,IPAM: Step 1: Thread-Safe IPAM Private IP Allocation
    API->>IPAM: allocate_ip("team5", lease=3600s)
    IPAM-->>API: Allocated Private IP: 10.0.10.10 (expires in 1h)
    end

    rect rgb(240, 255, 240)
    Note over API,Nova: Step 2: OpenStack Compute & Dedicated Port
    API->>Nova: create_sandbox_vm("sbx-team5", ip="10.0.10.10")
    Nova-->>API: VM Created & Attached to Port 10.0.10.10
    end

    rect rgb(255, 250, 235)
    Note over API,DNS: Step 3: Designate Subdomain Registration
    API->>DNS: create_record("team5", target="197.5.133.216", ttl=60)
    DNS-->>API: DNS Record Created (team5.cstam.felcloud.tn -> Floating IP)
    end

    rect rgb(255, 240, 245)
    Note over API,GW: Step 4: Zero-Downtime HAProxy Hot-Reload
    API->>GW: add_route("team5", "10.0.10.10:80")
    GW->>GW: Pre-reload Validation (AST syntax & haproxy -c)
    alt Case A: Syntax Rejection
        GW->>GW: Discard candidate (< 3 ms Auto-Rollback)
    else Syntax Valid
        GW->>GW: Atomic Swap & Daemon Seamless Reload
        alt Case B: Runtime Daemon Failure
            GW->>GW: Restore from haproxy.cfg.bak
        else Reload Success
            GW->>GW: Zero-Downtime Reload Complete (0 dropped requests)
        end
    end
    GW-->>API: Reload Successful (Routes Synchronized on gw1 & gw2)
    end

    API-->>Admin: {"status": "ready", "fqdn": "team5.cstam.felcloud.tn", "private_ip": "10.0.10.10"}
```

---

## 3. Multi-Factor Reclamation & IP Recycling Flow

The IPAM and Sandbox engine purges and recycles an IP address upon any of the following events:

```mermaid
flowchart TD
    VM["Active Sandbox Instance (sbx-team5)"] --> Trigger{"Trigger Event"}
    
    Trigger -->|1. Explicit Teardown| Del["DELETE /sandboxes/team5"]
    Trigger -->|2. Lease Expiration| Exp["Bail dépassé (t > expires_at)"]
    Trigger -->|3. Inactivité Réseau| Idle["0 trafic HTTP pendant > 30 min (Idle Timeout)"]
    Trigger -->|4. Machine Éteinte| Shut["Instance SHUTOFF > 10 min (Grace Period)"]
    
    Del --> Purge["Auto-Purge Pipeline"]
    Exp --> Purge
    Idle --> Purge
    Shut --> Purge

    Purge --> P1["1. Nova VM & Port Neutron détruits"]
    Purge --> P2["2. Enregistrement DNS Designate supprimé"]
    Purge --> P3["3. Route HAProxy retirée à chaud"]
    Purge --> P4["4. IP remise à 0 dans ipam.db"]
    
    P4 --> Ready["IP instantanément réattribuée à la team suivante !"]
```

---

## 4. High Availability & Failover Technical Breakdown

1. **Floating IP & VRRP Allowed-Address-Pairs**:
   - The Public Floating IP (`197.5.133.216`) forwards all incoming web traffic to the internal Virtual IP (VIP `10.0.10.5`).
   - OpenStack Neutron port security allows VIP packets via `allowed-address-pairs` on `gw1` and `gw2`.
   - Security Group explicitly permits **IP Protocol 112 (VRRP)**.
2. **Two-Phase Auto-Rollback Engine**:
   - **Case A (Pre-reload Rejection)**: Invalid syntax / IP / Port caught before reload. Candidate discarded in **< 3 ms** with 0ms downtime.
   - **Case B (Post-reload Recovery)**: Runtime failure triggers active restoration of `/etc/haproxy/haproxy.cfg.bak`.
3. **Automated IPAM & Continuous Reclamation**:
   - Asynchronous background worker scans the database every 10 seconds.
   - Reclaims expired leases, idle sandboxes, and abandoned shutoff VMs, freeing IPv4 addresses for new teams.
