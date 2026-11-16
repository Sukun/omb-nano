// Host-side checks for the PLAN_02 BLE capture engine (no Arduino, no radio).
//
// Compiled and run by tests/test_ble_capture.py. Each case is selected by
// argv[1]; the process exits non-zero when any CHECK fails and prints
// "PASS <case>" on success so the Python test can show what ran.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "ble_capture_engine.h"

using namespace ble_capture;

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

struct Rig {
    bool connected {true};
    bool subscribed {true};

    int notify_calls {0};
    int local_failure_at {-1};
    int terminal_failure_at {-1};
    int notify_after_terminal {0};
    bool failed {false};

    uint32_t micros_now {0};
    uint32_t micros_advance {500};
    uint32_t millis_now {0};
    uint32_t millis_advance {0};

    size_t record_length {64};
    uint16_t format_fail_seq {0xFFFF};

    std::vector<std::vector<uint8_t>> packets;
    std::vector<uint16_t> formatted;

    bool init_ok {true};
    uint16_t samples_ok {expected_sample_count};
    bool sd_ok {true};
    int init_calls {0};
    int stop_calls {0};
    int save_calls {0};
    int service_calls {0};
    int watchdog_calls {0};
    int read_calls {0};
    uint16_t saved_count {0};
};

bool rig_connected(void *context) { return static_cast<Rig *>(context)->connected; }

bool rig_subscribed(void *context) { return static_cast<Rig *>(context)->subscribed; }

size_t rig_format(void *context, uint16_t seq, char *out, size_t capacity) {
    Rig *rig = static_cast<Rig *>(context);
    rig->formatted.push_back(seq);
    if (seq == rig->format_fail_seq) {
        return 0;
    }
    if (rig->record_length == 0 || rig->record_length > capacity) {
        return 0;
    }
    for (size_t i = 0; i < rig->record_length; ++i) {
        out[i] = static_cast<char>('a' + ((seq + i) % 26));
    }
    out[rig->record_length - 1] = '\n';
    return rig->record_length;
}

NotifyOutcome rig_notify(void *context, const uint8_t *data, size_t length) {
    Rig *rig = static_cast<Rig *>(context);
    if (rig->failed) {
        ++rig->notify_after_terminal;
        return NotifyOutcome::TerminalFailure;
    }
    if (rig->terminal_failure_at >= 0 && rig->notify_calls == rig->terminal_failure_at) {
        rig->failed = true;
        ++rig->notify_calls;
        return NotifyOutcome::TerminalFailure;
    }
    if (rig->local_failure_at >= 0 && rig->notify_calls >= rig->local_failure_at) {
        ++rig->notify_calls;
        return NotifyOutcome::LocalFailure;
    }
    rig->packets.emplace_back(data, data + length);
    ++rig->notify_calls;
    rig->micros_now += rig->micros_advance;
    return NotifyOutcome::Sent;
}

uint32_t rig_now_micros(void *context) { return static_cast<Rig *>(context)->micros_now; }

uint32_t rig_now_millis(void *context) { return static_cast<Rig *>(context)->millis_now; }

bool rig_init_imu(void *context) {
    Rig *rig = static_cast<Rig *>(context);
    ++rig->init_calls;
    rig->millis_now += 1000;
    return rig->init_ok;
}

bool rig_read_sample(void *context, uint16_t index) {
    Rig *rig = static_cast<Rig *>(context);
    ++rig->read_calls;
    return index < rig->samples_ok;
}

void rig_stop_imu(void *context) { ++static_cast<Rig *>(context)->stop_calls; }

bool rig_save_sd(void *context, uint16_t count) {
    Rig *rig = static_cast<Rig *>(context);
    ++rig->save_calls;
    rig->saved_count = count;
    return rig->sd_ok;
}

void rig_service_events(void *context) {
    Rig *rig = static_cast<Rig *>(context);
    ++rig->service_calls;
    rig->millis_now += rig->millis_advance;
}

void rig_refresh_watchdog(void *context) { ++static_cast<Rig *>(context)->watchdog_calls; }

RadioOps make_radio(Rig *rig) {
    RadioOps ops {};
    ops.context = rig;
    ops.is_connected = rig_connected;
    ops.is_subscribed = rig_subscribed;
    ops.format_record = rig_format;
    ops.notify = rig_notify;
    ops.now_micros = rig_now_micros;
    ops.now_millis = rig_now_millis;
    return ops;
}

CaptureHooks make_hooks(Rig *rig) {
    CaptureHooks hooks {};
    hooks.context = rig;
    hooks.initialize_imu = rig_init_imu;
    hooks.read_sample = rig_read_sample;
    hooks.stop_imu = rig_stop_imu;
    hooks.save_sd = rig_save_sd;
    hooks.service_events = rig_service_events;
    hooks.refresh_watchdog = rig_refresh_watchdog;
    return hooks;
}

struct StartPacket {
    uint8_t bytes[start_length];

    StartPacket() {
        memset(bytes, 0, sizeof(bytes));
        bytes[0] = opcode_start;
        bytes[1] = protocol_version;
        for (size_t i = 0; i < run_id_length; ++i) {
            bytes[2 + i] = static_cast<uint8_t>(0xA0 + i);
        }
    }
};

struct Run {
    Rig rig;
    BleCaptureSession session;
    RadioOps radio;
    CaptureHooks hooks;

    Run() : radio(make_radio(&rig)), hooks(make_hooks(&rig)) {}

    StartCheck start() {
        const StartPacket packet;
        return session.request_start(packet.bytes, sizeof(packet.bytes), rig.connected,
                                     rig.subscribed);
    }

    StartCheck arm() {
        session.refresh_readiness(rig.connected, rig.subscribed);
        return start();
    }

    CaptureReport run() { return session.run_capture(hooks, radio); }
};

struct DataPacket {
    uint16_t seq {0};
    uint8_t index {0};
    uint8_t count {0};
    std::string payload;
};

DataPacket decode_data(const std::vector<uint8_t> &packet) {
    DataPacket decoded;
    decoded.seq = static_cast<uint16_t>(packet[1] | (packet[2] << 8));
    decoded.index = packet[3];
    decoded.count = packet[4];
    decoded.payload.assign(packet.begin() + 5, packet.end());
    return decoded;
}

std::string record_payload(const std::vector<std::vector<uint8_t>> &packets, uint16_t seq,
                           int *fragment_count) {
    std::string joined;
    *fragment_count = 0;
    uint8_t expected_index = 0;
    for (const std::vector<uint8_t> &packet : packets) {
        if (packet.empty() || packet[0] != opcode_data) {
            continue;
        }
        const DataPacket decoded = decode_data(packet);
        if (decoded.seq != seq) {
            continue;
        }
        CHECK(decoded.index == expected_index);
        CHECK(decoded.count > 0 && decoded.index < decoded.count);
        ++expected_index;
        *fragment_count = decoded.count;
        joined += decoded.payload;
    }
    return joined;
}

size_t count_opcodes(const std::vector<std::vector<uint8_t>> &packets, uint8_t opcode) {
    size_t count = 0;
    for (const std::vector<uint8_t> &packet : packets) {
        if (!packet.empty() && packet[0] == opcode) {
            ++count;
        }
    }
    return count;
}

std::string make_record(uint16_t seq, size_t length) {
    std::string record;
    for (size_t i = 0; i < length; ++i) {
        record.push_back(static_cast<char>('a' + ((seq + i) % 26)));
    }
    record[length - 1] = '\n';
    return record;
}

// ---------------------------------------------------------------------------

int case_start_validation() {
    Run run;
    const StartPacket packet;

    CHECK(run.session.request_start(packet.bytes, sizeof(packet.bytes), true, true) ==
          StartCheck::RejectedState);
    CHECK(run.session.request_start(packet.bytes, start_length - 1, true, true) ==
          StartCheck::RejectedLength);
    CHECK(run.session.request_start(packet.bytes, start_length + 1, true, true) ==
          StartCheck::RejectedLength);
    CHECK(run.session.request_start(nullptr, 0, true, true) == StartCheck::RejectedLength);

    run.session.refresh_readiness(true, true);
    CHECK(run.session.state() == State::Ready);

    uint8_t bad_opcode[start_length];
    memcpy(bad_opcode, packet.bytes, sizeof(bad_opcode));
    bad_opcode[0] = 0x02;
    CHECK(run.session.request_start(bad_opcode, sizeof(bad_opcode), true, true) ==
          StartCheck::RejectedOpcode);

    uint8_t bad_version[start_length];
    memcpy(bad_version, packet.bytes, sizeof(bad_version));
    bad_version[1] = 0x02;
    CHECK(run.session.request_start(bad_version, sizeof(bad_version), true, true) ==
          StartCheck::RejectedVersion);

    CHECK(run.session.request_start(packet.bytes, sizeof(packet.bytes), false, true) ==
          StartCheck::RejectedLink);
    CHECK(run.session.request_start(packet.bytes, sizeof(packet.bytes), true, false) ==
          StartCheck::RejectedLink);

    CHECK(run.session.request_start(packet.bytes, sizeof(packet.bytes), true, true) ==
          StartCheck::Accepted);
    CHECK(run.session.start_pending());
    CHECK(run.session.request_start(packet.bytes, sizeof(packet.bytes), true, true) ==
          StartCheck::RejectedState);

    // The run id is latched from the accepted START.
    for (size_t i = 0; i < run_id_length; ++i) {
        CHECK_EQ(run.session.run_id()[i], packet.bytes[2 + i]);
    }
    return failures;
}

int case_readiness_gate() {
    Run run;
    run.session.refresh_readiness(false, true);
    CHECK(run.session.state() == State::Idle);
    run.session.refresh_readiness(true, false);
    CHECK(run.session.state() == State::Idle);
    run.session.refresh_readiness(true, true);
    CHECK(run.session.state() == State::Ready);
    run.session.refresh_readiness(false, true);
    CHECK(run.session.state() == State::Idle);
    run.session.refresh_readiness(true, true);
    CHECK(run.session.state() == State::Ready);

    CHECK(run.arm() == StartCheck::Accepted);
    CHECK(run.session.capture_due());
    run.run();
    CHECK(run.session.state() == State::Done);
    CHECK(!run.session.capture_due());
    run.session.refresh_readiness(true, true);
    CHECK(run.session.state() == State::Done);
    CHECK(run.start() == StartCheck::RejectedState);
    return failures;
}

int case_begin_encoding() {
    const uint8_t run_id[run_id_length] = {1, 2, 3, 4, 5, 6, 7, 8};
    uint8_t buffer[32];
    memset(buffer, 0xEE, sizeof(buffer));

    const size_t length = encode_begin(buffer, sizeof(buffer), run_id, 600);
    CHECK_EQ(length, begin_length);
    CHECK_EQ(length, 12);
    CHECK_EQ(buffer[0], opcode_begin);
    CHECK_EQ(buffer[1], protocol_version);
    CHECK(memcmp(buffer + 2, run_id, run_id_length) == 0);
    CHECK_EQ(buffer[10], 0x58);  // 600 = 0x0258, little-endian
    CHECK_EQ(buffer[11], 0x02);

    uint8_t small[begin_length - 1];
    CHECK_EQ(encode_begin(small, sizeof(small), run_id, 600), 0);
    CHECK_EQ(encode_begin(nullptr, 0, run_id, 600), 0);
    return failures;
}

int case_end_encoding() {
    const uint8_t run_id[run_id_length] = {9, 8, 7, 6, 5, 4, 3, 2};
    uint8_t buffer[32];

    const size_t length = encode_end(buffer, sizeof(buffer), run_id, 3, capture_status_failed);
    CHECK_EQ(length, end_length);
    CHECK_EQ(length, 12);
    CHECK_EQ(buffer[0], opcode_end);
    CHECK(memcmp(buffer + 1, run_id, run_id_length) == 0);
    CHECK_EQ(buffer[9], 3);
    CHECK_EQ(buffer[10], 0);
    CHECK_EQ(buffer[11], 1);

    uint8_t small[end_length - 1];
    CHECK_EQ(encode_end(small, sizeof(small), run_id, 3, 0), 0);
    return failures;
}

int case_data_encoding_and_fragmentation() {
    Rig rig;
    rig.record_length = 64;  // 4 x 15 + 4 bytes
    TxScheduler tx;
    tx.reset();
    tx.set_sample_count(1);

    const ServiceResult result = tx.service(make_radio(&rig), tx_service_budget);
    CHECK(result == ServiceResult::Idle);
    CHECK_EQ(rig.packets.size(), fragment_count_for(64));
    CHECK_EQ(rig.packets.size(), 5);

    uint8_t expected_index = 0;
    for (const std::vector<uint8_t> &packet : rig.packets) {
        CHECK(packet.size() <= max_notification_length);
        CHECK_EQ(packet[0], opcode_data);
        const DataPacket decoded = decode_data(packet);
        CHECK_EQ(decoded.seq, 0);
        CHECK_EQ(decoded.index, expected_index);
        CHECK_EQ(decoded.count, 5);
        if (expected_index < 4) {
            CHECK_EQ(decoded.payload.size(), max_fragment_payload);
        } else {
            CHECK_EQ(decoded.payload.size(), 4);
            CHECK_EQ(decoded.payload.back(), '\n');
        }
        ++expected_index;
    }

    int fragments = 0;
    const std::string joined = record_payload(rig.packets, 0, &fragments);
    CHECK_EQ(fragments, 5);
    CHECK(joined == make_record(0, 64));

    CHECK_EQ(rig.formatted.size(), 1);  // the row is formatted once per record
    CHECK(!tx.backlog());
    CHECK_EQ(tx.tx_seq(), 1);
    return failures;
}

int case_record_too_long_no_truncation() {
    Rig rig;
    rig.record_length = 257;  // one byte over the 256-byte buffer
    TxScheduler tx;
    tx.reset();
    tx.set_sample_count(1);

    CHECK(tx.service(make_radio(&rig), tx_service_budget) ==
          ServiceResult::SerializationFailed);
    CHECK(rig.packets.empty());  // a truncated record must never be sent
    CHECK_EQ(tx.tx_seq(), 0);
    CHECK_EQ(tx.tx_fragment(), 0);

    Rig full;
    full.record_length = 256;
    TxScheduler tx_full;
    tx_full.reset();
    tx_full.set_sample_count(1);
    int services = 0;
    ServiceResult result = ServiceResult::BudgetExhausted;
    while (tx_full.backlog() && services < 8) {
        result = tx_full.service(make_radio(&full), tx_service_budget);
        ++services;
    }
    CHECK(result == ServiceResult::Idle);
    CHECK_EQ(full.packets.size(), max_fragments_per_record);
    CHECK_EQ(full.packets.size(), 18);
    for (const std::vector<uint8_t> &packet : full.packets) {
        CHECK(packet.size() <= max_notification_length);
    }
    int fragments = 0;
    const std::string joined = record_payload(full.packets, 0, &fragments);
    CHECK_EQ(fragments, 18);
    CHECK(joined == make_record(0, 256));
    return failures;
}

int case_local_failure_keeps_cursor() {
    Rig rig;
    rig.record_length = 64;
    rig.local_failure_at = 2;
    TxScheduler tx;
    tx.reset();
    tx.set_sample_count(1);

    CHECK(tx.service(make_radio(&rig), tx_service_budget) == ServiceResult::StoppedLocal);
    CHECK_EQ(rig.packets.size(), 2);
    CHECK_EQ(tx.tx_seq(), 0);
    CHECK_EQ(tx.tx_fragment(), 2);

    rig.local_failure_at = -1;
    CHECK(tx.service(make_radio(&rig), tx_service_budget) == ServiceResult::Idle);
    CHECK_EQ(rig.packets.size(), 5);
    CHECK_EQ(tx.tx_seq(), 1);

    // The retried fragments are indices 2, 3, 4 of the same record.
    const DataPacket third = decode_data(rig.packets[2]);
    const DataPacket fourth = decode_data(rig.packets[3]);
    const DataPacket fifth = decode_data(rig.packets[4]);
    CHECK_EQ(third.index, 2);
    CHECK_EQ(fourth.index, 3);
    CHECK_EQ(fifth.index, 4);
    int fragments = 0;
    CHECK(record_payload(rig.packets, 0, &fragments) == make_record(0, 64));
    return failures;
}

int case_budget_limits_service() {
    Rig rig;
    rig.record_length = 256;  // 18 fragments
    TxScheduler tx;
    tx.reset();
    tx.set_sample_count(1);

    CHECK(tx.service(make_radio(&rig), tx_service_budget) ==
          ServiceResult::BudgetExhausted);
    CHECK_EQ(rig.packets.size(), tx_fragments_per_service);
    CHECK_EQ(rig.packets.size(), 6);
    CHECK(tx.service(make_radio(&rig), tx_service_budget) ==
          ServiceResult::BudgetExhausted);
    CHECK_EQ(rig.packets.size(), 12);
    CHECK(tx.service(make_radio(&rig), tx_service_budget) == ServiceResult::Idle);
    CHECK_EQ(rig.packets.size(), 18);

    // The time budget alone can stop a service earlier than the fragment cap.
    Rig slow;
    slow.record_length = 256;
    slow.micros_advance = 2000;
    TxScheduler tx_slow;
    tx_slow.reset();
    tx_slow.set_sample_count(1);
    CHECK(tx_slow.service(make_radio(&slow), tx_service_budget) ==
          ServiceResult::BudgetExhausted);
    CHECK_EQ(slow.packets.size(), 3);
    return failures;
}

int case_tx_never_past_sample_count() {
    Rig rig;
    rig.record_length = 64;
    TxScheduler tx;
    tx.reset();
    tx.set_sample_count(0);
    CHECK(tx.service(make_radio(&rig), tx_service_budget) == ServiceResult::Idle);
    CHECK(rig.packets.empty());

    tx.set_sample_count(2);
    CHECK(tx.service(make_radio(&rig), tx_service_budget) ==
          ServiceResult::BudgetExhausted);
    CHECK(tx.service(make_radio(&rig), tx_service_budget) == ServiceResult::Idle);
    CHECK_EQ(rig.packets.size(), 10);  // 2 records x 5 fragments, never a third
    CHECK_EQ(tx.tx_seq(), 2);

    // sample_count is clamped to the protocol's 600 samples.
    tx.set_sample_count(5000);
    CHECK_EQ(tx.sample_count(), expected_sample_count);
    return failures;
}

int case_init_failure_sends_end_zero() {
    Run run;
    run.rig.init_ok = false;
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, 0);
    CHECK_EQ(report.capture_status, capture_status_failed);
    CHECK(report.end_sent);
    CHECK(!report.radio_terminated);
    CHECK_EQ(run.rig.save_calls, 0);
    CHECK_EQ(run.rig.stop_calls, 1);
    CHECK_EQ(run.rig.packets.size(), 2);
    CHECK_EQ(run.rig.packets[0][0], opcode_begin);
    CHECK_EQ(run.rig.packets[1][0], opcode_end);
    CHECK(memcmp(run.rig.packets[1].data() + 1, run.session.run_id(), run_id_length) == 0);
    CHECK_EQ(run.rig.packets[1][9], 0);
    CHECK_EQ(run.rig.packets[1][10], 0);
    CHECK_EQ(run.rig.packets[1][11], capture_status_failed);
    CHECK(run.session.state() == State::Done);
    return failures;
}

int case_init_failure_without_link() {
    Run run;
    run.rig.init_ok = false;
    CHECK(run.arm() == StartCheck::Accepted);  // START was accepted while connected
    run.rig.connected = false;                 // the link drops before the run

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, 0);
    CHECK_EQ(report.capture_status, capture_status_failed);
    CHECK(!report.end_sent);
    CHECK(!report.radio_terminated);  // no radio session existed at START
    CHECK(run.rig.packets.empty());
    CHECK_EQ(run.rig.stop_calls, 1);
    return failures;
}

int case_partial_capture_sends_end_n() {
    Run run;
    run.rig.record_length = 20;  // 2 fragments
    run.rig.samples_ok = 3;
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, 3);
    CHECK_EQ(report.capture_status, capture_status_failed);
    CHECK(report.sd_saved);
    CHECK(report.end_sent);
    CHECK(!report.radio_terminated);
    CHECK_EQ(run.rig.saved_count, 3);
    CHECK_EQ(run.rig.read_calls, 4);
    CHECK_EQ(run.rig.packets.size(), 1 + 3 * 2 + 1);

    const std::vector<uint8_t> &end = run.rig.packets.back();
    CHECK_EQ(end[0], opcode_end);
    CHECK_EQ(end[9], 3);
    CHECK_EQ(end[10], 0);
    CHECK_EQ(end[11], capture_status_failed);
    CHECK_EQ(count_opcodes(run.rig.packets, opcode_data), 6);
    return failures;
}

int case_terminal_failure_no_end_capture_continues() {
    Run run;
    run.rig.record_length = 20;
    run.rig.terminal_failure_at = 5;  // after BEGIN + 4 DATA notifications
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK_EQ(report.capture_status, capture_status_ok);
    CHECK(report.sd_saved);
    CHECK(!report.end_sent);
    CHECK(report.radio_terminated);
    CHECK_EQ(run.rig.save_calls, 1);
    CHECK_EQ(run.rig.saved_count, expected_sample_count);
    CHECK_EQ(run.rig.packets.size(), 5);
    CHECK_EQ(count_opcodes(run.rig.packets, opcode_end), 0);
    CHECK_EQ(run.rig.notify_after_terminal, 0);  // no sends after the radio died
    return failures;
}

int case_serialization_failure_no_end_capture_continues() {
    Run run;
    run.rig.record_length = 20;
    run.rig.format_fail_seq = 2;
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK(report.sd_saved);
    CHECK(!report.end_sent);
    CHECK(report.radio_terminated);
    CHECK_EQ(run.rig.packets.size(), 5);  // BEGIN + records 0 and 1 only
    CHECK_EQ(count_opcodes(run.rig.packets, opcode_end), 0);
    for (const std::vector<uint8_t> &packet : run.rig.packets) {
        CHECK(packet.size() <= max_notification_length);
    }
    return failures;
}

int case_begin_failure_disables_radio() {
    Run run;
    run.rig.record_length = 20;
    run.rig.terminal_failure_at = 0;  // the BEGIN notification itself fails
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK(report.sd_saved);
    CHECK(!report.end_sent);
    CHECK(report.radio_terminated);
    CHECK(run.rig.packets.empty());
    return failures;
}

int case_end_failure_is_terminal() {
    Run run;
    run.rig.record_length = 20;
    CHECK(run.arm() == StartCheck::Accepted);
    // BEGIN + 600 records x 2 fragments = 1201 sent notifications.
    run.rig.local_failure_at = 1 + expected_sample_count * 2;

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK_EQ(report.capture_status, capture_status_ok);
    CHECK(report.sd_saved);
    CHECK(!report.end_sent);
    CHECK(report.radio_terminated);
    CHECK_EQ(count_opcodes(run.rig.packets, opcode_end), 0);
    CHECK_EQ(run.rig.packets.back()[0], opcode_data);
    return failures;
}

int case_drain_completes_and_sends_end() {
    Run run;
    run.rig.record_length = 256;  // 18 fragments per record, more than one budget
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK_EQ(report.capture_status, capture_status_ok);
    CHECK(report.end_sent);
    CHECK(!report.radio_terminated);
    CHECK(run.rig.service_calls > 0);
    CHECK_EQ(run.rig.packets.size(),
             1 + static_cast<size_t>(expected_sample_count) * max_fragments_per_record + 1);
    CHECK_EQ(run.rig.packets.back()[0], opcode_end);
    CHECK_EQ(run.rig.packets.back()[9], expected_sample_count & 0xFF);
    CHECK_EQ(run.rig.packets.back()[10], expected_sample_count >> 8);
    CHECK_EQ(run.rig.packets.back()[11], capture_status_ok);
    return failures;
}

int case_drain_deadline() {
    Run run;
    run.rig.record_length = 20;
    run.rig.local_failure_at = 10;   // notifications never succeed again
    run.rig.millis_advance = 4000;   // injected clock for the drain deadline
    CHECK(run.arm() == StartCheck::Accepted);

    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, expected_sample_count);
    CHECK(report.sd_saved);
    CHECK(!report.end_sent);
    CHECK(report.radio_terminated);
    CHECK(run.rig.service_calls > 0);
    CHECK_EQ(count_opcodes(run.rig.packets, opcode_end), 0);
    CHECK(run.rig.millis_now >= drain_budget_millis);
    return failures;
}

int case_run_without_start_is_refused() {
    Run run;
    const CaptureReport report = run.run();
    CHECK_EQ(report.sample_count, 0);
    CHECK_EQ(report.capture_status, capture_status_failed);
    CHECK(!report.end_sent);
    CHECK_EQ(run.rig.init_calls, 0);
    CHECK_EQ(run.rig.save_calls, 0);
    return failures;
}

struct NamedCase {
    const char *name;
    int (*run)();
};

const NamedCase cases[] = {
    {"start_validation", case_start_validation},
    {"readiness_gate", case_readiness_gate},
    {"begin_encoding", case_begin_encoding},
    {"end_encoding", case_end_encoding},
    {"data_encoding_and_fragmentation", case_data_encoding_and_fragmentation},
    {"record_too_long_no_truncation", case_record_too_long_no_truncation},
    {"local_failure_keeps_cursor", case_local_failure_keeps_cursor},
    {"budget_limits_service", case_budget_limits_service},
    {"tx_never_past_sample_count", case_tx_never_past_sample_count},
    {"init_failure_sends_end_zero", case_init_failure_sends_end_zero},
    {"init_failure_without_link", case_init_failure_without_link},
    {"partial_capture_sends_end_n", case_partial_capture_sends_end_n},
    {"terminal_failure_no_end_capture_continues",
     case_terminal_failure_no_end_capture_continues},
    {"serialization_failure_no_end_capture_continues",
     case_serialization_failure_no_end_capture_continues},
    {"begin_failure_disables_radio", case_begin_failure_disables_radio},
    {"end_failure_is_terminal", case_end_failure_is_terminal},
    {"drain_completes_and_sends_end", case_drain_completes_and_sends_end},
    {"drain_deadline", case_drain_deadline},
    {"run_without_start_is_refused", case_run_without_start_is_refused},
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
