// =====================================================================
// Sentinel-X — configuration du dashboard
// À adapter avant déploiement. Aucun mot de passe ici : il est saisi
// dans l'interface au moment de la connexion et n'est jamais stocké.
// =====================================================================
window.SENTINEL_CONFIG = {
  // URL MQTT over WebSockets du broker.
  // ws://  = non chiffré, wss:// = chiffré (TLS). Exemple de forme :
  //   "wss://<IP_DU_SERVEUR>:<PORT>/mqtt"
  // Laissé vide volontairement : à renseigner avec la vraie valeur.
  brokerUrl: "wss://192.168.50.10/mqtt",
  username: "dashboard",

  topics: {
    telemetry: "sentinel/telemetry", // publié par l'ESP8266 (firmware actuel)
    cmd:       "sentinel/cmd",       // publié par le dashboard -> ESP8266
    alerts:    "sentinel/alerts",    // alertes vision / IA / API
    host:      "sentinel/host",      // métriques CPU/RAM du PC Serveur Local (optionnel)
    status:    "sentinel/status"     // Last Will de l'ESP8266 (optionnel)
  },

  // Correspondance clés JSON du firmware -> métriques du dashboard
  telemetryKeys: {
    temperature: "temperature",
    humidity:    "humidity",
    gas:         "gas",
    motion:      "motion",
    alert:       "alert",
    // Champs optionnels : affichés seulement s'ils sont présents dans le payload
    ip:     "ip",
    rssi:   "rssi",
    uptime: "uptime"
  },

  // Boîtier considéré hors ligne si aucune télémétrie depuis N secondes.
  // À caler sur 3 x l'intervalle de publication réel du firmware.
  staleAfterSeconds: 10,

  // Nombre de points conservés sur les courbes (mémoire navigateur uniquement)
  maxPoints: 300,

  // Flux MJPEG de la webcam (script YOLO). Vide = panneau désactivé.
  webcamUrl: "/video_feed"
};
