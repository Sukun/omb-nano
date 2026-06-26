#include "sd_manager.h"

#include "SdFat.h"

#include "params.h"            // nbr_wave_packet_freqs
#include "gnss_manager.h"      // gnss_manager, fix_information
#include "wave_analyzer.h"     // board_wave_analyzer, Wave_Packet
#include "watchdog_manager.h"  // wdt

// Software (bit-banged) SPI on the simplified-PCB SD pins (not a hardware IOM).
// SoftSpiDriver template parameters are <MISO, MOSI, SCK>.
// This requires SPI_DRIVER_SELECT == 2 in SdFatConfig.h (patched in the Dockerfile).
SoftSpiDriver<SD_MISO_PIN, SD_MOSI_PIN, SD_SCK_PIN> soft_spi;
// the clock argument is ignored for software SPI; keep DEDICATED_SPI for our single user
#define SD_SPI_CONFIG SdSpiConfig(SD_CS_PIN, DEDICATED_SPI, SD_SCK_MHZ(0), &soft_spi)

static SdFat32 sd;   // FAT16/FAT32, enough for buoy log files

SDManager board_sd_manager;

bool SDManager::setup(void){
  ready = false;
  Serial.println(F("SD: init (software SPI)"));

  pinMode(SD_CS_PIN, OUTPUT);
  digitalWrite(SD_CS_PIN, HIGH);
  wdt.restart();

  if (!sd.begin(SD_SPI_CONFIG)){
    Serial.println(F("SD: begin failed (no card / wiring?); logging disabled for this boot"));
    return false;
  }

  ready = true;
  Serial.println(F("SD: ready"));
  return true;
}

bool SDManager::is_ready(void) const {
  return ready;
}

void SDManager::log_latest_gnss_fix(void){
  if (!ready){
    Serial.println(F("SD: not ready, skip GNSS log"));
    return;
  }
  if (gnss_manager.gps_fixes_buffer.size() == 0){
    return;
  }

  fix_information crrt = gnss_manager.gps_fixes_buffer.back();

  File32 file = sd.open("GNSS.CSV", O_WRITE | O_CREAT | O_APPEND);
  if (!file){
    Serial.println(F("SD: cannot open GNSS.CSV"));
    return;
  }

  // posix timestamp, latitude (1e-7 deg), longitude (1e-7 deg)
  file.print(crrt.posix_timestamp); file.print(',');
  file.print(crrt.latitude);        file.print(',');
  file.println(crrt.longitude);

  file.flush();   // wait for write to complete before returning (power-loss safety)
  file.close();
  Serial.println(F("SD: logged GNSS fix"));
  wdt.restart();
}

void SDManager::log_latest_wave_packet(void){
  if (!ready){
    Serial.println(F("SD: not ready, skip wave log"));
    return;
  }
  if (board_wave_analyzer.wave_packet_buffer.size() == 0){
    return;
  }

  Wave_Packet crrt = board_wave_analyzer.wave_packet_buffer.back();

  File32 file = sd.open("WAVES.CSV", O_WRITE | O_CREAT | O_APPEND);
  if (!file){
    Serial.println(F("SD: cannot open WAVES.CSV"));
    return;
  }

  // posix, spectrum_number, Hs, Tz, Tc, max_value, then the discretised Welch bins
  file.print(crrt.posix_timestamp);  file.print(',');
  file.print(crrt.spectrum_number);  file.print(',');
  file.print(crrt.Hs, 4);            file.print(',');
  file.print(crrt.Tz, 4);            file.print(',');
  file.print(crrt.Tc, 4);            file.print(',');
  file.print(crrt.max_value, 6);
  for (size_t i = 0; i < nbr_wave_packet_freqs; i++){
    file.print(',');
    file.print(crrt.array_pwelch[i]);
  }
  file.println();

  file.flush();   // wait for write to complete before returning (power-loss safety)
  file.close();
  Serial.println(F("SD: logged wave packet"));
  wdt.restart();
}

bool SDManager::write_test_file(void){
  if (!ready){
    Serial.println(F("SD: not ready, skip test file"));
    return false;
  }
  File32 file = sd.open("TEST.TXT", O_WRITE | O_CREAT | O_APPEND);
  if (!file){
    Serial.println(F("SD: cannot open TEST.TXT"));
    return false;
  }
  file.print(millis());
  file.print(F(",SD test ok"));
  file.println();
  file.flush();
  file.close();
  Serial.println(F("SD: wrote TEST.TXT"));
  wdt.restart();
  return true;
}
