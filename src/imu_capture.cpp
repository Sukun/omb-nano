#include "imu_capture.h"
#include "imu_manager.h"
#include "sd_manager.h"
#include "params.h"
#include "watchdog_manager.h"

void capture_real_imu() {
    // Buffer the one-minute run so SD writes cannot stall sensor acquisition.
    static IMUCaptureSample samples[imu_capture_samples];
    size_t count = 0;
    Serial.println(F("capture: real IMU, 10 Hz; columns ms,seq,ax,ay,az,gx,gy,gz,acc_D"));
    board_imu_manger.start_IMU();
    for (size_t i = 0; i < imu_capture_samples; ++i) {
        float north, east, down, yaw, pitch, roll;
        if (!board_imu_manger.get_new_reading(north, east, down, yaw, pitch, roll)) {
            Serial.println(F("capture: IMU read failed"));
            break;
        }
        IMUCaptureSample &sample = samples[count];
        sample.ms = millis();
        sample.sequence = count;
        board_imu_manger.get_raw_axes(sample.ax, sample.ay, sample.az,
                                      sample.gx, sample.gy, sample.gz);
        sample.acc_D = down;
        print_capture_sample(Serial, sample);
        ++count;
        wdt.restart();
    }
    board_imu_manger.stop_IMU();
    const bool saved = board_sd_manager.log_imu_capture(samples, count);
    Serial.print(F("capture: samples ")); Serial.println(count);
    Serial.println(saved ? F("capture: SD saved") : F("capture: SD not saved"));
    Serial.println(F("capture: complete"));
}
