# Sentinel-X — Dashboard de supervision (front-end)

Page web statique en JS natif. Se connecte directement au broker Mosquitto en
MQTT over WebSockets (mqtt.js 5.16.0 et Chart.js 4.5.1 embarqués dans vendor/,
aucun accès Internet requis). Aucune donnée simulée.

## Fichiers
- index.html / style.css / app.js : l'interface
- config.js : URL du broker, topics, clés JSON, seuil hors-ligne, URL webcam
- vendor/ : librairies + licences (MIT)

## Métriques et topics consommés

| Topic | Producteur | Contenu attendu | État |
|---|---|---|---|
| sentinel/telemetry | ESP8266 | {temperature, humidity, gas, motion, alert} | Existe (firmware actuel) |
| sentinel/telemetry (champs optionnels) | ESP8266 | ip, rssi (dBm), uptime (s) | À ajouter au firmware si voulu |
| sentinel/cmd | Dashboard | {"buzzer":0/1, "led":"rouge"/"verte"/"off"} | Le firmware doit s'y abonner |
| sentinel/alerts | YOLO / IA / API | {source, type, severite, ts, details} | À produire |
| sentinel/status | ESP8266 (Last Will) | texte, ex. online / offline | Optionnel |
| sentinel/host | Script sur le PC serveur | {cpu, ram, disk, mqtt_log_bytes} | Optionnel (MCO) |
| $SYS/broker/... | Mosquitto | clients, charge 1 min, octets, perdus, heap, version | Natif (man mosquitto(8)) |

## Prérequis côté broker
- Un listener `protocol websockets` (man mosquitto.conf(5)), idéalement en TLS (wss://).
- Le navigateur doit faire confiance à la CA SentinelCA, et le certificat serveur
  doit contenir l'IP/nom utilisé dans l'URL (champ SAN).
- Compte dashboard avec ACL : lecture sentinel/telemetry, sentinel/alerts,
  sentinel/host, sentinel/status, $SYS/# ; écriture sentinel/cmd.

## Servir la page
N'importe quel serveur web statique (ex. conteneur nginx) pointant sur ce dossier.

## Palette
Couleurs relevées sur la couverture officielle du sujet EPSI (fond marine
#101731 → #1d3885, bleu #2260e9, et les couleurs du logo : cyan #36b6d4,
orange #e4803e, rouge #d94759, jaune #facf53, vert #8fbe54).
