# 🚀 FelCloud Resilient IP Optimizer & Sandbox Orchestrator
### **CSTAM-FELCLOUD Challenge — Phase 1: Core Functionalities MVP (50 / 50 Points)**

[![FastAPI](https://img.shields.io/badge/FastAPI-1.0.0-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![OpenStack](https://img.shields.io/badge/OpenStack-Nova%20%7C%20Neutron%20%7C%20Designate-EC1C24?logo=openstack&logoColor=white)](https://www.openstack.org)
[![HAProxy](https://img.shields.io/badge/HAProxy-Zero--Downtime-0052cc?logo=haproxy&logoColor=white)](https://www.haproxy.org)
[![VRRP](https://img.shields.io/badge/Keepalived-VRRP%20HA-brightgreen)](https://www.keepalived.org)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org)

---

## 📌 Présentation du Projet
**FelCloud Resilient IP Optimizer** est un orchestrateur cloud intelligent conçu pour les hackathons et environnements étudiants à haute densité sur **OpenStack**. Il résout la pénurie d'adresses IPv4 et élimine les points uniques de défaillance (SPOF) grâce à :

1. **L'Auto-Provisioning de Sandboxes [15 Pts]** : Création automatisée de VMs OpenStack (Nova/Neutron) avec IP privée dédiée et rattachement dynamique.
2. **Des Passerelles Haute Disponibilité (Dual HA Gateways) [15 Pts]** : Routage de bordure Keepalived (VRRP Protocol 112) + HAProxy avec basculement transparent (< 1s) entre `gw1` (Master) et `gw2` (Backup).
3. **Un Moteur IPAM & Multi-Factor Reclamation [10 Pts]** : Gestion d'inventaire SQLite thread-safe, baux courts (Short Leases), détection d'inactivité de trafic HTTP (Idle Timeout), détection d'extinction (SHUTOFF Grace Period), et libération immédiate à la demande.
4. **Zero-Downtime Hot-Reload & Auto-Rollback en 2 Phases [10 Pts]** : Rechargement des règles de routage HAProxy avec validation syntaxique pré-rechargement (Case A, < 3 ms) et restauration active post-erreur (Case B) sans perte de paquets.
5. **Gestion de l'Alimentation des VMs** : Contrôle du cycle de vie matériel (`start`, `stop`, `reboot`) directement depuis l'API.

---

## 🏛️ Architecture & Diagramme de Flux de Données

```mermaid
flowchart TB
    subgraph External_Traffic["1. Trafic Externe & Navigateurs"]
        Student["Navigateur Utilisateur / Étudiant\n(http://team5.cstam.felcloud.tn)"]
        DNS["OpenStack Designate DNS\n(Record A -> 197.5.133.216)"]
    end

    subgraph Edge_HA_Gateways["2. Passerelles HA Redondantes (gw1 / gw2)"]
        FIP["Floating IP Publique (197.5.133.216)"]
        VIP["VIP Virtuelle (10.0.10.5)\nKeepalived VRRP (Protocol 112)"]
        GW1["gw1 (Primary Master)\nHAProxy + Keepalived"]
        GW2["gw2 (Standby Backup)\nHAProxy + Keepalived"]
        
        FIP --> VIP
        VIP -.->|Actif| GW1
        VIP -.->|Failover < 1s| GW2
        GW1 <-->|Heartbeat VRRP| GW2
    end

    subgraph Control_Plane["3. Plan de Contrôle (FastAPI Orchestrator)"]
        API["FastAPI Orchestrator (app/main.py)"]
        IPAM["Moteur IPAM & Baux SQLite\n(ipam.db)"]
        Worker["Worker Asynchrone de Purge\n(Balayage toutes les 10s)"]
        GatewaySvc["Moteur Hot-Reload &\n2-Phase Auto-Rollback Engine"]
        ComputeSvc["OpenStack Compute Service\n(Nova & Neutron Ports)"]
        DNSSvc["OpenStack DNS Service\n(Designate Provider)"]
    end

    subgraph OpenStack_Infra["4. Infrastructure Réseau Privé (demo_network)"]
        Neutron["Neutron demo_network (10.0.10.0/24)\nAllowed-Address-Pairs (10.0.10.5)"]
        VM1["VM Étudiante: sbx-team5\nIP Privée: 10.0.10.10:80"]
        VM2["VM Étudiante: sbx-alpha\nIP Privée: 10.0.10.11:80"]
    end

    Student --> DNS
    DNS --> FIP
    GW1 -->|Host: team5... -> Proxy vers IP Privée| VM1
    GW1 -->|Host: alpha... -> Proxy vers IP Privée| VM2
    GW2 -.->|Proxy de secours| VM1

    API <--> IPAM
    API --> Worker
    API --> GatewaySvc
    API --> ComputeSvc
    API --> DNSSvc

    GatewaySvc -->|Zero-Downtime Hot-Reload| GW1
    GatewaySvc -->|Zero-Downtime Hot-Reload| GW2

    ComputeSvc --> Neutron
    Neutron --> VM1
    Neutron --> VM2
```

---

## 🚀 Fonctionnalités Détaillées

### 1. Cycle de Vie & Auto-Reclamation Multi-Critères
L'IPAM recycle et libère une adresse IP dès que **l'une** de ces conditions est satisfaite :
* ⏱️ **Expiration du Bail (Lease TTL)** : Le délai imparti (ex: 3600s) est dépassé.
* 💤 **Inactivité de Trafic (Idle Timeout)** : Aucun trafic HTTP / Heartbeat reçu pendant $> 30$ min.
* 🛑 **Machine Éteinte (SHUTOFF Grace Period)** : L'instance est restée éteinte pendant $> 10$ min.
* 🗑️ **Suppression Explicite (Teardown à la demande)** : Appel direct à `DELETE /sandboxes/{team_name}`.

### 2. Dual HA Gateways & Basculement Transparent (VRRP)
* Configuration des ports Neutron avec `allowed-address-pairs` pour porter la VIP `10.0.10.5`.
* Règle de Security Group autorisant le protocole **IP 112 (VRRP)**.
* Basculement automatique en cas de crash de `gw1` vers `gw2` en **moins de 1 seconde**.

### 3. Moteur de Hot-Reload & Auto-Rollback Sub-seconde
* **Phase 1 (Validation Pré-rechargement - Case A)** : Analyse syntaxique (`haproxy -c`) et sémantique (IP/Port/Subdomain). Rejet immédiat de toute configuration invalide en **< 3 ms** avec 0ms d'interruption.
* **Phase 2 (Restauration d'Urgence - Case B)** : En cas d'erreur runtime inattendue, restauration atomique du fichier `/etc/haproxy/haproxy.cfg.bak`.

---

## 🛠️ Installation & Démarrage

### 1. Prérequis
* Python 3.10+
* Accès OpenStack (Fichier de credentials / `.env`)

### 2. Installation des dépendances
```bash
python -m venv venv

# Windows (PowerShell) :
.\venv\Scripts\Activate.ps1

# Linux / macOS :
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Démarrage de l'API
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
* **Swagger UI** : `http://localhost:8000/docs`
* **ReDoc UI** : `http://localhost:8000/redoc`

---

## 🧪 Scripts de Démonstration & Validation (Jury)

### 1. Configuration OpenStack VRRP & VIP
```bash
python setup_openstack_vrrp.py
```

### 2. Preuve du Zero-Downtime Rollback (50 req/s sans interruption)
```bash
python verify_rollback_zero_downtime.py
```

### 3. Benchmark Complet en Direct (Métriques en ms)
```bash
python live_benchmark_demo.py
```

### 4. Suite de Tests Unitaires et d'Intégration
```bash
pytest tests/ -v
```

---

## 📁 Structure du Projet

```text
felcloud-dns/
├── app/
│   ├── main.py                     # Application FastAPI + Worker asynchrone de réclamation
│   ├── api/                        # Routes REST API
│   │   ├── sandboxes.py            # Endpoints Sandboxes (création, teardown, power, heartbeat)
│   │   ├── gateways.py             # Endpoints HA Gateways (status, routes, failover)
│   │   ├── ipam.py                 # Endpoints IPAM (status, allocations, allocate, release)
│   │   └── dns.py                  # Endpoints DNS Designate (records)
│   └── services/                   # Logique métier orchestrée
│       ├── sandbox_service.py      # Orchestrateur 4-en-1 unifié
│       ├── compute_service.py      # Driver OpenStack Nova / Neutron / Power
│       ├── gateway_service.py      # Moteur HAProxy Hot-Reload & 2-Phase Rollback
│       ├── ipam_service.py         # Moteur IPAM SQLite Thread-Safe & Multi-Factor Recycler
│       └── dns_service.py          # Driver OpenStack Designate DNS
├── deploy/ha_gateways/             # Configurations Keepalived & HAProxy pour gw1/gw2
├── tests/                          # Tests unitaires & intégration automatisés
├── ARCHITECTURE_PHASE1.md          # Spécification d'architecture détaillée
├── DEMO_VIDEO_GUIDE.md             # Guide pas-à-pas pour l'enregistrement vidéo (3 min)
├── setup_openstack_vrrp.py         # Script d'automatisation Neutron VIP & VRRP
├── verify_rollback_zero_downtime.py# Test de charge & rollback sans coupure (50 req/s)
├── live_benchmark_demo.py          # Benchmark chiffré des latences (ms)
├── requirements.txt                # Dépendances Python
└── .env.example                    # Modèle des variables d'environnement
```

---

## 👥 Équipe & Remerciements
* **Projet** : CSTAM-FELCLOUD Challenge
* **Mentors** : M. Mohamed Mongi Benyaiche (CEO FelCloud) & M. Oussama Zied (Cloud & DevOps Engineer)
* **Événement** : IEEE Computer Society Tunisian Annual Meeting (CSTAM 3.0)
