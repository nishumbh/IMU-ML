/* ESP32-C3 research logger: two MPU6050 IMUs + SPI microSD.
 * Example hardware ONLY; edit pins and driver for your exact board/IMUs.
 * Serial: s = new recording, x = stop/flush. No model or clinical feedback here.
 * Libraries: Adafruit MPU6050, Adafruit Unified Sensor, Adafruit BusIO.
 */
#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <esp_timer.h>
#include <math.h>

// EXAMPLE GPIOs, not a verified wiring diagram for every C3 mini variant.
constexpr int SDA_PIN = 4, SCL_PIN = 5;
constexpr int SD_SCK = 6, SD_MISO = 3, SD_MOSI = 7, SD_CS = 10;
constexpr uint8_t THIGH_ADDRESS = 0x68, SHANK_ADDRESS = 0x69;
constexpr int64_t PERIOD_US = 20000;  // 50 Hz target; timestamps remain actual.
const char *HEADER = "time_ms,thigh_ax,thigh_ay,thigh_az,thigh_gx,thigh_gy,thigh_gz,shank_ax,shank_ay,shank_az,shank_gx,shank_gy,shank_gz";
Adafruit_MPU6050 thigh, shank;
File logFile;
bool ready = false, recording = false;
int64_t startUs = 0, nextUs = 0, lastFlushUs = 0;
uint32_t samples = 0, missed = 0;

void stopRecording(const char *reason) {
  recording = false;
  if (logFile) { logFile.flush(); logFile.close(); }
  Serial.printf("# STOP: %s; samples=%lu missed_deadlines=%lu\n", reason,
                (unsigned long)samples, (unsigned long)missed);
}

void startRecording() {
  if (!ready) { Serial.println("# Hardware not ready; reset after fixing wiring."); return; }
  if (recording) { Serial.println("# Already recording. Press x first."); return; }
  char name[32];
  bool found = false;
  for (unsigned int n = 1; n <= 9999; ++n) {
    snprintf(name, sizeof(name), "/session_%04u.csv", n);
    if (!SD.exists(name)) { found = true; break; }
  }
  if (!found) { Serial.println("# Session names exhausted; back up card."); return; }
  logFile = SD.open(name, FILE_WRITE);
  if (!logFile) { Serial.println("# Cannot create SD file."); return; }
  if (!logFile.println(HEADER)) { stopRecording("SD header write failed"); return; }
  logFile.flush();
  samples = missed = 0;
  startUs = esp_timer_get_time();
  nextUs = lastFlushUs = startUs;
  recording = true;
  Serial.printf("# RECORDING %s (SI: m/s^2, rad/s); x to finish.\n", name);
}

bool finiteEvent(const sensors_event_t &a, const sensors_event_t &g) {
  return isfinite(a.acceleration.x) && isfinite(a.acceleration.y) && isfinite(a.acceleration.z)
      && isfinite(g.gyro.x) && isfinite(g.gyro.y) && isfinite(g.gyro.z);
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // Do not block forever waiting for USB serial.
  Serial.println("# Two-IMU research logger. NOT a medical device.");
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  if (!thigh.begin(THIGH_ADDRESS, &Wire) || !shank.begin(SHANK_ADDRESS, &Wire)) {
    Serial.println("# IMU init failed. MPU6050 AD0: thigh LOW, shank HIGH. Check 3.3V wiring.");
    return;
  }
  for (Adafruit_MPU6050 *imu : {&thigh, &shank}) {
    imu->setAccelerometerRange(MPU6050_RANGE_4_G);
    imu->setGyroRange(MPU6050_RANGE_500_DEG);
    imu->setFilterBandwidth(MPU6050_BAND_10_HZ);
  }
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, SPI, 4000000)) {
    Serial.println("# SD init failed. Check FAT32 card, power, SPI wiring, and CS pin.");
    return;
  }
  ready = true;
  Serial.println("# Ready. s = start new SD recording, x = stop and flush. No on-device inference.");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 's' || c == 'S') startRecording();
    if (c == 'x' || c == 'X') stopRecording("operator stop");
  }
  if (!recording) { delay(1); return; }
  int64_t now = esp_timer_get_time();
  if (now < nextUs) { delay(1); return; }
  // Never manufacture catch-up rows: actual timing gaps must remain visible.
  if (now - nextUs >= PERIOD_US) {
    missed += (uint32_t)((now - nextUs) / PERIOD_US);
    nextUs = now;
  }
  nextUs += PERIOD_US;
  sensors_event_t ta, tg, tt, sa, sg, st;
  const int64_t sampleUs = esp_timer_get_time();
  if (!thigh.getEvent(&ta, &tg, &tt) || !shank.getEvent(&sa, &sg, &st)
      || !finiteEvent(ta, tg) || !finiteEvent(sa, sg)) {
    stopRecording("invalid IMU read; fix wiring and start a fresh session"); return;
  }
  // Sequential reads, not hardware synchronization; measure skew before real studies.
  char row[384];
  int n = snprintf(row, sizeof(row), "%.3f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
    (sampleUs - startUs) / 1000.0,
    ta.acceleration.x, ta.acceleration.y, ta.acceleration.z, tg.gyro.x, tg.gyro.y, tg.gyro.z,
    sa.acceleration.x, sa.acceleration.y, sa.acceleration.z, sg.gyro.x, sg.gyro.y, sg.gyro.z);
  if (n <= 0 || n >= (int)sizeof(row) || logFile.write((const uint8_t *)row, n) != (size_t)n) {
    stopRecording("SD write failed"); return;
  }
  ++samples;
  if (sampleUs - lastFlushUs >= 1000000) {
    logFile.flush();  // May stall! Interface rejects timing gaps; this is a starter logger.
    lastFlushUs = sampleUs;
    Serial.printf("# samples=%lu missed=%lu\n", (unsigned long)samples, (unsigned long)missed);
  }
}
