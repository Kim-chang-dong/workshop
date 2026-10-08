import sys
path = "detection_webcam.py"
src = open(path, encoding="utf-8").read()
start_marker = "    def _capture_loop(self):"
end_marker = "    def get_latest_frame_with_id(self):"
if src.count(start_marker) != 1 or src.count(end_marker) != 1:
    sys.exit("ERREUR : marqueurs introuvables ou en double, aucune modification faite.")
start = src.index(start_marker)
end = src.index(end_marker)
if start >= end:
    sys.exit("ERREUR : ordre des méthodes inattendu, aucune modification faite.")

new_method = r'''    def _capture_loop(self):
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

'''

new_src = src[:start] + new_method + src[end:]
try:
    compile(new_src, path, "exec")
except SyntaxError as e:
    sys.exit(f"ERREUR : le résultat ne serait pas du Python valide ({e}), aucune modification faite.")

open(path, "w", encoding="utf-8").write(new_src)
print("OK : _capture_loop remplacée.")
