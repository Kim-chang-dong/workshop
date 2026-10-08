"""
Sentinel-X - Maintenance Prédictive & Détection d'anomalies (Isolation Forest)
Groupe 16 - Workshop M1
"""
import os
import time
import requests
import psycopg2
import numpy as np
from sklearn.ensemble import IsolationForest

# Configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "sentinel_db")
DB_USER = os.getenv("DB_USER", "sentinel_user")
DB_PASS = os.getenv("DB_PASSWORD", "sentinel_password")
ALERT_API = os.getenv("ALERT_API", "http://localhost:5000/api/v1/alerts")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))

print(f"[IA PRÉDICTIF] Connexion à TimescaleDB ({DB_HOST}:{DB_NAME})...")
try:
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    print("[IA PRÉDICTIF] Connecté à la base de données !")
except Exception as e:
    print(f"[ERREUR] Impossible de se connecter à la base : {e}")
    exit(1)

cur = conn.cursor()

while True:
    try:
        # 1. Chargement de l'historique pour l'entraînement
        cur.execute("SELECT temp, hum, gaz FROM mesures WHERE temp IS NOT NULL AND hum IS NOT NULL AND gaz IS NOT NULL ORDER BY ts DESC LIMIT 2000")
        rows = cur.fetchall()

        if len(rows) < 15:
            print(f"[IA PRÉDICTIF] Données insuffisantes pour l'apprentissage ({len(rows)}/15 requis). Nouvelle vérification dans {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)
            continue

        X = np.array(rows, dtype=float)

        # 2. Entraînement non supervisé du modèle Isolation Forest
        model = IsolationForest(contamination=0.03, random_state=42)
        model.fit(X)

        # 3. Évaluation du dernier point reçu
        cur.execute("SELECT temp, hum, gaz FROM mesures WHERE temp IS NOT NULL AND hum IS NOT NULL AND gaz IS NOT NULL ORDER BY ts DESC LIMIT 1")
        last = np.array(cur.fetchall(), dtype=float)

        if len(last) > 0:
            prediction = model.predict(last)[0]
            # -1 indique une anomalie statistique
            if prediction == -1:
                print(f"[ANOMALIE DÉTECTÉE] Valeurs atypiques : temp={last[0][0]}, hum={last[0][1]}, gaz={last[0][2]}")
                payload = {
                    "source": "predictif_ia",
                    "type": "anomalie_capteur",
                    "severite": "moyenne",
                    "details": {
                        "temperature": float(last[0][0]),
                        "humidite": float(last[0][1]),
                        "gaz": float(last[0][2]),
                        "score_anomalie": float(model.decision_function(last)[0])
                    }
                }
                try:
                    res = requests.post(ALERT_API, json=payload, timeout=2.0)
                    print(f"[ALERTE ENVOYÉE] Statut HTTP {res.status_code}")
                except Exception as e:
                    print(f"[ERREUR ENVOI ALERTE] {e}")
            else:
                print(f"[IA PRÉDICTIF] Données nominales (temp={last[0][0]}°C, gaz={last[0][2]}).")

    except Exception as e:
        print(f"[ERREUR BOUCLE PRÉDICTIVE] {e}")
        try:
            conn.rollback()
        except Exception:
            pass

    time.sleep(CHECK_INTERVAL)

