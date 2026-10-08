-- ==============================================================================
-- Initialisation de la Base de Données Sentinel-X (TimescaleDB / PostgreSQL)
-- ==============================================================================

-- Activation de l'extension TimescaleDB si non activée
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 1. Table normalisée 'sensor_data' (utilisée par le script d'ingestion API Python)
CREATE TABLE IF NOT EXISTS sensor_data (
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    device_id VARCHAR(64) NOT NULL,
    sensor_type VARCHAR(64) NOT NULL,
    value DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('sensor_data', 'time', if_not_exists => TRUE);

-- Index pour accélérer les requêtes Grafana
CREATE INDEX IF NOT EXISTS idx_sensor_data_type_time ON sensor_data (sensor_type, time DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_data_device ON sensor_data (device_id, time DESC);

-- 2. Table 'mesures' (format colonne large - conforme au sujet d'origine)
CREATE TABLE IF NOT EXISTS mesures (
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    device_id VARCHAR(64) DEFAULT 'esp8266_sentinel',
    temp REAL,
    hum REAL,
    gaz INT,
    presence SMALLINT
);
SELECT create_hypertable('mesures', 'ts', if_not_exists => TRUE);

-- 3. Table 'alertes' (alertes de sécurité, intrusion vision, détection prédictive)
CREATE TABLE IF NOT EXISTS alertes (
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source VARCHAR(64) NOT NULL,
    type VARCHAR(64) NOT NULL,
    severite VARCHAR(32) NOT NULL,
    details JSONB
);
SELECT create_hypertable('alertes', 'ts', if_not_exists => TRUE);

-- Index pour requêtes alertes
CREATE INDEX IF NOT EXISTS idx_alertes_ts ON alertes (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alertes_source ON alertes (source, ts DESC);

