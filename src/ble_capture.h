#ifndef BLE_CAPTURE_H
#define BLE_CAPTURE_H

#include "Arduino.h"

#include <ArduinoBLE.h>

#include "ble_capture_engine.h"

// Board-side BLE capture peripheral (PLAN_02).
//
// One connection session triggers one 600-sample run: the host writes a START
// to the control characteristic, the board reports BEGIN, streams DATA
// fragments during capture, drains the backlog afterwards and finishes with
// END. The protocol/state logic lives in ble_capture_engine.* and is covered by
// host tests; this class only wires it to ArduinoBLE, the real IMU and SD.
class BleCapturePeripheral {
  public:
    // GATT setup + advertising. Returns false when the BLE stack cannot start;
    // the caller still keeps the board alive.
    bool setup(void);

    // Servicing entry point for the BLE-mode main loop.
    void loop(void);

  private:
    static void control_written(BLEDevice device, BLECharacteristic characteristic);

    void on_control_write(void);
    void log_start_rejection(ble_capture::StartCheck check) const;
    void log_report(const ble_capture::CaptureReport &report) const;

    ble_capture::RadioOps radio_ops(void);
    ble_capture::CaptureHooks capture_hooks(void);

    static bool op_connected(void *context);
    static bool op_subscribed(void *context);
    static size_t op_format_record(void *context, uint16_t seq, char *out, size_t capacity);
    static ble_capture::NotifyOutcome op_notify(void *context, const uint8_t *data,
                                                size_t length);
    static uint32_t op_now_micros(void *context);
    static uint32_t op_now_millis(void *context);

    static bool hook_initialize_imu(void *context);
    static bool hook_read_sample(void *context, uint16_t index);
    static void hook_stop_imu(void *context);
    static bool hook_save_sd(void *context, uint16_t count);
    static void hook_service_events(void *context);
    static void hook_refresh_watchdog(void *context);
    static void service_during_imu_init(void *context);

    BLEService *service_ {nullptr};
    BLECharacteristic *control_ {nullptr};
    BLECharacteristic *data_ {nullptr};
    ble_capture::BleCaptureSession session_ {};
    bool ble_ready_ {false};
};

extern BleCapturePeripheral board_ble_capture;

#endif
