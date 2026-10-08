"""
Sentinel-X - Module Vision YOLOv8 (Détection d'intrusion humaine)
Groupe 16 - Workshop M1
"""
import os
import time
import requests
import cv2
from ultralytics import YOLO

# Configuration
MODEL_PATH = os.getenv("YOLO_MODEL", "yolov8n.pt")
WEBCAM_INDEX = int(os.getenv("WEBCAM_SOURCE", "0"))
ALERT_API = os.getenv("ALERT_API", "http://localhost:5000/api/v1/alerts")
ALERT_INTERVAL = float(os.getenv("ALERT_INTERVAL", "3.0"))

print(f"[YOLO] Chargement du modèle {MODEL_PATH}...")
model = YOLO(MODEL_PATH)

cap = cv2.VideoCapture(WEBCAM_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print(f"[ERREUR] Impossible d'ouvrir la caméra {WEBCAM_INDEX}")
    exit(1)

print("[YOLO] Démarrage de la détection en temps réel (Appuyez sur 'q' pour quitter)...")
last_alert_time = 0

while True:
    ret, frame = cap.read()
    if not ret:
        print("[YOLO] Fin de flux ou trame non reçue.")
        break

    # Inférence limitée à la classe 0 (person)
    results = model(frame, classes=[0], verbose=False)
    persons_detected = len(results[0].boxes)

    # Affichage du cadre annoté
    annotated = results[0].plot()
    cv2.putText(
        annotated,
        f"Intrus detectes: {persons_detected}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 0, 255) if persons_detected > 0 else (0, 255, 0),
        2
    )
    cv2.imshow("Sentinel-X - Vision YOLOv8", annotated)

    # Notification d'alerte cadencée
    now = time.time()
    if persons_detected > 0 and (now - last_alert_time > ALERT_INTERVAL):
        last_alert_time = now
        payload = {
            "source": "vision_yolo",
            "type": "intrusion",
            "severite": "haute",
            "details": {
                "personnes": persons_detected,
                "score": float(results[0].boxes.conf[0]) if len(results[0].boxes.conf) > 0 else 1.0
            }
        }
        try:
            res = requests.post(ALERT_API, json=payload, timeout=1.5)
            print(f"[YOLO -> ALERTE] Transmission réussie ({res.status_code}) : {persons_detected} personne(s)")
        except Exception as e:
            print(f"[YOLO -> ALERTE] Erreur d'envoi API : {e}")

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

