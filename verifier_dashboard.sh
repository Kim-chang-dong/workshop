#!/usr/bin/env bash
# =============================================================================
# Sentinel-X — Vérifications après deploy_dashboard.sh (LECTURE SEULE)
# OÙ L'EXÉCUTER : dans la VM Ubuntu, depuis ~/workshop :
#     cd ~/workshop && bash verifier_dashboard.sh
# Aucune modification n'est faite par ce script.
# =============================================================================
set -uo pipefail
VM_IP="192.168.50.10"
CA_CRT="mosquitto/config/certs/ca.crt"

sec() { printf '\n\e[36m=== %s ===\e[0m\n' "$*"; }

sec "1. Conteneurs (tous doivent être Up)"
docker compose ps

sec "2. Syntaxe nginx (attendu : syntax is ok / test is successful)"
docker exec sentinel_dashboard nginx -t

sec "3. Certificat web : SAN et chaîne (attendu : IP Address:${VM_IP} puis OK)"
openssl x509 -in dashboard/certs/web.crt -noout -subject -enddate -ext subjectAltName
openssl verify -CAfile "$CA_CRT" dashboard/certs/web.crt

sec "4. HTTPS 443 sans identifiants (attendu : 401 = authentification exigée)"
curl -s --cacert "$CA_CRT" -o /dev/null -w "%{http_code}\n" "https://${VM_IP}/"

sec "5. HTTPS 8443 interface vision sans identifiants (attendu : 401)"
curl -s --cacert "$CA_CRT" -o /dev/null -w "%{http_code}\n" "https://${VM_IP}:8443/"

sec "6. Ports en écoute sur la VM (attendu : 1883 et 5432 sur 127.0.0.1, 443/8443 sur ${VM_IP}, plus de 5000)"
sudo ss -ltnp | grep -E ':(443|8443|1883|5432|5000|8883|9001)\b' || true

sec "7. Mosquitto : listener WebSockets 9001 ouvert (attendu : ligne Opening websockets listen socket on port 9001)"
docker compose logs --tail 60 mqtt 2>/dev/null | grep -iE "9001|websocket|error" || echo "(aucune ligne trouvée dans les 60 dernières lignes)"

sec "8. Métriques hôte publiées sur sentinel/host (attendu : un JSON cpu/ram/disk sous 10 s)"
docker compose exec -T mqtt mosquitto_sub -h localhost -p 1883 -t sentinel/host -C 1 -W 10 \
  || echo "(rien reçu en 10 s — si 1883 exige une authentification, ajouter -u/-P)"

sec "9. Vision : import json présent dans le conteneur (attendu : 1)"
docker compose exec -T biometric-vision grep -c "^import json$" /app/detection_webcam.py

sec "10. Pare-feu DOCKER-USER (attendu : règles 443, 8443, 8090 AVANT le DROP)"
sudo iptables -L DOCKER-USER -n --line-numbers

sec "11. Persistance rules.v4 (attendu : les 3 lignes avant -A DOCKER-USER -j DROP)"
sudo grep -n "DOCKER-USER" /etc/iptables/rules.v4
