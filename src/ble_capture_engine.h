#ifndef BLE_CAPTURE_ENGINE_H
#define BLE_CAPTURE_ENGINE_H

// Board-side BLE capture protocol and session state machine (PLAN_02).
//
// This file is deliberately free of Arduino dependencies: the host tests
// compile it directly, with the radio operations injected through RadioOps.
// The Arduino glue (GATT service, BLE.poll, IMU, SD) lives in ble_capture.*.

#include <stddef.h>
#include <stdint.h>

namespace ble_capture {

// ---------------------------------------------------------------------------
// Protocol v1 identity. PLAN_03's receiver copies these exact values.
constexpr char advertised_name[] {"OMB-Nano"};
constexpr char service_uuid[] {"4F4D424E-4E41-4E4F-824C-850000000001"};
// Control: BLEWrite (write with response) only.
constexpr char control_characteristic_uuid[] {"4F4D424E-4E41-4E4F-824C-850000000002"};
// Data: BLENotify only, variable length, at most 20 bytes per notification.
constexpr char data_characteristic_uuid[] {"4F4D424E-4E41-4E4F-824C-850000000003"};

constexpr uint8_t protocol_version {0x01};

// START  (host -> Control): 0x01 + version + 8-byte run_id
// BEGIN  (Data):            0x11 + version + 8-byte run_id + uint16 expected_count
// DATA   (Data):            0x12 + uint16 seq + uint8 fragment_index + uint8 fragment_count
//                           + 1..15 CSV bytes
// END    (Data):            0x13 + 8-byte run_id + uint16 actual_count + uint8 capture_status
// All multi-byte fields are little-endian.
constexpr uint8_t opcode_start {0x01};
constexpr uint8_t opcode_begin {0x11};
constexpr uint8_t opcode_data {0x12};
constexpr uint8_t opcode_end {0x13};

constexpr size_t run_id_length {8};
constexpr size_t start_length {2 + run_id_length};
constexpr size_t begin_length {2 + run_id_length + 2};
constexpr size_t data_header_length {1 + 2 + 1 + 1};
constexpr size_t end_length {1 + run_id_length + 2 + 1};

// ATT_MTU 23 leaves 20 bytes of notification payload.
constexpr size_t max_notification_length {20};
constexpr size_t max_fragment_payload {max_notification_length - data_header_length};

constexpr size_t record_buffer_length {256};
constexpr size_t max_fragments_per_record {18};

constexpr uint16_t expected_sample_count {600};
constexpr uint8_t capture_status_ok {0};
constexpr uint8_t capture_status_failed {1};

// Send-service budgets. 待真机验证: PLAN_02 default candidates.
constexpr size_t tx_fragments_per_service {6};
constexpr uint32_t tx_service_budget_micros {5000};
constexpr uint32_t drain_budget_millis {30000};

static_assert(max_fragments_per_record * max_fragment_payload >= record_buffer_length,
              "18 x 15-byte fragments must cover the 256-byte record buffer");
static_assert(begin_length <= max_notification_length, "BEGIN must fit one notification");
static_assert(end_length <= max_notification_length, "END must fit one notification");
static_assert(data_header_length + max_fragment_payload <= max_notification_length,
              "DATA fragments must fit one notification");

enum class State : uint8_t { Idle, Ready, Capturing, Draining, Done };

enum class StartCheck : uint8_t {
    Accepted,
    RejectedLength,
    RejectedOpcode,
    RejectedVersion,
    RejectedLink,   // not connected, or the data characteristic is not subscribed
    RejectedState,  // not READY, or a START is already pending
};

enum class NotifyOutcome : uint8_t { Sent, LocalFailure, TerminalFailure };

enum class ServiceResult : uint8_t {
    Idle,                 // no backlog left
    BudgetExhausted,      // fragment or time budget used up
    StoppedLocal,         // temporary send failure; cursor not advanced
    StoppedTerminal,      // radio session must be disabled
    SerializationFailed,  // record would not fit the 256-byte buffer
};

struct TxBudget {
    size_t max_fragments;
    uint32_t max_micros;
};

constexpr TxBudget tx_service_budget {tx_fragments_per_service, tx_service_budget_micros};

size_t fragment_count_for(size_t record_length);

StartCheck check_start(const uint8_t *payload, size_t length, bool connected,
                       bool subscribed, State state);

// Each encoder returns the message length, or 0 when the arguments are invalid
// or the output buffer is too small.
size_t encode_begin(uint8_t *out, size_t capacity, const uint8_t run_id[run_id_length],
                    uint16_t expected_count);
size_t encode_data(uint8_t *out, size_t capacity, uint16_t seq, uint8_t fragment_index,
                   uint8_t fragment_count, const char *payload, size_t payload_length);
size_t encode_end(uint8_t *out, size_t capacity, const uint8_t run_id[run_id_length],
                  uint16_t actual_count, uint8_t capture_status);

// Bounded drain window (30 s candidate); subtraction form is wrap-safe.
class DrainWindow {
  public:
    void start(uint32_t now_millis) {
        start_millis_ = now_millis;
        started_ = true;
    }
    bool expired(uint32_t now_millis) const {
        return started_ && (now_millis - start_millis_) >= drain_budget_millis;
    }

  private:
    uint32_t start_millis_ {0};
    bool started_ {false};
};

// Injected radio operations. `format_record` fills `out` with the original CSV
// row for `seq` (including the final newline) and returns its length, or 0 when
// the row does not fit: a truncated record must never be sent.
struct RadioOps {
    void *context;
    bool (*is_connected)(void *context);
    bool (*is_subscribed)(void *context);
    size_t (*format_record)(void *context, uint16_t seq, char *out, size_t capacity);
    NotifyOutcome (*notify)(void *context, const uint8_t *data, size_t length);
    uint32_t (*now_micros)(void *context);
    uint32_t (*now_millis)(void *context);
};

// Injected acquisition side (IMU, SD, event pump, watchdog).
struct CaptureHooks {
    void *context;
    bool (*initialize_imu)(void *context);
    bool (*read_sample)(void *context, uint16_t index);
    void (*stop_imu)(void *context);
    bool (*save_sd)(void *context, uint16_t count);
    void (*service_events)(void *context);
    void (*refresh_watchdog)(void *context);
};

struct CaptureReport {
    uint16_t sample_count {0};
    uint8_t capture_status {capture_status_failed};
    bool sd_saved {false};
    bool end_sent {false};
    // A radio session existed at START but ended without a normal END.
    bool radio_terminated {false};
};

// One record's fragments are always formatted once and sent in order; the
// cursor only moves past a fragment the radio accepted.
class TxScheduler {
  public:
    void reset();
    void set_sample_count(uint16_t count);
    ServiceResult service(const RadioOps &radio, const TxBudget &budget);
    bool backlog() const { return tx_seq_ < sample_count_; }
    uint16_t tx_seq() const { return tx_seq_; }
    uint8_t tx_fragment() const { return tx_fragment_; }
    uint16_t sample_count() const { return sample_count_; }

  private:
    static constexpr uint16_t no_record {0xFFFF};

    uint16_t sample_count_ {0};
    uint16_t tx_seq_ {0};
    uint8_t tx_fragment_ {0};
    uint8_t fragment_count_ {0};
    uint16_t record_seq_ {no_record};
    size_t record_length_ {0};
    char record_[record_buffer_length] {};
};

// IDLE -> READY -> CAPTURING -> DRAINING -> DONE for one START per connection.
class BleCaptureSession {
  public:
    BleCaptureSession();
    void reset();

    State state() const { return state_; }
    bool start_pending() const { return start_pending_; }
    bool capture_due() const { return state_ == State::Ready && start_pending_; }
    const uint8_t *run_id() const { return run_id_; }

    // Validates and latches the START; nothing long-running happens here.
    StartCheck request_start(const uint8_t *payload, size_t length, bool connected,
                             bool subscribed);

    // IDLE<->READY upkeep from the real link state; other states are untouched.
    void refresh_readiness(bool connected, bool subscribed);

    CaptureReport run_capture(const CaptureHooks &hooks, const RadioOps &radio);

    const TxScheduler &tx() const { return tx_; }

  private:
    bool submit_end(const RadioOps &radio, uint16_t actual_count, uint8_t status);

    State state_ {State::Idle};
    uint8_t run_id_[run_id_length] {};
    bool start_pending_ {false};
    uint16_t sample_count_ {0};
    TxScheduler tx_;
};

}  // namespace ble_capture

#endif
