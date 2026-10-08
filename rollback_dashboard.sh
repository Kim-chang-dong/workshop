#!/usr/bin/env bash
# =============================================================================
# Sentinel-X — Retour arrière de deploy_dashboard.sh
# OÙ L'EXÉCUTER : dans la VM Ubuntu, depuis ~/workshop :
#     cd ~/workshop && bash rollback_dashboard.sh backup-dashboard-<date>
# Restaure : mosquitto.conf, passwd, detection_webcam.py, rules.v4
# Retire   : docker-compose.override.yml, ACL, règles DOCKER-USER ajoutées,
#            conteneurs dashboard et host-metrics
# Conserve : dossiers dashboard/ et host_metrics/ (à supprimer à la main si voulu)
# =============================================================================
set -Eeuo pipefail
BK="${1:-}"
[[ -n "$BK" && -d "$BK" ]] || { echo "Usage : bash rollback_dashboard.sh <dossier_de_sauvegarde>"; exit 1; }
[[ -f docker-compose.yml ]] || { echo "Lancer depuis ~/workshop."; exit 1; }
for f in mosquitto.conf passwd detection_webcam.py rules.v4; do
  [[ -e "$BK/$f" ]] || sudo test -e "$BK/$f" || { echo "Sauvegarde incomplète : $BK/$f manquant."; exit 1; }
done

read -rp "Confirmer le retour arrière depuis $BK ? (oui/non) : " ANS
[[ "$ANS" == "oui" ]] || { echo "Annulé."; exit 0; }

# 1. Fichiers restaurés à l'identique (propriétaire et droits conservés par -p)
cp -p "$BK/mosquitto.conf" mosquitto/config/mosquitto.conf
sudo cp -p "$BK/passwd" mosquitto/config/passwd
cp -p "$BK/detection_webcam.py" detection_webcam.py
sudo cp -p "$BK/rules.v4" /etc/iptables/rules.v4
rm -f mosquitto/config/acl

# 2. Règles DOCKER-USER ajoutées : suppression du runtime (ignorée si absente)
for r in "-s 192.168.50.1/32 -p tcp -m tcp --dport 443 -j ACCEPT" \
         "-s 192.168.50.1/32 -p tcp -m tcp --dport 8443 -j ACCEPT"; do
  read -ra spec <<< "$r"
  sudo iptables -D DOCKER-USER "${spec[@]}" 2>/dev/null || true
done
# La règle 8090 (flux FFmpeg) est volontairement conservée en runtime :
# elle existait avant le script et reste nécessaire à la vision.

# 3. Suppression de l'override, puis relance (retire les conteneurs ajoutés)
mv docker-compose.override.yml "$BK/docker-compose.override.yml.retire" 2>/dev/null || true
docker compose up -d --build --remove-orphans
docker compose ps
echo "Retour arrière terminé."
