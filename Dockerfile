# Image de base Python 3.11 légère
FROM python:3.11-slim

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000 \
    WEBCAM_SOURCE=0

# Installation centralisée de TOUTES les dépendances système (OpenCV + MediaPipe)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxrender-dev \
    v4l-utils \
    libegl1 \
    libgles2 \
    && rm -rf /var/lib/apt/lists/*

# Répertoire de travail
WORKDIR /app

# Installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie des modèles IA
COPY face_detection_yunet_2023mar.onnx .
COPY face_recognition_sface_2021dec.onnx .
COPY face_landmarker.task .

# Copie du code applicatif
COPY face_engine.py .
COPY detection_webcam.py .

# Création du dossier pour la base de visages (monté en volume)
RUN mkdir -p /app/faces_db

# Port exposé
EXPOSE 5000

# Commande de démarrage
CMD ["python", "detection_webcam.py"]
