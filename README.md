# 🛡️ Sentinel-X — Plateforme Complète Intégrée (Groupe 16)

Projet unifié combinant l'infrastructure IoT (MQTTS, TimescaleDB, Grafana, API Ingestion) et la vision par ordinateur biométrique (Reconnaissance Faciale SFace + YuNet + MediaPipe).

---

## 🏗️ Architecture Globale

```
                         [ESP8266 Boîtier IoT]
                                  │
                       (MQTTS 8883 / TLS)
                                  │
                                  ▼
┌─────────────────────────── Docker Compose ───────────────────────────┐
│                                                                      │
│   ┌─────────────────┐           ┌─────────────────┐                  │
│   │  sentinel_mqtt  │ ◄───────► │  biometric-     │ (Flask :5000)    │
│   │ (Mosquitto 2.x) │           │  vision         │ (YuNet + SFace)  │
│   │ [1883 / 8883]   │           └─────────────────┘                  │
│   └────────┬────────┘                    ▲                           │
│            │                             │ POST /api/v1/alerts       │
│            ▼                             │                           │
│   ┌─────────────────┐           ┌────────┴────────┐                  │
│   │ sentinel_api_   │           │ scripts/        │ (YOLOv8 +        │
│   │ ingest (Python) │           │ vision / IF     │  IsolationForest)│
│   └────────┬────────┘           └─────────────────┘                  │
│            │                                                         │
│            ▼                                                         │
│   ┌─────────────────┐           ┌─────────────────┐                  │
│   │   sentinel_db   │ ◄───────► │ sentinel_       │                  │
│   │  (TimescaleDB)  │           │ grafana         │                  │
│   │    [:5432]      │           │    [:3000]      │                  │
│   └─────────────────┘           └─────────────────┘                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Services Conteneurisés

| Service | Conteneur | Port Hôte | Description |
| :--- | :--- | :--- | :--- |
| **Broker MQTT** | `sentinel_mqtt` | `1883`, `8883` | Eclipse Mosquitto avec support TLS (MQTTS) et authentification sécurisée. |
| **Base de Données** | `sentinel_db` | `5432` | TimescaleDB (PostgreSQL 15) pour stockage séries temporelles et alertes. |
| **API Ingestion** | `sentinel_api_ingest` | Interne | Service Python souscrivant aux topics `sentinel/#` et écrivant en base. |
| **Supervision** | `sentinel_grafana` | `3000` | Tableaux de bord Grafana avec datasource TimescaleDB pré-provisionnée. |
| **Vision Biométrique** | `sentinel_biometric_vision` | `5000` | Dashboard web Flask, scan facial 478 points, identification et alertes MQTT. |

---

## 🚀 Démarrage Rapide

### 1. Lancement de la stack complète

Depuis la racine du projet :

```bash
docker compose up -d --build
```

Pour vérifier que tous les services sont démarrés :
```bash
docker compose ps
```

Pour consulter les logs en temps réel :
```bash
docker compose logs -f
```

### 2. Accès aux Interfaces Web

- **🔬 Vision Biométrique & Scan Facial** : [http://localhost:5000](http://localhost:5000)
- **📊 Tableau de bord Grafana** : [http://localhost:3000](http://localhost:3000)
  - Identifiants : `admin` / `sentinel_admin`
  - La source de données **Sentinel TimescaleDB** est connectée automatiquement.
- **📡 Broker MQTT** :
  - Port `1883` : MQTT standard
  - Port `8883` : MQTTS chiffré TLS (Certificats dans `mosquitto/config/certs/`)
  - Identifiants : `esp_client` / `SuperSecret123`

---

## 🔒 Sécurité & Chiffrement TLS (MQTTS)

Les certificats X.509 générés sont situés dans `mosquitto/config/certs/` :
- `ca.crt` : Certificat de l'Autorité de Certification interne (à embarquer dans le firmware ESP8266 `CA_CERT`).
- `server.crt` & `server.key` : Certificat et clé privée du serveur Mosquitto.

Les secrets (`*.key`, `passwd`, `.env`) sont protégés dans `.gitignore`.

---

## 📁 Schéma de la Base de Données (`timescaledb/init.sql`)

1. **`sensor_data`** : Table normalisée (`time`, `device_id`, `sensor_type`, `value`).
2. **`mesures`** : Table à colonnes larges (`ts`, `device_id`, `temp`, `hum`, `gaz`, `presence`).
3. **`alertes`** : Table des alertes de sécurité (`ts`, `source`, `type`, `severite`, `details`).

---

## 🤖 Modules IA & Scripts Complémentaires (`scripts/`)

- [**scripts/vision_yolo.py**](file:///c:/Users/axelm/Downloads/workshop/scripts/vision_yolo.py) : Détection de présence humaine via YOLOv8n (`person`, classe 0) et transmission automatique des alertes vers `POST /api/v1/alerts`.
- [**scripts/predictif_sentinel.py**](file:///c:/Users/axelm/Downloads/workshop/scripts/predictif_sentinel.py) : Maintenance prédictive par modèle `IsolationForest` (détection d'anomalies corrélées température/humidité/gaz).

Exécution locale d'un script :
```bash
python scripts/vision_yolo.py
# ou
python scripts/predictif_sentinel.py
```

---

## 🛠️ Arrêt de la Stack

```bash
docker compose down
```
Pour supprimer également les volumes persistants de données :
```bash
docker compose down -v
```

