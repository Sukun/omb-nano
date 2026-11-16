// Host-side checks for the bounded BLE record serializer (no board required).
//
// Compiled by tests/test_ble_capture.py against a minimal Arduino.h stub, so
// the real print_capture_sample() formatter and the 256-byte sink are what run.

#include <cstdio>
#include <cstring>
#include <string>

#include "ble_capture_engine.h"
#include "ble_record.h"

namespace {

int failures = 0;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);    \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

#define CHECK_EQ(actual, expected)                                              \
    do {                                                                        \
        const long long actual_value = static_cast<long long>(actual);           \
        const long long expected_value = static_cast<long long>(expected);       \
        if (actual_value != expected_value) {                                   \
            std::printf("FAIL %s:%d: %s == %s (got %lld, want %lld)\n",         \
                        __FILE__, __LINE__, #actual, #expected, actual_value,    \
                        expected_value);                                        \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

IMUCaptureSample sample(unsigned long ms, unsigned long sequence, float ax, float ay,
                        float az, float gx, float gy, float gz, float acc_d) {
    IMUCaptureSample value {};
    value.ms = ms;
    value.sequence = sequence;
    value.ax = ax;
    value.ay = ay;
    value.az = az;
    value.gx = gx;
    value.gy = gy;
    value.gz = gz;
    value.acc_D = acc_d;
    return value;
}

int case_plausible_row() {
    const IMUCaptureSample value =
        sample(1234567UL, 599UL, -0.123456f, 0.0f, 9.81f, -0.01f, 0.02f, 0.0f, 9.8f);
    char record[ble_capture::record_buffer_length];
    memset(record, 0, sizeof(record));
    const size_t length = format_capture_record(value, record, sizeof(record));

    const std::string expected =
        "1234567,599,-0.123456,0.000000,9.810000,-0.010000,0.020000,0.000000,9.800000\n";
    CHECK_EQ(length, expected.size());
    CHECK(length > 0);
    CHECK(std::string(record, length) == expected);
    CHECK_EQ(record[length - 1], '\n');
    CHECK(length < ble_capture::record_buffer_length);
    return failures;
}

int case_longest_plausible_row_fits() {
    // Widest realistic field widths: 10-digit ms, 3-digit sequence, 12-character
    // fixed-point values (sign + 3 integer digits + 6 decimals + separators).
    const IMUCaptureSample value =
        sample(4294967295UL, 599UL, -40.123456f, -40.123456f, 40.123456f,
               -20.123456f, -20.123456f, 20.123456f, -20.123456f);
    char record[ble_capture::record_buffer_length];
    const size_t length = format_capture_record(value, record, sizeof(record));

    CHECK(length > 0);
    CHECK(length < ble_capture::record_buffer_length);
    CHECK_EQ(record[length - 1], '\n');
    CHECK_EQ(ble_capture::fragment_count_for(length) <= ble_capture::max_fragments_per_record,
             1);
    return failures;
}

int case_capacity_boundary() {
    const IMUCaptureSample value =
        sample(1000UL, 0UL, 0.5f, -0.5f, 9.81f, 0.01f, -0.01f, 0.0f, 9.8f);
    char reference[ble_capture::record_buffer_length];
    const size_t length = format_capture_record(value, reference, sizeof(reference));
    CHECK(length > 0);

    char exact[ble_capture::record_buffer_length];
    CHECK_EQ(format_capture_record(value, exact, length), length);
    CHECK(memcmp(exact, reference, length) == 0);

    char too_small[ble_capture::record_buffer_length];
    CHECK_EQ(format_capture_record(value, too_small, length - 1), 0);
    CHECK(memcmp(too_small, reference, length - 1) == 0);  // only the prefix was written
    return failures;
}

int case_small_capacity_is_refused() {
    const IMUCaptureSample value =
        sample(1UL, 0UL, 0.0f, 0.0f, 9.81f, 0.0f, 0.0f, 0.0f, 9.8f);
    char record[ble_capture::record_buffer_length];
    CHECK_EQ(format_capture_record(value, record, 8), 0);
    CHECK_EQ(format_capture_record(value, record, 0), 0);
    return failures;
}

struct NamedCase {
    const char *name;
    int (*run)();
};

const NamedCase cases[] = {
    {"plausible_row", case_plausible_row},
    {"longest_plausible_row_fits", case_longest_plausible_row_fits},
    {"capacity_boundary", case_capacity_boundary},
    {"small_capacity_is_refused", case_small_capacity_is_refused},
};

}  // namespace

int main(int argc, char **argv) {
    if (argc != 2) {
        std::printf("usage: %s <case>\n", argv[0]);
        return 2;
    }
    for (const NamedCase &entry : cases) {
        if (std::strcmp(entry.name, argv[1]) == 0) {
            const int result = entry.run();
            if (result == 0) {
                std::printf("PASS %s\n", entry.name);
                return 0;
            }
            std::printf("FAILED %s (%d checks)\n", entry.name, result);
            return 1;
        }
    }
    std::printf("unknown case: %s\n", argv[1]);
    return 2;
}
