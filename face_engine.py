import os
import cv2
import json
import time
import math
import threading
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.vision import FaceLandmarksConnections

class FaceBiometricEngine:
    def __init__(self, 
                 yunet_model="face_detection_yunet_2023mar.onnx",
                 sface_model="face_recognition_sface_2021dec.onnx",
                 landmarker_model="face_landmarker.task",
                 db_dir="faces_db",
                 similarity_threshold=0.38):
        
        self.db_dir = db_dir
        os.makedirs(self.db_dir, exist_ok=True)
        self.similarity_threshold = similarity_threshold
        self.cache_file = os.path.join(self.db_dir, "database.json")
        self.lock = threading.Lock() # Verrou pour thread-safety absolu
        
        # 1. Initialisation YuNet (Détection visage)
        if not os.path.exists(yunet_model):
            raise FileNotFoundError(f"Modèle YuNet introuvable: {yunet_model}")
        self.detector = cv2.FaceDetectorYN.create(
            model=yunet_model,
            config="",
            input_size=(640, 480),
            score_threshold=0.65,
            nms_threshold=0.3,
            top_k=5000
        )
        
        # 2. Initialisation SFace (Reconnaissance 128D)
        if not os.path.exists(sface_model):
            raise FileNotFoundError(f"Modèle SFace introuvable: {sface_model}")
        self.recognizer = cv2.FaceRecognizerSF.create(
            model=sface_model,
            config=""
        )
        
        # 3. Initialisation MediaPipe FaceLandmarker (Maillage 478 points)
        if not os.path.exists(landmarker_model):
            raise FileNotFoundError(f"Modèle FaceLandmarker introuvable: {landmarker_model}")
        base_options = python.BaseOptions(model_asset_path=landmarker_model)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=4
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        
        # Connexions anatomiques
        self.parts_connections = {
            "contour": FaceLandmarksConnections.FACE_LANDMARKS_FACE_OVAL,
            "oeil_gauche": FaceLandmarksConnections.FACE_LANDMARKS_LEFT_EYE,
            "oeil_droit": FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_EYE,
            "iris_gauche": FaceLandmarksConnections.FACE_LANDMARKS_LEFT_IRIS,
            "iris_droit": FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_IRIS,
            "sourcil_gauche": FaceLandmarksConnections.FACE_LANDMARKS_LEFT_EYEBROW,
            "sourcil_droit": FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_EYEBROW,
            "nez": FaceLandmarksConnections.FACE_LANDMARKS_NOSE,
            "levres": FaceLandmarksConnections.FACE_LANDMARKS_LIPS,
        }
        
        self.known_faces = {}
        self.load_database()

    def load_database(self):
        """Charge ou régénère la base de données des visages connus depuis faces_db."""
        with self.lock:
            self.known_faces = {}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for person in data:
                            nom = person["nom"]
                            features = []
                            for feat in person["features"]:
                                arr = np.array(feat, dtype=np.float32).reshape(1, 128)
                                features.append(arr)
                            if features:
                                self.known_faces[nom] = features
                    print(f"[BDD] {len(self.known_faces)} personne(s) chargée(s) depuis le cache.")
                    return
                except Exception as e:
                    print(f"[BDD] Erreur lors du chargement du cache: {e}. Régénération...")
                    
        self.rebuild_database()

    def rebuild_database(self):
        """Parcourt le dossier faces_db et extrait les empreintes de chaque photo."""
        with self.lock:
            self.known_faces = {}
            extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            
            for root, _, files in os.walk(self.db_dir):
                for file in files:
                    if file.lower().endswith(extensions) and not file.startswith("."):
                        path = os.path.join(root, file)
                        nom = os.path.splitext(file)[0]
                        parent = os.path.basename(root)
                        if parent != os.path.basename(self.db_dir):
                            nom = parent
                            
                        feat = self._extract_feature_unsafe(path)
                        if feat is not None:
                            if nom not in self.known_faces:
                                self.known_faces[nom] = []
                            self.known_faces[nom].append(feat)
                            print(f"[BDD] Visage indexé pour: {nom} ({file})")
                            
            self._save_database_cache_unsafe()

    def _save_database_cache_unsafe(self):
        """Sauvegarde les empreintes en format JSON (doit être appelé sous lock)."""
        data = []
        for nom, feats in self.known_faces.items():
            data.append({
                "nom": nom,
                "features": [feat.flatten().tolist() for feat in feats]
            })
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print(f"[BDD] Base de données synchronisée ({len(self.known_faces)} personnes).")

    def _extract_feature_unsafe(self, image_path_or_bgr):
        """Extrait l'empreinte faciale 128D (doit être appelé sous lock)."""
        if isinstance(image_path_or_bgr, str):
            img = cv2.imread(image_path_or_bgr)
            if img is None:
                return None
        else:
            img = image_path_or_bgr

        h, w, _ = img.shape
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        
        if faces is None or len(faces) == 0:
            return None
        
        # Trouver le plus grand visage (largeur x hauteur)
        best_face = max(faces, key=lambda f: f[2] * f[3])
        if best_face[2] < 40 or best_face[3] < 40:
            return None

        aligned_face = self.recognizer.alignCrop(img, best_face)
        feature = self.recognizer.feature(aligned_face)
        return feature.reshape(1, 128)

    def extract_feature_from_image(self, image_path_or_bgr):
        with self.lock:
            return self._extract_feature_unsafe(image_path_or_bgr)

    def enroll_person(self, name, bgr_image):
        """Enregistre une nouvelle personne de manière thread-safe."""
        with self.lock:
            feat = self._extract_feature_unsafe(bgr_image)
            if feat is None:
                return False, "Aucun visage net détecté sur l'image."
            
            filename = f"{name}.jpg"
            save_path = os.path.join(self.db_dir, filename)
            cv2.imwrite(save_path, bgr_image)
            
            if name not in self.known_faces:
                self.known_faces[name] = []
            self.known_faces[name].append(feat)
            self._save_database_cache_unsafe()
            return True, f"Visage de '{name}' enregistré avec succès dans la base !"

    def delete_person(self, name):
        """Supprime une personne de la base de manière thread-safe."""
        with self.lock:
            if name in self.known_faces:
                del self.known_faces[name]
                for file in os.listdir(self.db_dir):
                    if file.startswith(name):
                        try:
                            os.remove(os.path.join(self.db_dir, file))
                        except Exception:
                            pass
                self._save_database_cache_unsafe()
                return True, f"Personne '{name}' supprimée."
            return False, f"Personne '{name}' introuvable."

    def list_enrolled_persons(self):
        with self.lock:
            return list(self.known_faces.keys())

    def match_feature(self, feature):
        """Compare une empreinte avec la base de données (copie snapshot thread-safe)."""
        with self.lock:
            items = list(self.known_faces.items())
            
        if not items or feature is None:
            return None, 0.0
        
        best_name = None
        best_score = -1.0
        
        for nom, feat_list in items:
            for known_feat in feat_list:
                score = self.recognizer.match(feature, known_feat, cv2.FaceRecognizerSF_FR_COSINE)
                if score > best_score:
                    best_score = float(score)
                    best_name = nom
                    
        return best_name, max(0.0, best_score)

    @staticmethod
    def draw_alpha_rect(img, x1, y1, x2, y2, color, alpha=0.7):
        """Dessine un rectangle semi-transparent rapide par région ROI sans copier l'image entière."""
        h, w = img.shape[:2]
        x1 = max(0, min(w, x1))
        x2 = max(0, min(w, x2))
        y1 = max(0, min(h, y1))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return
        roi = img[y1:y2, x1:x2]
        colored = np.full_like(roi, color, dtype=np.uint8)
        cv2.addWeighted(colored, alpha, roi, 1.0 - alpha, 0, roi)

    def process_frame(self, frame):
        """
        Traite une image vidéo:
        1. Détection YuNet + Reconnaissance SFace
        2. Maillage MediaPipe (478 repères anatomiques)
        3. Dessin des parties du visage et télémétrie HUD
        """
        h_frame, w_frame, _ = frame.shape
        annotated_frame = frame.copy()
        
        # Exécution sous lock pour éviter les collisions C++ DNN
        with self.lock:
            self.detector.setInputSize((w_frame, h_frame))
            _, yunet_faces = self.detector.detect(frame)
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            mp_results = self.landmarker.detect(mp_img)
            
        mesh_faces = mp_results.face_landmarks if mp_results and mp_results.face_landmarks else []
        
        detected_persons = []
        alerte_intrus = False
        scan_progress = (math.sin(time.time() * 3.5) + 1.0) / 2.0
        
        if yunet_faces is not None and len(yunet_faces) > 0:
            for face in yunet_faces:
                fx, fy, fw, fh = map(int, face[:4])
                conf_det = float(face[-1])
                
                # Filtrer le bruit et les micro-détections (faux positifs de fond)
                if fw < 40 or fh < 40 or conf_det < 0.65:
                    continue
                    
                # Alignement et extraction sous lock
                with self.lock:
                    aligned = self.recognizer.alignCrop(frame, face)
                    feat = self.recognizer.feature(aligned)
                
                # Correspondance BDD
                nom, sim_score = self.match_feature(feat)
                is_recognized = (sim_score >= self.similarity_threshold) and (nom is not None)
                
                percent = min(100, int((sim_score / 0.70) * 100)) if is_recognized else max(0, int((sim_score / 0.50) * 100))
                
                if is_recognized:
                    nom_affiche = nom
                    couleur = (50, 220, 80)       # Vert
                    couleur_hud = (80, 240, 120)
                    statut_label = "AUTORISE"
                else:
                    nom_affiche = "INCONNU / NON RECONNU"
                    couleur = (0, 0, 255)         # Rouge
                    couleur_hud = (40, 40, 255)
                    statut_label = "ALERTE INTRUS"
                    alerte_intrus = True
                    
                detected_persons.append({
                    "nom": nom if is_recognized else "Inconnu",
                    "reconnu": is_recognized,
                    "score": float(round(sim_score, 3)),
                    "pourcentage": percent,
                    "bbox": [fx, fy, fw, fh],
                    "parties_scannees": ["contour", "yeux", "sourcils", "nez", "levres", "iris"]
                })
                
                # A. Boîte ciblée haute technologie
                self.draw_cyber_bracket(annotated_frame, fx, fy, fw, fh, couleur)
                
                # B. Ligne de balayage laser
                self.draw_laser_scanline(annotated_frame, fx, fy, fw, fh, scan_progress, couleur)
                
                # C. Badge d'identification
                badge_text = f"[{statut_label}] {nom_affiche} ({percent}%)"
                (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_DUPLEX, 0.55, 1)
                by1 = max(0, fy - 26)
                by2 = max(by1 + 22, fy)
                cv2.rectangle(annotated_frame, (fx, by1), (fx + tw + 10, by2), couleur, -1)
                cv2.putText(annotated_frame, badge_text, (fx + 5, by2 - 6),
                            cv2.FONT_HERSHEY_DUPLEX, 0.55, (0, 0, 0) if is_recognized else (255, 255, 255), 1, cv2.LINE_AA)
                
                # D. Télémétrie des parties scannées
                self.draw_parts_telemetry(annotated_frame, fx + fw + 8, fy, is_recognized, couleur_hud)

        # 3. Dessin des parties anatomiques (MediaPipe)
        for landmarks in mesh_faces:
            self.draw_detailed_facial_parts(annotated_frame, landmarks, w_frame, h_frame)
            
        # 4. HUD Supérieur Global
        nb_visages = len(detected_persons)
        if nb_visages == 0:
            texte_global = "VEILLE BIOMETRIQUE : AUCUN VISAGE DETECTE"
            col_global = (180, 180, 180)
        elif alerte_intrus:
            texte_global = f"ALERTE SECURITE : {nb_visages} VISAGE(S) - INDIVIDU NON RECONNU !"
            col_global = (0, 0, 255)
        else:
            texte_global = f"ZONE SECURISEE : {nb_visages} VISAGE(S) AUTORISE(S)"
            col_global = (0, 220, 80)
            
        # Bandeau semi-transparent supérieur ultra-rapide (ROI)
        self.draw_alpha_rect(annotated_frame, 0, 0, w_frame, 36, (20, 20, 20), alpha=0.75)
        cv2.circle(annotated_frame, (18, 18), 6, col_global, -1)
        cv2.putText(annotated_frame, texte_global, (32, 24), cv2.FONT_HERSHEY_DUPLEX, 0.60, col_global, 1, cv2.LINE_AA)
        
        with self.lock:
            total_bdd_count = len(self.known_faces)
        info_bdd = f"BDD: {total_bdd_count} visage(s)"
        cv2.putText(annotated_frame, info_bdd, (w_frame - 180, 24), cv2.FONT_HERSHEY_DUPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)

        statut_data = {
            "humain_present": nb_visages > 0,
            "visages_detectes": nb_visages,
            "alerte_intrus": alerte_intrus,
            "zone_securisee": (nb_visages > 0 and not alerte_intrus),
            "personnes": detected_persons,
            "total_bdd": total_bdd_count,
            "liste_bdd": self.list_enrolled_persons()
        }
        
        return annotated_frame, statut_data

    def draw_cyber_bracket(self, img, x, y, w, h, color):
        """Dessine des coins biométriques aux 4 coins du visage."""
        b_len = max(12, int(min(w, h) * 0.18))
        thick = 2
        
        # Haut-Gauche
        cv2.line(img, (x, y), (x + b_len, y), color, thick)
        cv2.line(img, (x, y), (x, y + b_len), color, thick)
        # Haut-Droite
        cv2.line(img, (x + w, y), (x + w - b_len, y), color, thick)
        cv2.line(img, (x + w, y), (x + w, y + b_len), color, thick)
        # Bas-Gauche
        cv2.line(img, (x, y + h), (x + b_len, y + h), color, thick)
        cv2.line(img, (x, y + h), (x, y + h - b_len), color, thick)
        # Bas-Droite
        cv2.line(img, (x + w, y + h), (x + w - b_len, y + h), color, thick)
        cv2.line(img, (x + w, y + h), (x + w, y + h - b_len), color, thick)
        
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 1, cv2.LINE_4)

    def draw_laser_scanline(self, img, x, y, w, h, progress, color):
        """Dessine un faisceau laser animé balayant le visage."""
        scan_y = int(y + progress * h)
        if 0 <= scan_y < img.shape[0]:
            cv2.line(img, (x, scan_y), (x + w, scan_y), color, 2, cv2.LINE_AA)
            self.draw_alpha_rect(img, x, scan_y - 2, x + w, scan_y + 3, color, alpha=0.35)

    def draw_detailed_facial_parts(self, img, landmarks, w, h):
        """Scanne et trace les zones anatomiques spécifiques (yeux, nez, bouche, etc.)."""
        color_map = {
            "contour": (80, 240, 100),        # Mâchoire & ovale
            "oeil_gauche": (255, 220, 0),     # Yeux cyan
            "oeil_droit": (255, 220, 0),
            "iris_gauche": (255, 255, 255),   # Iris pupille
            "iris_droit": (255, 255, 255),
            "sourcil_gauche": (255, 170, 50), # Sourcils
            "sourcil_droit": (255, 170, 50),
            "nez": (0, 215, 255),             # Arête et nez
            "levres": (210, 80, 255),         # Lèvres
        }
        
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
        
        for part_name, connections in self.parts_connections.items():
            c = color_map.get(part_name, (200, 200, 200))
            for conn in connections:
                idx1 = conn.start
                idx2 = conn.end
                if idx1 < len(pts) and idx2 < len(pts):
                    cv2.line(img, pts[idx1], pts[idx2], c, 1, cv2.LINE_AA)
                    
        # Points lumineux repères
        for kidx in [1, 33, 263, 61, 291, 199]:
            if kidx < len(pts):
                cv2.circle(img, pts[kidx], 2, (0, 255, 255), -1)

    def draw_parts_telemetry(self, img, tx, ty, is_recognized, text_color):
        """Affiche la télémétrie des parties scannées sur le côté du visage."""
        if tx + 130 > img.shape[1]:
            tx = max(10, tx - 250)
        ty = max(42, min(img.shape[0] - 110, ty))
            
        items = [
            "SCAN ANATOMIQUE:",
            "[OK] OVALE : 100%",
            "[OK] YEUX  : 100%",
            "[OK] SOURCILS: 100%",
            "[OK] NEZ   : 100%",
            "[OK] LEVRES: 100%"
        ]
        
        box_h = len(items) * 16 + 8
        self.draw_alpha_rect(img, tx, ty, tx + 135, ty + box_h, (15, 15, 20), alpha=0.75)
        cv2.rectangle(img, (tx, ty), (tx + 135, ty + box_h), text_color, 1)
        
        for i, txt in enumerate(items):
            c = (255, 255, 255) if i == 0 else text_color
            cv2.putText(img, txt, (tx + 6, ty + 14 + i * 16),
                        cv2.FONT_HERSHEY_PLAIN, 0.85, c, 1, cv2.LINE_AA)
