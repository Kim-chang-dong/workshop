# 🐳 Déploiement Docker - Reconnaissance Faciale & Biométrie

Ce guide explique comment construire et exécuter l'application sous **Docker** et **Docker Compose**.

---

## 📁 Fichiers inclus pour Docker

- [**Dockerfile**](file:///c:/Users/axelm/Downloads/workshop/Dockerfile) : Image optimisée basée sur `python:3.11-slim` avec toutes les bibliothèques système OpenCV (`libgl1`, `v4l-utils`), MediaPipe et les modèles IA pré-intégrés.
- [**docker-compose.yml**](file:///c:/Users/axelm/Downloads/workshop/docker-compose.yml) : Orchestration du conteneur avec persistance du dossier `faces_db` et configuration des ports et flux.
- [**.dockerignore**](file:///c:/Users/axelm/Downloads/workshop/.dockerignore) : Exclusion des fichiers de cache et fichiers temporaires.
- [**requirements.txt**](file:///c:/Users/axelm/Downloads/workshop/requirements.txt) : Dépendances Python nécessaires.

---

## 🚀 1. Lancement rapide avec Docker Compose

Dans votre terminal :

```bash
# Construire et démarrer le conteneur
docker compose up --build

# Ou en arrière-plan (mode détaché)
docker compose up -d --build
```

L'application sera accessible sur votre navigateur à l'adresse :  
👉 **http://localhost:5000**

Pour arrêter le conteneur :
```bash
docker compose down
```

---

## 📷 2. Gestion de la Caméra dans Docker

### A. Sur Linux (ou Raspberry Pi / Serveur)
Sous Linux, Docker accède directement à la webcam via le périphérique `/dev/video0`.
Dans [docker-compose.yml](file:///c:/Users/axelm/Downloads/workshop/docker-compose.yml), décommentez simplement :
```yaml
devices:
  - /dev/video0:/dev/video0
```
Et définissez :
```yaml
environment:
  - WEBCAM_SOURCE=/dev/video0
```

### B. Sur Windows avec Docker Desktop
Sur Windows, Docker fonctionne dans une machine virtuelle WSL2. Par défaut, Windows n'expose pas directement les webcams USB à l'intérieur des conteneurs Linux WSL2.

Vous avez 3 options simples :
1. **Flux Caméra IP / Téléphone (Le plus simple en conteneur)** :
   Installez une application gratuite comme *IP Webcam* ou *DroidCam* sur votre smartphone, ou utilisez une caméra réseau RTSP.
   Passez simplement l'URL dans `docker-compose.yml` :
   ```yaml
   environment:
     - WEBCAM_SOURCE=http://192.168.1.50:8080/video
   ```
2. **Passer la webcam USB à WSL2 via `usbipd-win`** :
   ```powershell
   winget install usbipd
   usbipd wsl list
   usbipd wsl attach --busid <BUSID>
   ```
3. **Exécution native sur Windows** :
   Si vous souhaitez utiliser directement votre webcam USB physique sans passer par un pont USB dans WSL2, vous pouvez continuer à lancer l'application directement avec :
   ```powershell
   python detection_webcam.py
   ```

---

## 💾 3. Persistance des visages enregistrés

Grâce au volume monté dans [docker-compose.yml](file:///c:/Users/axelm/Downloads/workshop/docker-compose.yml) :
```yaml
volumes:
  - ./faces_db:/app/faces_db
```
Tous les visages que vous enregistrez via le bouton *« Enregistrer mon visage en direct »* sont automatiquement conservés sur votre machine hôte dans votre dossier `faces_db/`. Même si vous détruisez ou reconstruisez le conteneur, **vos visages ne sont jamais perdus**.
