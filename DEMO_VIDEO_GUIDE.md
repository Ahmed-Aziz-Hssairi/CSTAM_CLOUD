# 🎬 Guide d'Enregistrement Vidéo - Démonstration Phase 1 (50 Points)

## 📌 Objectif de la vidéo (Exigé par le sujet CSTAM-FELCLOUD) :
> *"Short video demonstration showcasing active subdomain routing, dynamic hot-reloading, and simulated primary gateway failover"*

---

## ⏱️ Durée recommandée : **2 à 3 minutes**

---

## 📽️ Scénario de la Démonstration (Plan séquence)

### 🔹 Étape 1 : Présentation de l'Architecture & Démarrage de l'API (30s)
1. **Montrer l'interface OpenStack** :
   * Montrer la topologie réseau (`demo_network`, `gw1`, `gw2`, et les VMs).
2. **Démarrer l'API FastAPI** :
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   * Ouvrir Swagger UI sur `http://localhost:8000/docs`.

---

### 🔹 Étape 2 : Provisioning & Routage Dynamique du Sous-domaine (45s)
1. **Appeler l'API de création** :
   * Exécuter `POST /sandboxes` avec `team_name = "team5"`, `lease_seconds = 300`.
2. **Montrer le résultat** :
   * L'adresse IP privée attribuée par l'IPAM (ex: `10.0.0.10`).
   * L'enregistrement DNS créé dans OpenStack Designate (`team5.cstam.felcloud.tn.`).
   * La route HAProxy rechargée sans coupure.
3. **Tester la résolution** :
   * Ouvrir le navigateur sur `http://team5.cstam.felcloud.tn` ou faire un `curl -H "Host: team5.cstam.felcloud.tn" http://10.0.0.5`.

---

### 🔹 Étape 3 : Démonstration du Sub-Second Auto-Rollback (45s)
1. **Injecter volontairement une mauvaise configuration** :
   * Envoyer une requête avec une IP corrompue (ex: `POST /gateways/routes` avec `ip_address = "999.999.999.999"`).
2. **Montrer la réaction du système** :
   * L'API intercepte l'erreur de syntaxe.
   * L'Auto-Rollback est exécuté en **< 30 millisecondes**.
   * Le trafic existant vers `team5.cstam.felcloud.tn` continue de fonctionner **sans aucune coupure** (0ms downtime).

---

### 🔹 Étape 4 : Démonstration du Basculement HA Gateway (Failover) (45s)
1. **Montrer l'état des deux passerelles** :
   * `gw1` est **Master (VIP 10.0.0.5 active)**.
   * `gw2` est **Standby (Backup)**.
2. **Simuler la panne de `gw1`** :
   * Stopper le service ou exécuter `POST /gateways/failover`.
3. **Prouver la bascule instantanée** :
   * `gw2` prend la main sur la VIP.
   * La requête vers `team5.cstam.felcloud.tn` répond toujours sans interruption.

---

### 🔹 Étape 5 : Exécution du Script Automatisé de Benchmark
Pour afficher toutes les métriques en direct devant le jury, lancez le script :
```bash
python live_benchmark_demo.py
```
Ce script affichera directement les temps mesurés en millisecondes pour chaque scénario.
