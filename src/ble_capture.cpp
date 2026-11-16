#include "ble_capture.h"

#include "ble_record.h"
#include "imu_capture.h"
#include "imu_manager.h"
#include "params.h"
#include "sd_manager.h"
#include "watchdog_manager.h"

BleCapturePeripheral board_ble_capture;

namespace {

BleCapturePeripheral *const peripheral = &board_ble_capture;

}  // namespace

bool BleCapturePeripheral::setup(void) {
    Serial.println(F("ble: init (OMB-Nano peripheral)"));
    wdt.restart();

    if (!BLE.begin()) {
        Serial.println(F("ble: BLE.begin failed; capture service unavailable"));
        return false;
    }

    service_ = new BLEService(ble_capture::service_uuid);
    // The control characteristic accepts a whole MTU-sized write so an
    // over-long START reaches the length check instead of being clamped by
    // the library to the expected size.
    control_ = new BLECharacteristic(ble_capture::control_characteristic_uuid, BLEWrite,
                                     static_cast<int>(ble_capture::max_notification_length));
    data_ = new BLECharacteristic(ble_capture::data_characteristic_uuid, BLENotify,
                                  static_cast<int>(ble_capture::max_notification_length));
    if (service_ == nullptr || control_ == nullptr || data_ == nullptr) {
        Serial.println(F("ble: GATT allocation failed"));
        return false;
    }

    BLE.setLocalName(ble_capture::advertised_name);
    BLE.setDeviceName(ble_capture::advertised_name);
    BLE.setAdvertisedService(*service_);
    service_->addCharacteristic(*control_);
    service_->addCharacteristic(*data_);
    BLE.addService(*service_);
    control_->setEventHandler(BLEWritten, control_written);
    wdt.restart();

    if (!BLE.advertise()) {
        Serial.println(F("ble: advertise failed"));
        return false;
    }

    ble_ready_ = true;
    session_.reset();
    Serial.print(F("ble: advertising as "));
    Serial.println(ble_capture::advertised_name);
    Serial.println(F("ble: waiting for START on the control characteristic"));
    return true;
}

void BleCapturePeripheral::loop(void) {
    if (!ble_ready_) {
        return;
    }

    BLE.poll();
    const bool connected = op_connected(this);
    const bool subscribed = op_subscribed(this);
    session_.refresh_readiness(connected, subscribed);

    if (session_.capture_due()) {
        const ble_capture::CaptureReport report =
            session_.run_capture(capture_hooks(), radio_ops());
        log_report(report);
    }
}

void BleCapturePeripheral::control_written(BLEDevice device,
                                           BLECharacteristic characteristic) {
    (void)device;
    (void)characteristic;
    peripheral->on_control_write();
}

void BleCapturePeripheral::on_control_write(void) {
    if (control_ == nullptr) {
        return;
    }
    const int length = control_->valueLength();
    const uint8_t *payload = control_->value();
    const ble_capture::StartCheck check = session_.request_start(
        payload, length < 0 ? 0 : static_cast<size_t>(length), op_connected(this),
        op_subscribed(this));
    if (check == ble_capture::StartCheck::Accepted) {
        Serial.println(F("ble: START accepted"));
    } else {
        log_start_rejection(check);
    }
}

void BleCapturePeripheral::log_start_rejection(ble_capture::StartCheck check) const {
    Serial.print(F("ble: START rejected ("));
    switch (check) {
        case ble_capture::StartCheck::RejectedLength:
            Serial.print(F("length"));
            break;
        case ble_capture::StartCheck::RejectedOpcode:
            Serial.print(F("opcode"));
            break;
        case ble_capture::StartCheck::RejectedVersion:
            Serial.print(F("version"));
            break;
        case ble_capture::StartCheck::RejectedLink:
            Serial.print(F("not connected/subscribed"));
            break;
        case ble_capture::StartCheck::RejectedState:
            Serial.print(F("state "));
            Serial.print(static_cast<int>(session_.state()));
            break;
        case ble_capture::StartCheck::Accepted:
            break;
    }
    Serial.println(')');
}

void BleCapturePeripheral::log_report(const ble_capture::CaptureReport &report) const {
    Serial.print(F("ble: capture samples "));
    Serial.print(report.sample_count);
    Serial.print(F(" status "));
    Serial.print(report.capture_status);
    Serial.print(F(" sd "));
    Serial.print(report.sd_saved ? 1 : 0);
    Serial.print(F(" end "));
    Serial.print(report.end_sent ? 1 : 0);
    Serial.print(F(" radio_terminated "));
    Serial.println(report.radio_terminated ? 1 : 0);
}

ble_capture::RadioOps BleCapturePeripheral::radio_ops(void) {
    ble_capture::RadioOps ops {};
    ops.context = this;
    ops.is_connected = &BleCapturePeripheral::op_connected;
    ops.is_subscribed = &BleCapturePeripheral::op_subscribed;
    ops.format_record = &BleCapturePeripheral::op_format_record;
    ops.notify = &BleCapturePeripheral::op_notify;
    ops.now_micros = &BleCapturePeripheral::op_now_micros;
    ops.now_millis = &BleCapturePeripheral::op_now_millis;
    return ops;
}

ble_capture::CaptureHooks BleCapturePeripheral::capture_hooks(void) {
    ble_capture::CaptureHooks hooks {};
    hooks.context = this;
    hooks.initialize_imu = &BleCapturePeripheral::hook_initialize_imu;
    hooks.read_sample = &BleCapturePeripheral::hook_read_sample;
    hooks.stop_imu = &BleCapturePeripheral::hook_stop_imu;
    hooks.save_sd = &BleCapturePeripheral::hook_save_sd;
    hooks.service_events = &BleCapturePeripheral::hook_service_events;
    hooks.refresh_watchdog = &BleCapturePeripheral::hook_refresh_watchdog;
    return hooks;
}

bool BleCapturePeripheral::op_connected(void *context) {
    (void)context;
    return BLE.connected();
}

bool BleCapturePeripheral::op_subscribed(void *context) {
    const BleCapturePeripheral *self = static_cast<const BleCapturePeripheral *>(context);
    return self->data_ != nullptr && self->data_->subscribed();
}

size_t BleCapturePeripheral::op_format_record(void *context, uint16_t seq, char *out,
                                              size_t capacity) {
    (void)context;
    if (seq >= imu_capture_samples) {
        return 0;
    }
    return format_capture_record(capture_sample_buffer()[seq], out, capacity);
}

ble_capture::NotifyOutcome BleCapturePeripheral::op_notify(void *context,
                                                           const uint8_t *data,
                                                           size_t length) {
    const BleCapturePeripheral *self = static_cast<const BleCapturePeripheral *>(context);
    if (self->data_ == nullptr || length > ble_capture::max_notification_length) {
        return ble_capture::NotifyOutcome::TerminalFailure;
    }
    // writeValue returns 0 when the library could not hand the notification to
    // the stack. A non-zero return is a local submit, not a delivery guarantee.
    const int result = self->data_->writeValue(data, static_cast<int>(length));
    return result != 0 ? ble_capture::NotifyOutcome::Sent
                       : ble_capture::NotifyOutcome::LocalFailure;
}

uint32_t BleCapturePeripheral::op_now_micros(void *context) {
    (void)context;
    return static_cast<uint32_t>(micros());
}

uint32_t BleCapturePeripheral::op_now_millis(void *context) {
    (void)context;
    return static_cast<uint32_t>(millis());
}

bool BleCapturePeripheral::hook_initialize_imu(void *context) {
    wdt.restart();
    return board_imu_manger.start_IMU_bounded(&service_during_imu_init, context);
}

bool BleCapturePeripheral::hook_read_sample(void *context, uint16_t index) {
    (void)context;
    if (index >= imu_capture_samples) {
        return false;
    }
    float north, east, down, yaw, pitch, roll;
    if (!board_imu_manger.get_new_reading(north, east, down, yaw, pitch, roll)) {
        Serial.println(F("ble: IMU read failed"));
        return false;
    }
    IMUCaptureSample &sample = capture_sample_buffer()[index];
    sample.ms = millis();
    sample.sequence = index;
    board_imu_manger.get_raw_axes(sample.ax, sample.ay, sample.az,
                                  sample.gx, sample.gy, sample.gz);
    sample.acc_D = down;
    return true;
}

void BleCapturePeripheral::hook_stop_imu(void *context) {
    (void)context;
    board_imu_manger.stop_IMU();
    wdt.restart();
}

bool BleCapturePeripheral::hook_save_sd(void *context, uint16_t count) {
    (void)context;
    return board_sd_manager.log_imu_capture(capture_sample_buffer(), count);
}

void BleCapturePeripheral::hook_service_events(void *context) {
    (void)context;
    BLE.poll();
    wdt.restart();
}

void BleCapturePeripheral::hook_refresh_watchdog(void *context) {
    (void)context;
    wdt.restart();
}

void BleCapturePeripheral::service_during_imu_init(void *context) {
    (void)context;
    BLE.poll();
    wdt.restart();
}
