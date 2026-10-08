import os
import json
import time
import psycopg2
import paho.mqtt.client as mqtt

# ==============================================================================
# CONFIGURATIONS VIA VARIABLES D'ENVIRONNEMENT (Idéal pour Docker)
# ==============================================================================
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "sentinel/#")
MQTT_USER = os.getenv("MQTT_USER", "esp_client")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "SuperSecret123")

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_NAME = os.getenv("DB_NAME", "sentinel_db")
DB_USER = os.getenv("DB_USER", "sentinel_user")
DB_PASS = os.getenv("DB_PASSWORD", os.getenv("DB_PASS", "sentinel_password"))

# ==============================================================================
# INITIALISATION ET CONNEXION BASE DE DONNÉES
# ==============================================================================
def init_database_tables(connection):
    """Crée automatiquement les tables et hypertables si elles n'existent pas."""
    try:
        with connection.cursor() as cur:
            # Table normalisée (sensor_data)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sensor_data (
                    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    device_id VARCHAR(64) NOT NULL,
                    sensor_type VARCHAR(64) NOT NULL,
                    value DOUBLE PRECISION NOT NULL
                );
            """)
            try:
                cur.execute("SELECT create_hypertable('sensor_data', 'time', if_not_exists => TRUE);")
            except Exception:
                pass

            # Table à colonnes larges (mesures)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mesures (
                    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    device_id VARCHAR(64) DEFAULT 'esp8266_sentinel',
                    temp REAL,
                    hum REAL,
                    gaz INT,
                    presence SMALLINT
                );
            """)
            try:
                cur.execute("SELECT create_hypertable('mesures', 'ts', if_not_exists => TRUE);")
            except Exception:
                pass

            # Table alertes
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alertes (
                    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source VARCHAR(64) NOT NULL,
                    type VARCHAR(64) NOT NULL,
                    severite VARCHAR(32) NOT NULL,
                    details JSONB
                );
            """)
            try:
                cur.execute("SELECT create_hypertable('alertes', 'ts', if_not_exists => TRUE);")
            except Exception:
                pass

            connection.commit()
            print("[DB] Tables et hypertables TimescaleDB initialisées avec succès.")
    except Exception as e:
        print(f"[DB] Note lors de l'initialisation des tables : {e}")
        connection.rollback()

def get_db_connection():
    while True:
        try:
            print(f"[DB] Connexion à PostgreSQL/TimescaleDB ({DB_HOST}:{DB_NAME})...")
            conn = psycopg2.connect(
                host=DB_HOST,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS
            )
            print("[DB] Connecté à la base de données PostgreSQL/TimescaleDB !")
            init_database_tables(conn)
            return conn
        except Exception as e:
            print(f"[DB] En attente de la base de données... Erreur: {e}")
            time.sleep(5)

conn = get_db_connection()

# ==============================================================================
# CALLBACKS MQTT
# ==============================================================================
def on_connect(client, userdata, flags, rc, *args):
    if rc == 0:
        print(f"[MQTT] Connecté au broker avec succès ! Souscription au topic : {MQTT_TOPIC}")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"[MQTT] Échec de connexion au broker MQTT, code de retour : {rc}")

def on_message(client, userdata, msg):
    global conn
    try:
        topic = msg.topic
        payload_str = msg.payload.decode('utf-8')
        print(f"[MQTT] Message reçu sur [{topic}] : {payload_str}")
        
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            print("[MQTT] Avertissement : Le message reçu n'est pas un JSON valide.")
            return

        device_id = data.get("device_id", "esp8266_sentinel")

        # Cas 1 : Message d'alerte direct (ex: intrusion vision, anomalie, etc.)
        if "type" in data and ("source" in data or "severite" in data or "alert" in topic):
            source = data.get("source", "vision" if "vision" in topic else "iot")
            alerte_type = data.get("type", "alerte")
            severite = data.get("severite", "haute")
            details = json.dumps(data.get("details", data))
            
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alertes (ts, source, type, severite, details)
                    VALUES (NOW(), %s, %s, %s, %s::jsonb)
                    """,
                    (source, alerte_type, severite, details)
                )
            conn.commit()
            print(f"[DB] Alerte enregistrée : {alerte_type} ({severite}) depuis {source}")
            return

        # Cas 2 : Télémétrie capteurs (supporte les clés courtes temp/hum/gaz/presence ou longues)
        temp_val = data.get('temp', data.get('temperature'))
        hum_val = data.get('hum', data.get('humidity'))
        gaz_val = data.get('gaz', data.get('gas'))
        presence_val = data.get('presence', data.get('motion'))
        alert_val = data.get('alert', data.get('alerte'))

        metrics = {}
        if temp_val is not None:
            metrics['temperature'] = float(temp_val)
        if hum_val is not None:
            metrics['humidity'] = float(hum_val)
        if gaz_val is not None:
            metrics['gas'] = int(gaz_val)
        if presence_val is not None:
            metrics['motion'] = 1.0 if presence_val in [1, True, "1", "true"] else 0.0
        if alert_val is not None:
            metrics['alert'] = 1.0 if alert_val in [1, True, "1", "true"] else 0.0

        if not metrics:
            # Traiter toute autre clé numérique présente dans le JSON
            for k, v in data.items():
                if isinstance(v, (int, float)) and k != "device_id":
                    metrics[k] = float(v)

        with conn.cursor() as cur:
            # Insertion dans la table normalisée (sensor_data)
            for sensor_type, value in metrics.items():
                cur.execute(
                    """
                    INSERT INTO sensor_data (time, device_id, sensor_type, value) 
                    VALUES (NOW(), %s, %s, %s)
                    """,
                    (device_id, sensor_type, value)
                )
            
            # Insertion dans la table mesures large si au moins un capteur présent
            if temp_val is not None or hum_val is not None or gaz_val is not None or presence_val is not None:
                cur.execute(
                    """
                    INSERT INTO mesures (ts, device_id, temp, hum, gaz, presence)
                    VALUES (NOW(), %s, %s, %s, %s, %s)
                    """,
                    (
                        device_id,
                        float(temp_val) if temp_val is not None else None,
                        float(hum_val) if hum_val is not None else None,
                        int(gaz_val) if gaz_val is not None else None,
                        1 if presence_val in [1, True, "1", "true"] else 0
                    )
                )

        conn.commit()
        print(f"[DB] {len(metrics)} métrique(s) insérée(s) pour le périphérique '{device_id}'.")

    except psycopg2.OperationalError as e:
        print(f"[DB] Erreur opérationnelle PostgreSQL ({e}), reconnexion...")
        conn = get_db_connection()
    except Exception as e:
        print(f"[DB] Erreur SQL : {e}")
        try:
            conn.rollback()
        except Exception:
            pass

# ==============================================================================
# INITIALISATION MQTT ET BOUCLE D'ÉCOUTE
# ==============================================================================
# Compatibilité paho-mqtt 1.x et 2.x
try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
except AttributeError:
    client = mqtt.Client()

if MQTT_USER and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

client.on_connect = on_connect
client.on_message = on_message

while True:
    try:
        print(f"[MQTT] Tentative de connexion au broker MQTT ({MQTT_BROKER}:{MQTT_PORT})...")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        break
    except Exception as e:
        print(f"[MQTT] Erreur de connexion ({e}). Nouvel essai dans 5 secondes...")
        time.sleep(5)

print("[MQTT] Démarrage de la boucle d'écoute MQTT...")
client.loop_forever()
