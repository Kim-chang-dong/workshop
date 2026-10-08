#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <ArduinoJson.h>

// ==========================================
// CONFIGURATION RÉSEAU ET MQTTS
// ==========================================
const char* WIFI_SSID     = "Pixel 8 Pro";
const char* WIFI_PASS     = "dbsycpgestion";
const char* MQTT_SERVER   = "10.188.125.209";
const int   MQTT_PORT     = 8883;
const char* MQTT_TOPIC        = "sentinel/telemetry";
const char* MQTT_TOPIC_STATUS = "sentinel/status"; // [NOUVEAU] Topic pour le Last Will (LWT)

// Certificat Root CA (SentinelCA) encodé au format PEM
const char CA_CERT[] PROGMEM = R"PEM(
-----BEGIN CERTIFICATE-----
MIIDCzCCAfOgAwIBAgIUY1dUnnoiI6UQpwdK8Qor3Bpk7MswDQYJKoZIhvcNAQEL
BQAwFTETMBEGA1UEAwwKU2VudGluZWxDQTAeFw0yNjEwMDYxMDE4NDdaFw0yNzEw
MDYxMDE4NDdaMBUxEzARBgNVBAMMClNlbnRpbmVsQ0EwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQDKTwNOdthJLVxHt1swnifEoMxcZXnp8N5R/Uzk09yR
CBykbBFHIfnFqaPKWkk96iA3F/5ZDPjqmrQQPPARyjMcQLaMXHLn95eLVThOOWHV
3FHExA9jwfj41ZOF/mzyyI3Dc/nIfUvSnwPHrHTIZlebXC4l15imuJbGsyA8NoJx
1XsvCF9jeutT+vFi18WugV5gxjm0YYgtO9K5G4CWVEYqJf4wR4/Sqa76nZcsC7Mr
R/gmf+J1/R7tph4iExQxdiFYCosJXaYKii8Ua87JHp5YaoDEjUWyrGDRfAa3ySQO
YPcs6h1TuweJd4K8DCPn/2nMASbGmN44Eiit/bnm1PStAgMBAAGjUzBRMB0GA1Ud
DgQWBBTFXH1KPfKr/5TLruhzyqOuX0V2wTAfBgNVHSMEGDAWgBTFXH1KPfKr/5TL
ruhzyqOuX0V2wTAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQBj
U2fOxqIpw+IFzdMt5MR6Ov0pj5Q08jR2xfSpBY4V2C1Te5WmNQuqP5gv6S+Q5ODR
jVWSWrjrS7ZAilBKWgcoqlk2wYWO/hC/UbZ0hwaLakwdLOqwLQ3BzYVo4uT5xESW
S1/XZNHnafL/UqG9mhWHg6BIblWXOJnznSGwF8AflqcxHN+UHtMElP4XkhmZ4Pwr
v69o/zHNu0/8svxOcq7GuwT2W3DgR37NqdwVBmKhxSjr4YBfQpIIQLyD5IqPAQru
Z0fhon+YnG5RrQabWjerYnsBow+Q+K3rHDRTEFfzlUz/VBmF8RqS8Y0XHlrYv1cP
Dp9bOk86E8SkwFGpUbiH
-----END CERTIFICATE-----
)PEM";

// Clé publique du broker Mosquitto (server.crt, signé par Sentinel-G16-CA).
const char SERVER_PUBKEY[] PROGMEM = R"KEY(
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxOsrZ/AEn8LA9qUtuAxu
7kKXngpWWxhjfjFoOVR791YHyb0C34YTcoH36mu/Wty4cmXpha1NyQE4IlNMUspQ
U+3EqoMns15UQWoW5076NfuMnQD99/9Ie7/Uvch/xMb/1x6usa/hFBzBD5tpVlRP
fV0f3Thm7UzIwv0zwnxRsdtsZRnWB0BFGjGyulWLwY55Lmr2rrlCcNVilacibSee
Ytf9b/HQJ6z5z5+XtflXbD+qF71R21PCVf25rFOEkTzLz63wOaGduxnW/YQxT9AD
XTYbPOhm+j1hk9xIkOtTD+wDsfrrQ9AzXWu4aM+haTl87BdlvlG6MjqONi30HxG/
YwIDAQAB
-----END PUBLIC KEY-----
)KEY";

// ==========================================
// DÉFINITION DES BROCHES (PINOUT ESP8266)
// ==========================================
#define PIN_OLED_SDA  D2  // GPIO4
#define PIN_OLED_SCL  D1  // GPIO5
#define PIN_DHT       D5  // GPIO14
#define PIN_PIR       D6  // GPIO12
#define PIN_BUZZER    D7  // GPIO13
#define PIN_LED_RED   D0  // GPIO16
#define PIN_LED_GREEN D8  // GPIO15
#define PIN_MQ2       A0  // ADC0 (0-3.3V via pont diviseur)

// ==========================================
// INSTANCIATION DES OBJETS
// ==========================================
Adafruit_SSD1306 display(128, 64, &Wire, -1);
DHT dht(PIN_DHT, DHT22);
WiFiClientSecure espClient;
PubSubClient client(espClient);

BearSSL::PublicKey serverKey(SERVER_PUBKEY);

unsigned long lastMsgTime = 0;

// ==========================================
// FONCTIONS DE CONNEXION
// ==========================================
void setupWifi() {
    delay(10);
    Serial.println();
    Serial.print("Connexion au reseau Wi-Fi : ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }

    Serial.println("");
    Serial.println("Wi-Fi connecte !");
    Serial.print("Adresse IP ESP8266 : ");
    Serial.println(WiFi.localIP());
}

void reconnectMqtt() {
    while (!client.connected()) {
        Serial.print("Tentative de connexion MQTTS...");
        String clientId = "ESP8266Sentinel-";
        clientId += String(random(0xffff), HEX);

        // [MODIF] Configuration du Last Will (LWT)
        // client.connect(ID, utilisateur, mdp, topic_lwt, qos, retain, message_lwt)
        if (client.connect(clientId.c_str(), "esp_client", "SuperSecret123", MQTT_TOPIC_STATUS, 1, true, "offline")) {
            Serial.println(" Connecte au broker MQTT !");
            // [NOUVEAU] Publie l'état en ligne lors de la reconnexion réussie
            client.publish(MQTT_TOPIC_STATUS, "online", true);
        } else {
            Serial.print(" Echec, rc=");
            Serial.print(client.state());
            Serial.println(" Nouvel essai dans 5 secondes...");
            delay(5000);
        }
    }
}

// ==========================================
// SETUP
// ==========================================
void setup() {
    Serial.begin(115200);

    // Configuration des broches E/S
    pinMode(PIN_PIR, INPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    digitalWrite(PIN_BUZZER, LOW);
    digitalWrite(PIN_LED_RED, LOW);
    digitalWrite(PIN_LED_GREEN, HIGH);

    // Initialisation du DHT22
    dht.begin();

    // Initialisation OLED
    Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println(F("Erreur d'initialisation OLED"));
        for (;;);
    }

    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor(0, 10);
    display.println("Initialisation...");
    display.display();

    // Connexion Wi-Fi
    setupWifi();

    // Validation TLS par épinglage de la clé publique du broker
    espClient.setKnownKey(&serverKey);

    // Configuration du serveur MQTT
    client.setServer(MQTT_SERVER, MQTT_PORT);
}

// ==========================================
// LOOP
// ==========================================
void loop() {
    if (!client.connected()) {
        reconnectMqtt();
    }
    client.loop();

    // Envoi des données de télémétrie toutes les 2 secondes
    unsigned long now = millis();
    if (now - lastMsgTime > 2000) {
        lastMsgTime = now;

        // 1. LECTURE DES CAPTEURS
        float temp = dht.readTemperature();
        float hum  = dht.readHumidity();
        int mq2Value = analogRead(PIN_MQ2);
        bool pirState = digitalRead(PIN_PIR);

        // 2. CALCUL DE L'ÉTAT D'ALERTE GLOBAL
        bool alertTriggered = pirState || (mq2Value > 400);

        // Actuateurs locaux (LEDs + Buzzer)
        if (alertTriggered) {
            digitalWrite(PIN_LED_RED, HIGH);
            digitalWrite(PIN_LED_GREEN, LOW);
            digitalWrite(PIN_BUZZER, HIGH);
        } else {
            digitalWrite(PIN_LED_RED, LOW);
            digitalWrite(PIN_LED_GREEN, HIGH);
            digitalWrite(PIN_BUZZER, LOW);
        }

        // 3. AFFICHAGE OLED
        display.clearDisplay();
        display.setTextSize(1);
        display.setTextColor(WHITE);

        display.setCursor(0, 0);
        display.print("--- SENTINEL-X --- ");
        display.println(alertTriggered ? "!" : "OK");

        display.setCursor(0, 14);
        display.print("Temp : ");
        if (isnan(temp)) display.print("Err");
        else { display.print(temp, 1); display.write(247); display.print("C"); }

        display.setCursor(0, 26);
        display.print("Hum  : ");
        if (isnan(hum)) display.print("Err");
        else { display.print(hum, 1); display.print(" %"); }

        display.setCursor(0, 38);
        display.print("Gaz  : ");
        display.print(mq2Value);

        display.setCursor(0, 50);
        display.print("Mouv : ");
        display.print(pirState ? "DETECTE" : "RAS");

        display.display();

        // 4. ENVOI DE LA TÉLÉMÉTRIE MQTT (JSON)
        // [MODIF] Augmentation de la taille de 256 à 384 pour accueillir les nouvelles données
        StaticJsonDocument<384> doc;

        // Données d'origine
        doc["device_id"]   = "esp8266_sentinel";
        doc["temperature"] = isnan(temp) ? 0.0 : temp;
        doc["humidity"]    = isnan(hum) ? 0.0 : hum;
        doc["gas"]         = mq2Value;
        doc["motion"]      = pirState;
        doc["alert"]       = alertTriggered;

        // [NOUVEAU] Données de monitoring
        doc["ip"]          = WiFi.localIP().toString(); // Adresse IP sous forme de chaîne de caractères
        doc["rssi"]        = WiFi.RSSI();               // Force du signal Wi-Fi en dBm (ex: -65)
        doc["uptime"]      = millis() / 1000;           // Temps de fonctionnement en secondes

        char jsonBuffer[384];
        serializeJson(doc, jsonBuffer);

        Serial.print("Envoi MQTT sur ");
        Serial.print(MQTT_TOPIC);
        Serial.print(" : ");
        Serial.println(jsonBuffer);

        client.publish(MQTT_TOPIC, jsonBuffer);
    }
}