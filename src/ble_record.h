#ifndef BLE_RECORD_H
#define BLE_RECORD_H

// One CSV capture row as a bounded buffer for the BLE DATA path (PLAN_02).
//
// The row is produced by the real print_capture_sample(), so the bytes sent
// over BLE are exactly the bytes the serial and SD paths write. A row that
// would overrun the buffer is reported as a serialization failure instead of
// being truncated.

#include "Arduino.h"
#include "imu_capture.h"

class BoundedRecordPrint : public Print {
  public:
    BoundedRecordPrint(char *buffer, size_t capacity)
        : buffer_(buffer), capacity_(capacity) {}

    size_t write(uint8_t byte) override {
        if (length_ >= capacity_) {
            overflowed_ = true;
            return 0;
        }
        buffer_[length_++] = static_cast<char>(byte);
        return 1;
    }

    size_t write(const uint8_t *data, size_t size) override {
        size_t written = 0;
        while (written < size) {
            if (length_ >= capacity_) {
                overflowed_ = true;
                return written;
            }
            buffer_[length_++] = static_cast<char>(data[written++]);
        }
        return written;
    }

    using Print::write;

    bool overflowed() const { return overflowed_; }
    size_t length() const { return length_; }

  private:
    char *buffer_;
    size_t capacity_;
    size_t length_ {0};
    bool overflowed_ {false};
};

// Returns the CSV row length (including the final newline), or 0 when the row
// does not fit `capacity` bytes.
inline size_t format_capture_record(const IMUCaptureSample &sample, char *out,
                                    size_t capacity) {
    BoundedRecordPrint sink(out, capacity);
    print_capture_sample(sink, sample);
    return sink.overflowed() ? 0 : sink.length();
}

#endif
