#ifndef IMU_CAPTURE_H
#define IMU_CAPTURE_H

#include "Arduino.h"

// Numeric CSV: ms,sequence,ax,ay,az,gx,gy,gz,acc_D.
// Six-axis values are the latest unfiltered sensor event at each 10 Hz output,
// in m/s^2 and rad/s. acc_D is the existing filtered vertical acceleration.
struct IMUCaptureSample {
    unsigned long ms;
    unsigned long sequence;
    float ax, ay, az, gx, gy, gz, acc_D;
};

// The same formatter writes to Serial and SD; no header or text in data rows.
inline size_t print_capture_sample(Print &out, const IMUCaptureSample &sample) {
    size_t n = out.print(sample.ms);
    n += out.print(','); n += out.print(sample.sequence);
    const float values[] = {sample.ax, sample.ay, sample.az,
                            sample.gx, sample.gy, sample.gz, sample.acc_D};
    for (float value : values) {
        n += out.print(','); n += out.print(value, 6);
    }
    n += out.print('\n');
    return n;
}

// The single 600-sample run buffer, shared by the serial and BLE capture paths.
IMUCaptureSample *capture_sample_buffer();

void capture_real_imu();
#endif
