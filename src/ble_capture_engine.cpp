#include "ble_capture_engine.h"

#include <string.h>

namespace ble_capture {

namespace {

void put_u16(uint8_t *out, uint16_t value) {
    out[0] = static_cast<uint8_t>(value & 0xFFu);
    out[1] = static_cast<uint8_t>((value >> 8) & 0xFFu);
}

bool link_valid(const RadioOps &radio) {
    return radio.is_connected(radio.context) && radio.is_subscribed(radio.context);
}

}  // namespace

size_t fragment_count_for(size_t record_length) {
    if (record_length == 0 || record_length > record_buffer_length) {
        return 0;
    }
    const size_t count = (record_length + max_fragment_payload - 1) / max_fragment_payload;
    return count <= max_fragments_per_record ? count : 0;
}

StartCheck check_start(const uint8_t *payload, size_t length, bool connected,
                       bool subscribed, State state) {
    if (payload == nullptr || length != start_length) {
        return StartCheck::RejectedLength;
    }
    if (payload[0] != opcode_start) {
        return StartCheck::RejectedOpcode;
    }
    if (payload[1] != protocol_version) {
        return StartCheck::RejectedVersion;
    }
    if (!connected || !subscribed) {
        return StartCheck::RejectedLink;
    }
    if (state != State::Ready) {
        return StartCheck::RejectedState;
    }
    return StartCheck::Accepted;
}

size_t encode_begin(uint8_t *out, size_t capacity, const uint8_t run_id[run_id_length],
                    uint16_t expected_count) {
    if (out == nullptr || run_id == nullptr || capacity < begin_length) {
        return 0;
    }
    out[0] = opcode_begin;
    out[1] = protocol_version;
    memcpy(out + 2, run_id, run_id_length);
    put_u16(out + 2 + run_id_length, expected_count);
    return begin_length;
}

size_t encode_data(uint8_t *out, size_t capacity, uint16_t seq, uint8_t fragment_index,
                   uint8_t fragment_count, const char *payload, size_t payload_length) {
    if (out == nullptr || payload == nullptr || payload_length == 0 ||
        payload_length > max_fragment_payload || fragment_count == 0 ||
        fragment_index >= fragment_count || capacity < data_header_length + payload_length) {
        return 0;
    }
    out[0] = opcode_data;
    put_u16(out + 1, seq);
    out[3] = fragment_index;
    out[4] = fragment_count;
    memcpy(out + data_header_length, payload, payload_length);
    return data_header_length + payload_length;
}

size_t encode_end(uint8_t *out, size_t capacity, const uint8_t run_id[run_id_length],
                  uint16_t actual_count, uint8_t capture_status) {
    if (out == nullptr || run_id == nullptr || capacity < end_length) {
        return 0;
    }
    out[0] = opcode_end;
    memcpy(out + 1, run_id, run_id_length);
    put_u16(out + 1 + run_id_length, actual_count);
    out[end_length - 1] = capture_status;
    return end_length;
}

void TxScheduler::reset() {
    sample_count_ = 0;
    tx_seq_ = 0;
    tx_fragment_ = 0;
    fragment_count_ = 0;
    record_seq_ = no_record;
    record_length_ = 0;
}

void TxScheduler::set_sample_count(uint16_t count) {
    sample_count_ = count > expected_sample_count ? expected_sample_count : count;
}

ServiceResult TxScheduler::service(const RadioOps &radio, const TxBudget &budget) {
    const uint32_t started_micros = radio.now_micros(radio.context);
    size_t attempts = 0;
    while (attempts < budget.max_fragments) {
        if (static_cast<uint32_t>(radio.now_micros(radio.context) - started_micros) >=
            budget.max_micros) {
            return ServiceResult::BudgetExhausted;
        }
        if (tx_seq_ >= sample_count_) {
            return ServiceResult::Idle;
        }

        // Format a record once; every fragment uses the same CSV bytes.
        if (record_seq_ != tx_seq_) {
            const size_t length =
                radio.format_record(radio.context, tx_seq_, record_, sizeof(record_));
            if (length == 0 || length > sizeof(record_)) {
                return ServiceResult::SerializationFailed;
            }
            const size_t fragments = fragment_count_for(length);
            if (fragments == 0) {
                return ServiceResult::SerializationFailed;
            }
            record_seq_ = tx_seq_;
            record_length_ = length;
            fragment_count_ = static_cast<uint8_t>(fragments);
            tx_fragment_ = 0;
        }

        const size_t offset = static_cast<size_t>(tx_fragment_) * max_fragment_payload;
        size_t payload_length = record_length_ - offset;
        if (payload_length > max_fragment_payload) {
            payload_length = max_fragment_payload;
        }

        uint8_t packet[max_notification_length];
        const size_t packet_length =
            encode_data(packet, sizeof(packet), tx_seq_, tx_fragment_, fragment_count_,
                        record_ + offset, payload_length);
        if (packet_length == 0) {
            return ServiceResult::SerializationFailed;
        }

        const NotifyOutcome outcome = radio.notify(radio.context, packet, packet_length);
        if (outcome == NotifyOutcome::TerminalFailure) {
            return ServiceResult::StoppedTerminal;
        }
        if (outcome == NotifyOutcome::LocalFailure) {
            // Keep the cursor on this fragment; retry when there is budget again.
            return ServiceResult::StoppedLocal;
        }

        ++attempts;
        ++tx_fragment_;
        if (tx_fragment_ >= fragment_count_) {
            tx_fragment_ = 0;
            fragment_count_ = 0;
            record_length_ = 0;
            record_seq_ = no_record;
            ++tx_seq_;
        }
    }
    return tx_seq_ < sample_count_ ? ServiceResult::BudgetExhausted : ServiceResult::Idle;
}

BleCaptureSession::BleCaptureSession() {
    reset();
}

void BleCaptureSession::reset() {
    state_ = State::Idle;
    memset(run_id_, 0, sizeof(run_id_));
    start_pending_ = false;
    sample_count_ = 0;
    tx_.reset();
}

StartCheck BleCaptureSession::request_start(const uint8_t *payload, size_t length,
                                            bool connected, bool subscribed) {
    const StartCheck check = check_start(payload, length, connected, subscribed, state_);
    if (check != StartCheck::Accepted || start_pending_) {
        return check == StartCheck::Accepted ? StartCheck::RejectedState : check;
    }
    memcpy(run_id_, payload + 2, run_id_length);
    start_pending_ = true;
    return StartCheck::Accepted;
}

void BleCaptureSession::refresh_readiness(bool connected, bool subscribed) {
    if (state_ == State::Idle && connected && subscribed) {
        state_ = State::Ready;
    } else if (state_ == State::Ready && (!connected || !subscribed)) {
        state_ = State::Idle;
    }
}

bool BleCaptureSession::submit_end(const RadioOps &radio, uint16_t actual_count,
                                   uint8_t status) {
    uint8_t message[end_length];
    const size_t length = encode_end(message, sizeof(message), run_id_, actual_count, status);
    if (length == 0) {
        return false;
    }
    return radio.notify(radio.context, message, length) == NotifyOutcome::Sent;
}

CaptureReport BleCaptureSession::run_capture(const CaptureHooks &hooks,
                                             const RadioOps &radio) {
    CaptureReport report;
    report.capture_status = capture_status_failed;
    if (state_ != State::Ready || !start_pending_) {
        return report;  // only an accepted START may begin a run
    }

    state_ = State::Capturing;
    start_pending_ = false;
    sample_count_ = 0;
    tx_.reset();
    tx_.set_sample_count(0);

    const bool radio_started = link_valid(radio);
    bool radio_active = radio_started;

    if (radio_active) {
        uint8_t message[begin_length];
        const size_t length =
            encode_begin(message, sizeof(message), run_id_, expected_sample_count);
        if (length == 0 ||
            radio.notify(radio.context, message, length) != NotifyOutcome::Sent) {
            radio_active = false;
        }
    }

    // IMU init + warm-up is the capture preparation phase; it has its own
    // bounded retry policy inside start_IMU_bounded().
    if (!hooks.initialize_imu(hooks.context)) {
        hooks.stop_imu(hooks.context);
        hooks.refresh_watchdog(hooks.context);
        report.sample_count = 0;
        report.capture_status = capture_status_failed;
        if (radio_active && link_valid(radio)) {
            report.end_sent = submit_end(radio, 0, capture_status_failed);
        }
        if (!report.end_sent) {
            radio_active = false;
        }
        report.radio_terminated = radio_started && !report.end_sent;
        state_ = State::Done;
        return report;
    }

    if (radio_active && !link_valid(radio)) {
        radio_active = false;
    }

    report.capture_status = capture_status_ok;
    for (uint16_t index = 0; index < expected_sample_count; ++index) {
        if (radio_active && !link_valid(radio)) {
            // Disconnect/unsubscribe is terminal for this run: capture and SD
            // continue, but this run is never resumed on a later connection.
            radio_active = false;
        }
        if (!hooks.read_sample(hooks.context, index)) {
            report.capture_status = capture_status_failed;
            break;
        }
        sample_count_ = static_cast<uint16_t>(index + 1);
        tx_.set_sample_count(sample_count_);

        if (radio_active) {
            const ServiceResult result = tx_.service(radio, tx_service_budget);
            if (result == ServiceResult::StoppedTerminal ||
                result == ServiceResult::SerializationFailed) {
                radio_active = false;
            }
        }
        hooks.refresh_watchdog(hooks.context);
    }

    hooks.stop_imu(hooks.context);
    hooks.refresh_watchdog(hooks.context);
    report.sd_saved = hooks.save_sd(hooks.context, sample_count_);
    report.sample_count = sample_count_;
    hooks.refresh_watchdog(hooks.context);

    if (radio_active) {
        state_ = State::Draining;
        DrainWindow window;
        window.start(radio.now_millis(radio.context));
        while (tx_.backlog() && !window.expired(radio.now_millis(radio.context))) {
            if (!link_valid(radio)) {
                radio_active = false;
                break;
            }
            hooks.service_events(hooks.context);
            const ServiceResult result = tx_.service(radio, tx_service_budget);
            if (result == ServiceResult::StoppedTerminal ||
                result == ServiceResult::SerializationFailed) {
                radio_active = false;
                break;
            }
            hooks.refresh_watchdog(hooks.context);
        }

        if (radio_active) {
            if (!tx_.backlog()) {
                report.end_sent = submit_end(radio, sample_count_, report.capture_status);
            }
            if (!report.end_sent) {
                radio_active = false;
            }
        }
    }

    report.radio_terminated = radio_started && !report.end_sent;
    state_ = State::Done;
    return report;
}

}  // namespace ble_capture
