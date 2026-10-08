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
