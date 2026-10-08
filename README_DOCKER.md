# 🐳 Déploiement Docker — Stack Complète Sentinel-X (Groupe 16)

Guide d'orchestration de l'ensemble des conteneurs : Broker Mosquitto (MQTTS), TimescaleDB, Ingestion MQTT Python, Grafana et Reconnaissance Faciale / Biométrie.

---

## 🏗️ Architecture des Services Docker Compose

Le fichier [**docker-compose.yml**](file:///c:/Users/axelm/Downloads/workshop/docker-compose.yml) orchestre 5 services reliés par le réseau `sentinel_net` :

1. **`mqtt`** (`sentinel_mqtt`) :
   - Image : `eclipse-mosquitto:latest`
   - Ports : `1883` (interne / debug) & `8883` (MQTTS TLS pour ESP8266)
   - Configuration : `./mosquitto/config/mosquitto.conf`
   - Certificats TLS : `./mosquitto/config/certs/`
   - Utilisateurs : `./mosquitto/config/passwd` (`esp_client` / `SuperSecret123`)

2. **`timescaledb`** (`sentinel_db`) :
   - Image : `timescale/timescaledb:latest-pg15`
   - Port : `5432`
   - Volume persistant : `db_data`
   - Initialisation auto : `./timescaledb/init.sql` (crée `sensor_data`, `mesures` et `alertes`)

3. **`api_ingest`** (`sentinel_api_ingest`) :
   - Image construite via : `./app/Dockerfile`
   - Écoute les topics MQTT `sentinel/#` et persiste en base de données.

4. **`grafana`** (`sentinel_grafana`) :
   - Image : `grafana/grafana:latest`
   - Port : `3000` (`admin` / `sentinel_admin`)
   - Source de données TimescaleDB auto-provisionnée via `./grafana/provisioning/`

5. **`biometric-vision`** (`sentinel_biometric_vision`) :
   - Image construite via : `./Dockerfile`
   - Port : `5000` (Interface Web de reconnaissance faciale)
   - Volume persistant : `./faces_db` (visages enregistrés)
   - Transmission automatique d'alertes intrusion vers le broker MQTT

---

## 🚀 Commandes de Déploiement

### Démarrage :
```bash
# Lancement de toute la stack en arrière-plan
docker compose up -d --build
```

### Vérification de l'état :
```bash
docker compose ps
```

### Lecture des logs :
```bash
# Tous les services
docker compose logs -f

# Un service spécifique
docker compose logs -f biometric-vision
docker compose logs -f api_ingest
docker compose logs -f mqtt
```

### Arrêt :
```bash
docker compose down
```

---

## 📷 Gestion de la Caméra pour le Conteneur Vision

- **Sur Linux natif** : Décommentez `devices: - /dev/video0:/dev/video0` dans `docker-compose.yml`.
- **Sur Windows / WSL2** :
  - **Option 1 (Le plus simple)** : Utilisez un flux RTSP ou Caméra IP de smartphone (ex: IP Webcam) et configurez `WEBCAM_SOURCE=http://192.168.1.XX:8080/video` dans `.env`.
  - **Option 2 (USB physique)** : Attachez la webcam à WSL2 avec `usbipd wsl attach --busid <BUSID>`.
  - **Option 3 (Exécution native)** : Vous pouvez lancer la vision directement sur votre machine hôte avec `python detection_webcam.py` pendant que les autres conteneurs (MQTT, TimescaleDB, Grafana) tournent sous Docker !
