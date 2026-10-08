import os
import cv2
import time
import threading
import traceback
import numpy as np
from flask import Flask, Response, jsonify, render_template_string, request, send_from_directory
from flask_cors import CORS
from face_engine import FaceBiometricEngine

app = Flask(__name__)
CORS(app)

# Configuration par variables d'environnement (idéal pour Docker)
webcam_source_env = os.getenv("WEBCAM_SOURCE", os.getenv("WEBCAM_INDEX", "0"))
if webcam_source_env.isdigit():
    WEBCAM_INDEX = int(webcam_source_env)
else:
    WEBCAM_INDEX = webcam_source_env  # Supporte '/dev/video0', un flux RTSP, ou un fichier vidéo

FACES_DB_DIR = os.getenv("FACES_DB_DIR", "faces_db")
os.makedirs(FACES_DB_DIR, exist_ok=True)

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.38"))

print("[INITIALISATION] Chargement du moteur biométrique et des modèles IA...")
engine = FaceBiometricEngine(
    yunet_model="face_detection_yunet_2023mar.onnx",
    sface_model="face_recognition_sface_2021dec.onnx",
    landmarker_model="face_landmarker.task",
    db_dir=FACES_DB_DIR,
    similarity_threshold=SIMILARITY_THRESHOLD
)
print(f"[INITIALISATION] Prêt ! {len(engine.known_faces)} personne(s) dans la base de données.")

class SentinelMQTTNotifier:
    """Gestionnaire de notifications MQTT pour la stack Sentinel-X (non-bloquant)."""
    def __init__(self, broker, port=1883, user="esp_client", password="SuperSecret123"):
        self.broker = broker
        self.port = port
        self.user = user
        self.password = password
        self.client = None
        self.connected = False
        self.last_alert_time = 0
        self.lock = threading.Lock()
        if self.broker:
            threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        try:
            import paho.mqtt.client as mqtt
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            except AttributeError:
                self.client = mqtt.Client()
            if self.user and self.password:
                self.client.username_pw_set(self.user, self.password)
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            self.connected = True
            print(f"[MQTT SENTINEL] Connecté avec succès au broker {self.broker}:{self.port}")
        except Exception as e:
            print(f"[MQTT SENTINEL] Broker non joignable ({e}) - Alertes MQTT désactivées.")
            self.connected = False

    def publish_event(self, source="vision", alert_type="intrusion", severite="haute", details=None, min_interval=3.0):
        if not self.connected or not self.client:
            return
        now = time.time()
        with self.lock:
            if now - self.last_alert_time < min_interval:
                return
            self.last_alert_time = now

        payload = {
            "source": source,
            "type": alert_type,
            "severite": severite,
            "device_id": "sentinel_vision",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "details": details or {}
        }
        try:
            msg = json.dumps(payload)
            self.client.publish("sentinel/alerts", msg)
            self.client.publish("sentinel/g16/alerts", msg)
            # Télémétrie alerte pour TimescaleDB et Grafana
            telemetry_payload = json.dumps({
                "device_id": "sentinel_vision",
                "alert": 1.0,
                "motion": 1.0
            })
            self.client.publish("sentinel/telemetry", telemetry_payload)
            print(f"[MQTT SENTINEL] Alerte transmise : {alert_type} ({severite})")
        except Exception as e:
            print(f"[MQTT SENTINEL] Erreur publication MQTT : {e}")

    def stop(self):
        if self.client and self.connected:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass

MQTT_BROKER = os.getenv("MQTT_BROKER", "")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "esp_client")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "SuperSecret123")
mqtt_notifier = SentinelMQTTNotifier(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASSWORD) if MQTT_BROKER else None

class CameraWorker:
    """Gestionnaire de flux vidéo multithread ultra-robuste et résistant aux pannes."""
    def __init__(self, camera_index=WEBCAM_INDEX):
        self.camera_index = camera_index
        self.cap = None
        self._init_camera()
        
        self.raw_frame = None
        self.annotated_frame = None
        self.frame_id = 0
        self.lock = threading.Lock()
        
        self.statut = {
            "humain_present": False,
            "score": 0.0,
            "visages_detectes": 0,
            "alerte_intrus": False,
            "zone_securisee": False,
            "personnes": [],
            "total_bdd": len(engine.known_faces),
            "liste_bdd": engine.list_enrolled_persons(),
            "fps": 0.0
        }
        
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _init_camera(self):
        """Ouvre la webcam avec gestion des backends sous Windows."""
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
                
        # Sous Windows avec index numérique, tester CAP_DSHOW puis backend par défaut
        if isinstance(self.camera_index, int) and os.name == 'nt':
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(self.camera_index)
        else:
            self.cap = cv2.VideoCapture(self.camera_index)
            
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Réduit le buffer pour 0 latence
            print(f"[CAMERA] Source vidéo '{self.camera_index}' ouverte avec succès.")
        else:
            print(f"[ATTENTION] Impossible d'ouvrir la source vidéo '{self.camera_index}'.")

    def _capture_loop(self):
        import threading
        import numpy as np

        fps_counter = 0
        last_fps_time = time.time()
        current_fps = 0.0
        consecutive_read_failures = 0

        # --- Découplage capture / IA ---
        ia_event = threading.Event()
        ia_busy = threading.Event()
        ia_input = {"frame": None}
        overlay_lock = threading.Lock()
        overlay = {"mask": None, "drawn": None}

        def ia_worker():
            while self.running:
                if not ia_event.wait(timeout=0.5):
                    continue
                ia_event.clear()
                frame_ia = ia_input["frame"]
                try:
                    annotated, status_info = engine.process_frame(frame_ia.copy())

                    if annotated is not None and annotated.shape == frame_ia.shape:
                        mask = np.any(annotated != frame_ia, axis=2)
                    else:
                        mask = None
                    with overlay_lock:
                        overlay["mask"] = mask
                        overlay["drawn"] = annotated

                    score_max = 0.0
                    if status_info["personnes"]:
                        score_max = max(p["score"] for p in status_info["personnes"])

                    with self.lock:
                        self.statut = {
                            "humain_present": status_info["humain_present"],
                            "score": float(round(score_max, 3)),
                            "visages_detectes": status_info["visages_detectes"],
                            "alerte_intrus": status_info["alerte_intrus"],
                            "zone_securisee": status_info["zone_securisee"],
                            "personnes": status_info["personnes"],
                            "total_bdd": status_info["total_bdd"],
                            "liste_bdd": status_info["liste_bdd"],
                            "fps": self.statut.get("fps", 0.0)
                        }

                    if mqtt_notifier and status_info.get("alerte_intrus"):
                        mqtt_notifier.publish_event(
                            source="vision",
                            alert_type="intrusion",
                            severite="haute",
                            details={
                                "visages_detectes": status_info["visages_detectes"],
                                "score": float(round(score_max, 3)),
                                "message": "Intrus détecté (visage non reconnu)"
                            }
                        )
                except Exception as e:
                    print(f"[ERREUR IA] {e}")
                    traceback.print_exc()
                finally:
                    ia_busy.clear()

        threading.Thread(target=ia_worker, daemon=True).start()

        while self.running:
            try:
                if self.cap is None or not self.cap.isOpened():
                    time.sleep(0.5)
                    self._init_camera()
                    continue

                ok, frame = self.cap.read()
                if not ok or frame is None:
                    consecutive_read_failures += 1
                    if consecutive_read_failures > 30:
                        print("[CAMERA] Perte du signal vidéo, réinitialisation de la webcam...")
                        self._init_camera()
                        consecutive_read_failures = 0
                    time.sleep(0.02)
                    continue

                consecutive_read_failures = 0
                fps_counter += 1
                now = time.time()
                if now - last_fps_time >= 1.0:
                    current_fps = fps_counter / (now - last_fps_time)
                    fps_counter = 0
                    last_fps_time = now

                if not ia_busy.is_set():
                    ia_input["frame"] = frame.copy()
                    ia_busy.set()
                    ia_event.set()

                live = frame.copy()
                with overlay_lock:
                    mask = overlay["mask"]
                    drawn = overlay["drawn"]
                if mask is not None and mask.shape == live.shape[:2]:
                    live[mask] = drawn[mask]

                with self.lock:
                    self.raw_frame = frame.copy()
                    self.annotated_frame = live
                    self.frame_id += 1
                    self.statut["fps"] = round(current_fps, 1)

            except Exception as e:
                print(f"[ERREUR CAPTURE] {e}")
                traceback.print_exc()
                time.sleep(0.05)

    def get_latest_frame_with_id(self):
        with self.lock:
            if self.annotated_frame is not None:
                return self.annotated_frame, self.frame_id
            return None, 0

    def get_raw_frame(self):
        with self.lock:
            if self.raw_frame is not None:
                return self.raw_frame.copy()
            return None

    def get_statut(self):
        with self.lock:
            return dict(self.statut)

    def release(self):
        self.running = False
        if self.cap and self.cap.isOpened():
            self.cap.release()

camera = CameraWorker(WEBCAM_INDEX)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Biométrie & Reconnaissance Faciale</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0a0e17;
            --card-bg: rgba(16, 23, 38, 0.85);
            --border-color: #1e293b;
            --accent-cyan: #00f2fe;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --text-main: #f8fafc;
            --text-dim: #94a3b8;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.05) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(16, 185, 129, 0.05) 0%, transparent 40%);
            padding: 24px;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
        }

        .title-group h1 {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .title-group p {
            color: var(--text-dim);
            font-size: 0.88rem;
            margin-top: 4px;
        }

        .badge-live {
            background: rgba(16, 185, 129, 0.15);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 6px 14px;
            border-radius: 20px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .pulse-dot {
            width: 8px;
            height: 8px;
            background: var(--accent-green);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .grid-container {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 24px;
        }

        @media (max-width: 1024px) {
            .grid-container { grid-template-columns: 1fr; }
        }

        .video-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            display: flex;
            flex-direction: column;
        }

        .video-header {
            padding: 14px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(15, 23, 42, 0.6);
            border-bottom: 1px solid var(--border-color);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
        }

        .stream-wrapper {
            position: relative;
            width: 100%;
            background: #000;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        .stream-wrapper img {
            width: 100%;
            height: auto;
            max-height: 600px;
            object-fit: contain;
            display: block;
        }

        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 24px;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 20px;
            backdrop-filter: blur(8px);
        }

        .card h2 {
            font-size: 1.1rem;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: 600;
        }

        .status-box {
            padding: 16px;
            border-radius: 12px;
            font-weight: 600;
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            transition: all 0.3s;
        }

        .status-safe {
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #34d399;
        }

        .status-alert {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #f87171;
            animation: alertBlink 1s infinite alternate;
        }

        .status-standby {
            background: rgba(148, 163, 184, 0.1);
            border: 1px solid rgba(148, 163, 184, 0.2);
            color: #cbd5e1;
        }

        @keyframes alertBlink {
            from { box-shadow: 0 0 10px rgba(239, 68, 68, 0.2); }
            to { box-shadow: 0 0 20px rgba(239, 68, 68, 0.5); }
        }

        .parts-list {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-bottom: 16px;
        }

        .part-badge {
            background: rgba(30, 41, 59, 0.6);
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 0.8rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border: 1px solid rgba(255, 255, 255, 0.05);
            font-family: 'JetBrains Mono', monospace;
        }

        .part-badge.active {
            border-color: rgba(0, 242, 254, 0.4);
            color: var(--accent-cyan);
        }

        .input-group {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        input[type="text"] {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            border-radius: 8px;
            color: #fff;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }

        input[type="text"]:focus {
            border-color: var(--accent-cyan);
        }

        button {
            cursor: pointer;
            border: none;
            padding: 12px 18px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s;
        }

        .btn-primary {
            background: linear-gradient(135deg, #00f2fe 0%, #4facfe 100%);
            color: #0b0f19;
        }

        .btn-primary:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 15px rgba(0, 242, 254, 0.3);
        }

        .btn-danger {
            background: rgba(239, 68, 68, 0.15);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.3);
            padding: 6px 10px;
            font-size: 0.8rem;
        }

        .btn-danger:hover {
            background: rgba(239, 68, 68, 0.25);
        }

        .faces-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-height: 240px;
            overflow-y: auto;
            margin-top: 10px;
        }

        .face-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            background: rgba(30, 41, 59, 0.5);
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }

        .face-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .face-avatar {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid var(--accent-cyan);
        }

        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            padding: 14px 20px;
            border-radius: 10px;
            font-size: 0.9rem;
            display: none;
            z-index: 100;
            box-shadow: 0 10px 20px rgba(0,0,0,0.5);
        }
    </style>
</head>
<body>

    <header>
        <div class="title-group">
            <h1>🔬 Reconnaissance Faciale & Maillage Biométrique</h1>
            <p>Scan anatomique des parties du visage (Yeux, Nez, Lèvres, Contour) + Matching BDD (SFace)</p>
        </div>
        <div class="badge-live">
            <div class="pulse-dot"></div>
            <span id="fps-counter">FLUX ACTIF - -- FPS</span>
        </div>
    </header>

    <div class="grid-container">
        <!-- Colonne Vidéo -->
        <div class="video-card">
            <div class="video-header">
                <span>CAPTURE WEBCAM // HD STREAM</span>
                <span id="detected-count">0 VISAGE DÉTECTÉ</span>
            </div>
            <div class="stream-wrapper">
                <img src="/video_feed" alt="Flux vidéo de reconnaissance faciale">
            </div>
        </div>

        <!-- Colonne Latérale -->
        <div class="sidebar">
            
            <!-- Carte Statut Sécurité -->
            <div class="card">
                <h2>🛡️ Statut Sécurité</h2>
                <div id="status-card" class="status-box status-standby">
                    <span id="status-text">Veille - Aucun visage</span>
                    <span id="status-score">-- %</span>
                </div>

                <div style="font-size: 0.85rem; color: var(--text-dim); margin-bottom: 8px; font-weight: 600;">
                    PARTIES DU VISAGE SCANNEES :
                </div>
                <div class="parts-list">
                    <div class="part-badge active"><span>👁️ Yeux / Iris</span><span>100%</span></div>
                    <div class="part-badge active"><span>👃 Nez & Arête</span><span>100%</span></div>
                    <div class="part-badge active"><span>👄 Lèvres</span><span>100%</span></div>
                    <div class="part-badge active"><span>🤨 Sourcils</span><span>100%</span></div>
                    <div class="part-badge active" style="grid-column: span 2;"><span>📐 Contour & Mâchoire</span><span>100%</span></div>
                </div>
            </div>

            <!-- Carte Enregistrement -->
            <div class="card">
                <h2>📸 Enrôlement Visage (BDD)</h2>
                <p style="font-size: 0.85rem; color: var(--text-dim); margin-bottom: 14px;">
                    Positionnez-vous face à la caméra et renseignez un nom pour enregistrer votre empreinte faciale.
                </p>
                <div class="input-group">
                    <input type="text" id="person-name" placeholder="Ex: Axel" />
                    <button class="btn-primary" onclick="enrollLive()">
                        <span>📸 Enregistrer mon visage en direct</span>
                    </button>
                </div>
            </div>

            <!-- Carte Base de Données -->
            <div class="card">
                <h2>📁 Personnes en Base (<span id="total-bdd">0</span>)</h2>
                <div id="faces-container" class="faces-list">
                    <!-- Rempli en JS -->
                </div>
            </div>

        </div>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.style.display = 'block';
            toast.style.background = isError ? 'rgba(239, 68, 68, 0.95)' : 'rgba(16, 185, 129, 0.95)';
            toast.style.color = '#fff';
            setTimeout(() => { toast.style.display = 'none'; }, 3500);
        }

        async function updateStatus() {
            try {
                const res = await fetch('/api/statut');
                const data = await res.json();

                document.getElementById('fps-counter').textContent = `FLUX ACTIF - ${data.fps || 0} FPS`;
                document.getElementById('detected-count').textContent = `${data.visages_detectes} VISAGE(S) DÉTECTÉ(S)`;
                document.getElementById('total-bdd').textContent = data.total_bdd;

                const statusCard = document.getElementById('status-card');
                const statusText = document.getElementById('status-text');
                const statusScore = document.getElementById('status-score');

                if (data.visages_detectes === 0) {
                    statusCard.className = 'status-box status-standby';
                    statusText.textContent = 'Veille - Aucun visage';
                    statusScore.textContent = '-- %';
                } else if (data.alerte_intrus) {
                    statusCard.className = 'status-box status-alert';
                    statusText.textContent = 'ALERTE : INTRUS / INCONNU !';
                    statusScore.textContent = `${Math.round(data.score * 100)}%`;
                } else {
                    const nom = (data.personnes && data.personnes[0]) ? data.personnes[0].nom : 'Autorisé';
                    statusCard.className = 'status-box status-safe';
                    statusText.textContent = `AUTORISÉ : ${nom}`;
                    statusScore.textContent = `${Math.round(data.score * 100)}%`;
                }
            } catch (err) {
                console.error(err);
            }
        }

        async function loadFaces() {
            try {
                const res = await fetch('/api/faces');
                const faces = await res.json();
                const container = document.getElementById('faces-container');
                container.innerHTML = '';

                if (faces.length === 0) {
                    container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85rem; padding: 10px 0;">Aucun visage dans la base.</div>';
                    return;
                }

                faces.forEach(name => {
                    const div = document.createElement('div');
                    div.className = 'face-item';
                    div.innerHTML = `
                        <div class="face-info">
                            <img class="face-avatar" src="/faces_db/${name}.jpg" onerror="this.src='https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=00f2fe&color=0b0f19'" />
                            <span style="font-weight: 500;">${name}</span>
                        </div>
                        <button class="btn-danger" onclick="deleteFace('${name}')">Supprimer</button>
                    `;
                    container.appendChild(div);
                });
            } catch (err) {
                console.error(err);
            }
        }

        async function enrollLive() {
            const nameInput = document.getElementById('person-name');
            const name = nameInput.value.trim();
            if (!name) {
                showToast("Veuillez saisir un prénom ou un nom !", true);
                return;
            }

            try {
                const res = await fetch('/api/enroll_live', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ nom: name })
                });
                const data = await res.json();
                if (data.success) {
                    showToast(data.message, false);
                    nameInput.value = '';
                    loadFaces();
                } else {
                    showToast(data.message, true);
                }
            } catch (err) {
                showToast("Erreur lors de l'enregistrement", true);
            }
        }

        async function deleteFace(name) {
            if (!confirm(`Supprimer '${name}' de la base de données ?`)) return;
            try {
                const res = await fetch('/api/delete_face', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ nom: name })
                });
                const data = await res.json();
                showToast(data.message, !data.success);
                loadFaces();
            } catch (err) {
                showToast("Erreur lors de la suppression", true);
            }
        }

        setInterval(updateStatus, 800);
        loadFaces();
    </script>
</body>
</html>
"""

def generate_frames():
    """Générateur HTTP multipart pour le flux vidéo MJPEG synchronisé sans surcharge CPU."""
    last_sent_id = -1
    while True:
        frame, frame_id = camera.get_latest_frame_with_id()
        if frame is None or frame_id == last_sent_id:
            time.sleep(0.015) # Régulation intelligente du framerate
            continue
            
        last_sent_id = frame_id
        ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ret:
            continue
            
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/')
def index():
    """Tableau de bord web de reconnaissance faciale."""
    return render_template_string(HTML_DASHBOARD)

@app.route('/video_feed')
def video_feed():
    """Route pour le flux vidéo webcam annoté."""
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/statut')
def api_statut():
    """Route JSON pour le statut de détection et reconnaissance biométrique."""
    return jsonify(camera.get_statut())

@app.route('/api/faces')
def api_faces():
    """Retourne la liste des personnes enregistrées dans la BDD."""
    return jsonify(engine.list_enrolled_persons())

@app.route('/api/enroll_live', methods=['POST'])
def api_enroll_live():
    """Enregistre le visage actuellement visible sur la webcam dans la base."""
    data = request.get_json(silent=True) or {}
    nom = data.get("nom", "").strip()
    if not nom:
        return jsonify({"success": False, "message": "Nom requis."}), 400
        
    raw_frame = camera.get_raw_frame()
    if raw_frame is None:
        return jsonify({"success": False, "message": "Aucune image webcam disponible."}), 500
        
    success, message = engine.enroll_person(nom, raw_frame)
    return jsonify({"success": success, "message": message, "nom": nom})

@app.route('/api/delete_face', methods=['POST'])
def api_delete_face():
    """Supprime une personne de la base de données."""
    data = request.get_json(silent=True) or {}
    nom = data.get("nom", "").strip()
    if not nom:
        return jsonify({"success": False, "message": "Nom requis."}), 400
        
    success, message = engine.delete_person(nom)
    return jsonify({"success": success, "message": message})

@app.route('/faces_db/<path:filename>')
def serve_face_photo(filename):
    """Sert les photos enregistrées dans la BDD pour le dashboard."""
    return send_from_directory(FACES_DB_DIR, filename)

@app.route('/api/v1/alerts', methods=['POST'])
def api_v1_alerts():
    """Point d'entrée conforme au contrat d'API Sentinel-X pour la réception des alertes."""
    data = request.get_json(silent=True) or {}
    print(f"[API SENTINEL] Alerte reçue : {data}")
    source = data.get("source", "vision")
    alert_type = data.get("type", "intrusion")
    severite = data.get("severite", "haute")
    details = data.get("details", {})
    if mqtt_notifier:
        mqtt_notifier.publish_event(source=source, alert_type=alert_type, severite=severite, details=details, min_interval=0.0)
    return jsonify({"success": True, "message": "Alerte Sentinel-X transmise avec succès", "received": data})

if __name__ == '__main__':
    port = int(os.getenv("PORT", "5000"))
    print("\n=======================================================")
    print("  SERVEUR DE RECONNAISSANCE FACIALE ET SCAN BIOMÉTRIQUE")
    print(f"  Accédez au dashboard sur : http://localhost:{port}")
    print("=======================================================\n")
    try:
        app.run(host='0.0.0.0', port=port, threaded=True)
    finally:
        camera.release()
        if mqtt_notifier:
            mqtt_notifier.stop()
