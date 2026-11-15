#ifndef SD_MANAGER_H
#define SD_MANAGER_H

#include "Arduino.h"
#include "board_control.h"  // SD_*_PIN definitions

// Local data logging to the microSD card (simplified-PCB addition).
// On this board the SD card replaces the (DNP) Iridium link as the data sink: the GNSS
// fixes and wave spectra that would otherwise be transmitted are appended to CSV files.
//
// The card is driven over software (bit-banged) SPI (SdFat SoftSpiDriver), because the
// chosen pins (SD_*_PIN in board_control.h) are arbitrary GPIOs, not a hardware SPI IOM.
//
// IMU capture buffers a run and writes SD after the IMU is off.
struct IMUCaptureSample;
class SDManager{
  public:
    // Initialise software SPI + mount the card. Returns false (and disables logging) if
    // no card / wiring problem; never blocks the main program.
    bool setup(void);

    bool is_ready(void) const;

    // Append the newest GNSS fix to GNSS.CSV  (columns: posix,lat_1e7,lon_1e7).
    void log_latest_gnss_fix(void);

    // Append the newest wave spectrum to WAVES.CSV
    // (columns: posix,spectrum_number,Hs,Tz,Tc,Tp,max_value, then nbr_wave_packet_freqs bins).
    void log_latest_wave_packet(void);
    bool log_imu_capture(const IMUCaptureSample *samples, size_t count);
    bool write_test_file(void);  // SD card write test

  private:
    bool ready {false};
};

extern SDManager board_sd_manager;

#endif
