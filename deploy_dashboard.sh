#!/usr/bin/env bash
# =============================================================================
# Sentinel-X (Groupe 16) — Déploiement sécurisé du dashboard de supervision
#
# OÙ L'EXÉCUTER : dans la VM Ubuntu (dockersrv), depuis ~/workshop :
#     cd ~/workshop && bash deploy_dashboard.sh
#
# PRÉREQUIS (le script vérifie et s'arrête s'il en manque un) :
#   - ~/workshop/sentinel-dashboard-v2.zip         (le front, copié depuis Windows)
#   - mosquitto/config/certs/ca.crt et ca.key      (CA SentinelCA ; ou CA_KEY=/chemin)
#   - Docker Compose >= 2.24.4                     (balises !reset / !override)
#   - /etc/iptables/rules.v4                       (règles persistantes existantes)
#
# CE QUE FAIT LE SCRIPT :
#   1. Sauvegarde tout ce qu'il modifie dans backup-dashboard-<date>/
#   2. Installe le front dans dashboard/site et renseigne config.js
#   3. Émet un certificat web (SAN IP:192.168.50.10) signé par SentinelCA
#   4. Crée l'authentification HTTP (htpasswd) et la conf nginx (HTTPS)
#   5. Ajoute à Mosquitto un listener WebSockets interne + compte dashboard + ACL
#   6. Crée le collecteur de métriques hôte (topic sentinel/host)
#   7. Corrige l'import json manquant du script vision (alertes MQTT)
#   8. Écrit docker-compose.override.yml (nouveaux services + réduction des ports)
#   9. Ouvre 443/8443 à 192.168.50.1 uniquement (DOCKER-USER, runtime + rules.v4)
#  10. Relance la stack
#
# RETOUR ARRIÈRE : bash rollback_dashboard.sh backup-dashboard-<date>
# =============================================================================
set -Eeuo pipefail

# ----------------------------- Paramètres réels ------------------------------
VM_IP="192.168.50.10"          # IP de la VM (eth0, vue avec ip -4 addr show)
CLIENT_IP="192.168.50.1"       # Hôte Windows, seul client autorisé
STREAM_HOST="192.168.50.1"     # Hôte qui sert le flux FFmpeg
STREAM_PORT="8090"
ZIP_FILE="sentinel-dashboard-v2.zip"
CERT_DIR="mosquitto/config/certs"
CA_CRT="${CERT_DIR}/ca.crt"
CA_KEY="${CA_KEY:-${CERT_DIR}/ca.key}"
RULES_V4="/etc/iptables/rules.v4"
BACKUP_DIR="backup-dashboard-$(date +%Y%m%d-%H%M%S)"

ok()   { printf '\e[32m[OK]\e[0m %s\n' "$*"; }
info() { printf '\e[36m[..]\e[0m %s\n' "$*"; }
die()  { printf '\e[31m[ERREUR]\e[0m %s\n' "$*" >&2; exit 1; }
trap 'die "Échec ligne $LINENO. Rien n a été relancé si l erreur précède l étape 10. Sauvegardes : $BACKUP_DIR"' ERR

# =============================================================================
# 0. Vérifications préalables (aucune modification)
# =============================================================================
info "Vérifications préalables"
[[ -f docker-compose.yml && -f detection_webcam.py ]] || die "Lancer le script depuis ~/workshop."
[[ -f "$ZIP_FILE" ]]  || die "$ZIP_FILE absent de ~/workshop (copier le zip depuis Windows avec scp)."
[[ -f "$CA_CRT" ]]    || die "$CA_CRT introuvable."
[[ -f "$CA_KEY" ]]    || die "Clé de la CA introuvable ($CA_KEY). Relancer avec CA_KEY=/chemin/ca.key bash deploy_dashboard.sh"
[[ -f mosquitto/config/mosquitto.conf && -f mosquitto/config/passwd ]] || die "Config Mosquitto introuvable."
[[ ! -e docker-compose.override.yml ]] || die "docker-compose.override.yml existe déjà : script déjà passé ? (voir rollback)."
sudo test -f "$RULES_V4" || die "$RULES_V4 introuvable."
command -v openssl >/dev/null && command -v python3 >/dev/null || die "openssl et python3 requis."

COMPOSE_V="$(docker compose version --short | sed 's/^v//')"
[[ "$(printf '%s\n2.24.4\n' "$COMPOSE_V" | sort -V | head -n1)" == "2.24.4" ]] \
  || die "Docker Compose $COMPOSE_V trop ancien (2.24.4 minimum pour !reset/!override)."
ok "Prérequis présents (Compose $COMPOSE_V)"

# =============================================================================
# 1. Saisie des secrets (jamais écrits en clair sur le disque)
# =============================================================================
ask_pass() {  # $1 = libellé, résultat dans REPLY_PASS
  local p1 p2
  while true; do
    read -rsp "$1 (12 caractères min.) : " p1; echo
    read -rsp "Confirmer : " p2; echo
    [[ "$p1" == "$p2" ]] || { echo "Différents, recommencer."; continue; }
    (( ${#p1} >= 12 )) || { echo "Trop court."; continue; }
    REPLY_PASS="$p1"; return
  done
}
read -rp "Identifiant de la page web (authentification HTTP) [admin-sentinel] : " WEB_USER
WEB_USER="${WEB_USER:-admin-sentinel}"
ask_pass "Mot de passe de la page web"; WEB_PASS="$REPLY_PASS"
read -rp "Identifiant MQTT du dashboard [dashboard] : " DASH_USER
DASH_USER="${DASH_USER:-dashboard}"
[[ "$DASH_USER" =~ ^[A-Za-z0-9_-]+$ ]] || die "Identifiant MQTT invalide."

# =============================================================================
# 2. Sauvegardes de tout ce qui va être modifié
# =============================================================================
info "Sauvegarde dans $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
cp -p mosquitto/config/mosquitto.conf "$BACKUP_DIR/"
sudo cp -p mosquitto/config/passwd "$BACKUP_DIR/passwd"
cp -p detection_webcam.py "$BACKUP_DIR/"
sudo cp -p "$RULES_V4" "$BACKUP_DIR/rules.v4"
ok "Sauvegardes faites"

# =============================================================================
# 3. Front : extraction + config.js (broker WSS via nginx, flux vidéo même origine)
# =============================================================================
info "Installation du front"
mkdir -p dashboard/site dashboard/certs
TMP_EXTRACT="$(mktemp -d)"
python3 -m zipfile -e "$ZIP_FILE" "$TMP_EXTRACT"
SRC_DIR="$(dirname "$(find "$TMP_EXTRACT" -name index.html -print -quit)")"
[[ -f "$SRC_DIR/config.js" ]] || die "config.js introuvable dans le zip."
cp -r "$SRC_DIR"/. dashboard/site/
rm -rf "$TMP_EXTRACT"

python3 - "$VM_IP" "$DASH_USER" <<'PY'
import re, sys
vm_ip, user = sys.argv[1], sys.argv[2]
path = "dashboard/site/config.js"
src = open(path, encoding="utf-8").read()
repl = {
    r'brokerUrl:\s*""': f'brokerUrl: "wss://{vm_ip}/mqtt"',
    r'username:\s*""':  f'username: "{user}"',
    r'webcamUrl:\s*""': 'webcamUrl: "/video_feed"',
}
for pat, new in repl.items():
    src, n = re.subn(pat, new, src)
    if n != 1:
        sys.exit(f"config.js : motif {pat!r} trouvé {n} fois (1 attendu), arrêt.")
open(path, "w", encoding="utf-8").write(src)
PY
ok "Front installé (broker wss://$VM_IP/mqtt, webcam /video_feed)"

# =============================================================================
# 4. Certificat web signé par SentinelCA, SAN = IP de la VM
# =============================================================================
info "Émission du certificat web (la passphrase de la CA peut être demandée)"
EXT_FILE="$(mktemp)"
cat > "$EXT_FILE" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:${VM_IP}
EOF
openssl req -new -newkey rsa:2048 -nodes \
  -keyout dashboard/certs/web.key -out dashboard/certs/web.csr \
  -subj "/O=Sentinel-X G16/CN=${VM_IP}"
openssl x509 -req -in dashboard/certs/web.csr -CA "$CA_CRT" -CAkey "$CA_KEY" \
  -set_serial "0x$(openssl rand -hex 16)" -days 397 -sha256 \
  -extfile "$EXT_FILE" -out dashboard/certs/web.crt
rm -f "$EXT_FILE" dashboard/certs/web.csr
chmod 600 dashboard/certs/web.key
openssl verify -CAfile "$CA_CRT" dashboard/certs/web.crt >/dev/null || die "Certificat web invalide."
ok "Certificat web émis et vérifié contre SentinelCA"

# =============================================================================
# 5. Authentification HTTP (apr1) + configuration nginx
# =============================================================================
info "Création du htpasswd et de nginx.conf"
printf '%s:%s\n' "$WEB_USER" "$(printf '%s' "$WEB_PASS" | openssl passwd -apr1 -stdin)" > dashboard/htpasswd
unset WEB_PASS
# Lisible uniquement par l'utilisateur nginx du conteneur (uid 101 dans l'image officielle)
sudo chown 101:101 dashboard/htpasswd && sudo chmod 400 dashboard/htpasswd

cat > dashboard/nginx.conf <<EOF
# Généré par deploy_dashboard.sh — inclus dans le contexte http de nginx
server_tokens off;
limit_req_zone \$binary_remote_addr zone=sentinel_req:10m rate=20r/s;
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

ssl_certificate     /etc/nginx/certs/web.crt;
ssl_certificate_key /etc/nginx/certs/web.key;
ssl_protocols       TLSv1.2 TLSv1.3;
ssl_session_cache   shared:SSL:5m;

# DNS interne Docker : résolution à la requête (nginx démarre même si un service est arrêté)
resolver 127.0.0.11 valid=30s ipv6=off;

# --- 443 : dashboard + flux vidéo + MQTT over WebSockets ---------------------
server {
    listen 443 ssl;
    server_name ${VM_IP};

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' wss://${VM_IP}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

    auth_basic           "Sentinel-X";
    auth_basic_user_file /etc/nginx/htpasswd;
    limit_req zone=sentinel_req burst=40 nodelay;

    root  /usr/share/nginx/html;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }

    # Flux MJPEG annoté du conteneur vision (même origine que la page)
    location = /video_feed {
        set \$vision_up biometric-vision:5000;
        proxy_pass http://\$vision_up/video_feed;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    # MQTT over WebSockets -> listener interne Mosquitto 9001.
    # Pas d'auth HTTP ici : Mosquitto authentifie (compte dashboard + ACL).
    location = /mqtt {
        auth_basic off;
        set \$mqtt_up mqtt:9001;
        proxy_pass http://\$mqtt_up;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 3600s;
    }
}

# --- 8443 : interface du conteneur vision (enrôlement des visages) -----------
server {
    listen 8443 ssl;
    server_name ${VM_IP};

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    auth_basic           "Sentinel-X Vision";
    auth_basic_user_file /etc/nginx/htpasswd;
    client_max_body_size 10m;

    location / {
        set \$vision_up biometric-vision:5000;
        proxy_pass http://\$vision_up;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
EOF
ok "nginx.conf écrit"

# =============================================================================
# 6. Mosquitto : listener WebSockets interne + ACL + compte dashboard
# =============================================================================
info "Configuration Mosquitto"
cat > mosquitto/config/acl <<EOF
# Généré par deploy_dashboard.sh — appliqué au seul listener WebSockets (9001)
user ${DASH_USER}
topic read sentinel/telemetry
topic read sentinel/alerts
topic read sentinel/host
topic read sentinel/status
topic read \$SYS/#
topic write sentinel/cmd
EOF
chmod 644 mosquitto/config/acl

if ! grep -q '^listener 9001' mosquitto/config/mosquitto.conf; then
cat >> mosquitto/config/mosquitto.conf <<'EOF'

# 3. Listener WebSockets (dashboard) — NON publié : accessible uniquement
#    via nginx (wss://, TLS) sur le réseau Docker interne. Ajouté par deploy_dashboard.sh
listener 9001
protocol websockets
allow_anonymous false
password_file /mosquitto/config/passwd
acl_file /mosquitto/config/acl
EOF
fi

# Ajout du compte (saisie interactive, jamais en argument). Pas de -c : le fichier
# existant (compte de l'ESP8266) est conservé. Propriétaire et droits restaurés ensuite.
PASSWD_OWNER="$(sudo stat -c '%u:%g' mosquitto/config/passwd)"
PASSWD_MODE="$(sudo stat -c '%a' mosquitto/config/passwd)"
echo "Mot de passe MQTT du compte '${DASH_USER}' (il sera saisi dans le dashboard) :"
docker run --rm -it -v "$PWD/mosquitto/config:/cfg" eclipse-mosquitto:latest \
  mosquitto_passwd /cfg/passwd "$DASH_USER"
sudo chown "$PASSWD_OWNER" mosquitto/config/passwd
sudo chmod "$PASSWD_MODE" mosquitto/config/passwd
ok "Mosquitto : listener 9001, ACL et compte '${DASH_USER}' prêts"

# =============================================================================
# 7. Collecteur de métriques hôte -> sentinel/host
# =============================================================================
info "Collecteur sentinel/host"
mkdir -p host_metrics
cat > host_metrics/host_metrics.py <<'PY'
"""Publie CPU / RAM / disque de la VM et la taille du log Mosquitto sur sentinel/host."""
import json, os, time
import psutil
import paho.mqtt.client as mqtt

BROKER   = os.getenv("MQTT_BROKER", "mqtt")
PORT     = int(os.getenv("MQTT_PORT", "1883"))
USER     = os.getenv("MQTT_USER") or None
PASSWORD = os.getenv("MQTT_PASSWORD") or None
TOPIC    = os.getenv("MQTT_TOPIC", "sentinel/host")
INTERVAL = float(os.getenv("INTERVAL_S", "5"))
LOG_FILE = os.getenv("LOG_FILE", "/mqttlog/mosquitto.log")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sentinel-host-metrics")
if USER:
    client.username_pw_set(USER, PASSWORD)
client.reconnect_delay_set(min_delay=1, max_delay=30)
client.connect_async(BROKER, PORT, keepalive=30)
client.loop_start()

psutil.cpu_percent(None)  # première mesure de référence
while True:
    time.sleep(INTERVAL)
    payload = {
        # /proc n'est pas isolé par Docker : CPU et RAM sont ceux de la VM.
        "cpu":  round(psutil.cpu_percent(None), 1),
        "ram":  round(psutil.virtual_memory().percent, 1),
        # Rootfs du conteneur = système de fichiers hébergeant /var/lib/docker sur la VM.
        "disk": round(psutil.disk_usage("/").percent, 1),
        "ts":   int(time.time() * 1000),
    }
    try:
        payload["mqtt_log_bytes"] = os.path.getsize(LOG_FILE)
    except OSError:
        pass
    client.publish(TOPIC, json.dumps(payload), qos=0, retain=True)
PY

cat > host_metrics/Dockerfile <<'EOF'
FROM python:3.11-slim
RUN pip install --no-cache-dir "paho-mqtt>=2,<3" "psutil>=5.9,<8" \
 && useradd --system --uid 10001 --no-create-home metrics
WORKDIR /app
COPY host_metrics.py .
USER metrics
CMD ["python", "-u", "host_metrics.py"]
EOF
ok "Collecteur prêt"

# =============================================================================
# 8. Vision : import json manquant (alertes MQTT jamais publiées)
# =============================================================================
if ! grep -q '^import json$' detection_webcam.py; then
  sed -i '0,/^import os$/s//import os\nimport json/' detection_webcam.py
  grep -q '^import json$' detection_webcam.py || die "Ajout de import json impossible."
fi
ok "Vision : import json présent"

# =============================================================================
# 9. Compose override (nouveaux services + réduction de la surface exposée)
# =============================================================================
info "Écriture de docker-compose.override.yml"
cat > docker-compose.override.yml <<EOF
# Généré par deploy_dashboard.sh — chargé automatiquement par docker compose.
# Supprimer ce fichier (rollback) rétablit les ports et retire les nouveaux services.
services:
  mqtt:
    ports: !override
      - "127.0.0.1:1883:1883"   # MQTT clair : plus exposé hors de la VM
      - "8883:8883"             # MQTTS ESP8266 (filtré par DOCKER-USER)
    volumes:
      - ./mosquitto/config/acl:/mosquitto/config/acl:ro

  timescaledb:
    ports: !override
      - "127.0.0.1:5432:5432"   # PostgreSQL : accès local VM uniquement

  biometric-vision:
    ports: !reset []            # Flask n'est plus exposé : passe par nginx (443/8443)

  host-metrics:
    build:
      context: ./host_metrics
    image: sentinel-host-metrics:latest
    container_name: sentinel_host_metrics
    restart: unless-stopped
    environment:
      MQTT_BROKER: mqtt
      MQTT_PORT: "1883"
      MQTT_USER: \${MQTT_USER:-}
      MQTT_PASSWORD: \${MQTT_PASSWORD:-}
      MQTT_TOPIC: sentinel/host
      INTERVAL_S: "5"
      LOG_FILE: /mqttlog/mosquitto.log
    volumes:
      - ./mosquitto/log:/mqttlog:ro
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    depends_on: [mqtt]
    networks: [sentinel_net]

  dashboard:
    image: nginx:stable-alpine
    container_name: sentinel_dashboard
    restart: unless-stopped
    ports:
      - "${VM_IP}:443:443"
      - "${VM_IP}:8443:8443"
    volumes:
      - ./dashboard/site:/usr/share/nginx/html:ro
      - ./dashboard/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./dashboard/certs:/etc/nginx/certs:ro
      - ./dashboard/htpasswd:/etc/nginx/htpasswd:ro
    security_opt: ["no-new-privileges:true"]
    depends_on: [mqtt, biometric-vision]
    networks: [sentinel_net]
EOF
docker compose config -q || die "docker compose config refuse la configuration (rien n'a été relancé)."
ok "Compose validé"

# =============================================================================
# 10. Pare-feu DOCKER-USER : 443/8443 depuis l'hôte Windows uniquement,
#     + sortie des conteneurs vers le flux FFmpeg (runtime ET rules.v4)
# =============================================================================
info "Règles DOCKER-USER"
RULES=(
  "-s ${CLIENT_IP}/32 -p tcp -m tcp --dport 443 -j ACCEPT"
  "-s ${CLIENT_IP}/32 -p tcp -m tcp --dport 8443 -j ACCEPT"
  "-d ${STREAM_HOST}/32 -p tcp -m tcp --dport ${STREAM_PORT} -j ACCEPT"
)
DROP_COUNT="$(sudo grep -cx -- '-A DOCKER-USER -j DROP' "$RULES_V4" || true)"
[[ "$DROP_COUNT" == "1" ]] || die "rules.v4 : ligne '-A DOCKER-USER -j DROP' trouvée $DROP_COUNT fois (1 attendue)."
for r in "${RULES[@]}"; do
  read -ra spec <<< "$r"
  # Runtime : insertion juste avant le DROP si la règle n'existe pas déjà
  if ! sudo iptables -C DOCKER-USER "${spec[@]}" 2>/dev/null; then
    POS="$(sudo iptables -L DOCKER-USER -n --line-numbers | awk '$2=="DROP"{print $1; exit}')"
    sudo iptables -I DOCKER-USER "${POS:-1}" "${spec[@]}"
  fi
  # Persistance : même règle dans rules.v4, avant la ligne DROP
  if ! sudo grep -qxF -- "-A DOCKER-USER $r" "$RULES_V4"; then
    sudo sed -i "/^-A DOCKER-USER -j DROP\$/i -A DOCKER-USER $r" "$RULES_V4"
  fi
done
ok "Pare-feu à jour (runtime + $RULES_V4)"

# =============================================================================
# 11. Relance de la stack
# =============================================================================
info "Relance (build des images modifiées, recréation des conteneurs impactés)"
docker compose up -d --build
docker compose ps
trap - ERR
cat <<EOF

=============================================================================
 Déploiement terminé. Sauvegardes : $BACKUP_DIR
 Dashboard      : https://${VM_IP}        (auth HTTP : ${WEB_USER})
 Bouton Connexion du dashboard : wss://${VM_IP}/mqtt, compte MQTT '${DASH_USER}'
 Interface vision (enrôlement) : https://${VM_IP}:8443
 Vérifications  : bash verifier_dashboard.sh
 Retour arrière : bash rollback_dashboard.sh $BACKUP_DIR
=============================================================================
EOF
