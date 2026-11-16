#include "sd_export.h"

#include <string.h>

// SdFat defaults its begin() chip select to SS, which the mbed bridge does not define.
#include "board_control.h"  // SD_CS_PIN
#ifndef SS
#define SS SD_CS_PIN
#endif
#include "SdFat.h"
#include "crc32.h"
#include "sd_manager.h"
#include "watchdog_manager.h"

namespace {

// 64 source bytes per DATA line: ~130 bytes on the wire, so even a 600-sample
// IMU CSV (about 54 kB) transfers in a couple of seconds at 1 Mbps while every
// line stays short enough for the host to parse and log.
constexpr size_t chunk_size {64};

uint8_t chunk[chunk_size];
char hex_buffer[2 * chunk_size];
constexpr char hex_digits[] = "0123456789ABCDEF";

void print_hex32(uint32_t value) {
    char out[8];
    for (int i = 7; i >= 0; --i) {
        out[i] = hex_digits[value & 0x0Fu];
        value >>= 4;
    }
    Serial.write(reinterpret_cast<const uint8_t *>(out), sizeof(out));
}

void print_error(const __FlashStringHelper *code, const char *name) {
    Serial.print(F("SDX-ERR "));
    Serial.print(code);
    if (name != nullptr && name[0] != '\0') {
        Serial.print(' ');
        Serial.print(name);
    }
    Serial.println();
    Serial.flush();
}

char lower(char c) {
    return (c >= 'A' && c <= 'Z') ? static_cast<char>(c + ('a' - 'A')) : c;
}

bool equals_ci(const char *a, const char *b) {
    while (*a != '\0' && *b != '\0') {
        if (lower(*a) != lower(*b)) {
            return false;
        }
        ++a;
        ++b;
    }
    return *a == '\0' && *b == '\0';
}

bool starts_with_ci(const char *text, const char *prefix) {
    while (*prefix != '\0') {
        if (lower(*text) != lower(*prefix) || *text == '\0') {
            return false;
        }
        ++text;
        ++prefix;
    }
    return true;
}

}  // namespace

SDExporter board_sd_export;

void SDExporter::discard_input(void) {
    while (Serial.available() > 0) {
        Serial.read();
    }
    line_length_ = 0;
}

void SDExporter::print_ready(void) {
    discard_input();
    Serial.print(F("SDX-READY sd="));
    Serial.println(board_sd_manager.is_ready() ? 1 : 0);
    Serial.println(F("export: LIST | GET <name> (read-only; SD not modified)"));
    Serial.flush();
}

void SDExporter::poll(void) {
    while (Serial.available() > 0) {
        const int c = Serial.read();
        if (c < 0) {
            break;
        }
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            if (line_length_ > 0) {
                line_[line_length_] = '\0';
                handle_line(line_);
                line_length_ = 0;
            }
            continue;
        }
        if (line_length_ + 1 < line_max) {
            line_[line_length_++] = static_cast<char>(c);
        } else {
            line_length_ = 0;  // overlong input, drop it
        }
    }
}

void SDExporter::handle_line(char *line) {
    while (*line == ' ') {
        ++line;
    }
    size_t length = strlen(line);
    while (length > 0 && line[length - 1] == ' ') {
        line[--length] = '\0';
    }
    if (length == 0) {
        return;
    }

    if (equals_ci(line, "LIST") || equals_ci(line, "LS")) {
        handle_list();
        return;
    }
    if (starts_with_ci(line, "GET ")) {
        const char *name = line + 4;
        while (*name == ' ') {
            ++name;
        }
        handle_get(name);
        return;
    }
    if (equals_ci(line, "PING")) {
        Serial.println(F("SDX-PONG"));
        Serial.flush();
        return;
    }
    print_error(F("UNKNOWN"), line);
}

void SDExporter::handle_list(void) {
    SdFat32 *volume = board_sd_manager.volume();
    if (volume == nullptr) {
        print_error(F("NO-SD"), nullptr);
        return;
    }

    File32 directory;
    if (!directory.open("/")) {
        print_error(F("OPEN-ROOT"), nullptr);
        return;
    }

    Serial.println(F("SDX-LIST-BEGIN"));
    uint32_t count = 0;
    File32 file;
    while (file.openNext(&directory, O_RDONLY)) {
        if (file.isFile()) {
            char name[13];
            if (file.getName(name, sizeof(name)) > 0) {
                Serial.print(F("SDX-FILE "));
                Serial.print(name);
                Serial.print(' ');
                Serial.println(static_cast<uint32_t>(file.fileSize()));
                ++count;
            }
        }
        file.close();
        wdt.restart();
    }
    directory.close();
    Serial.print(F("SDX-LIST-END "));
    Serial.println(count);
    Serial.flush();
}

void SDExporter::handle_get(const char *name) {
    if (!valid_name(name)) {
        print_error(F("BAD-NAME"), name);
        return;
    }
    SdFat32 *volume = board_sd_manager.volume();
    if (volume == nullptr) {
        print_error(F("NO-SD"), name);
        return;
    }

    File32 file;
    if (!file.open(name, O_RDONLY)) {
        print_error(volume->exists(name) ? F("OPEN") : F("NOT-FOUND"), name);
        return;
    }
    if (file.isDir() || file.isSubDir()) {
        file.close();
        print_error(F("NOT-FILE"), name);
        return;
    }

    const uint32_t size = static_cast<uint32_t>(file.fileSize());
    Serial.print(F("SDX-BEGIN "));
    Serial.print(name);
    Serial.print(' ');
    Serial.println(size);

    uint32_t offset = 0;
    uint32_t crc = crc32_initial_value;
    while (offset < size) {
        size_t wanted = size - offset;
        if (wanted > chunk_size) {
            wanted = chunk_size;
        }
        const int received = file.read(chunk, wanted);
        if (received <= 0) {
            Serial.print(F("SDX-ERR READ "));
            Serial.print(name);
            Serial.print(' ');
            Serial.println(offset);
            file.close();
            Serial.flush();
            return;
        }

        crc = crc32_update(crc, chunk, static_cast<size_t>(received));
        for (int i = 0; i < received; ++i) {
            hex_buffer[2 * i] = hex_digits[chunk[i] >> 4];
            hex_buffer[2 * i + 1] = hex_digits[chunk[i] & 0x0Fu];
        }
        Serial.print(F("SDX-DATA "));
        Serial.write(reinterpret_cast<const uint8_t *>(hex_buffer),
                     2 * static_cast<size_t>(received));
        Serial.println();

        offset += static_cast<uint32_t>(received);
        wdt.restart();
    }
    file.close();

    Serial.print(F("SDX-END "));
    Serial.print(size);
    Serial.print(' ');
    print_hex32(crc32_final(crc));
    Serial.println();
    Serial.flush();
}

bool SDExporter::valid_name(const char *name) const {
    if (name == nullptr) {
        return false;
    }
    size_t length = strlen(name);
    if (length == 0 || length > 12) {  // FAT 8.3 name, without LFN support
        return false;
    }
    for (size_t i = 0; i < length; ++i) {
        const char c = name[i];
        const bool alphanumeric = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                                  (c >= '0' && c <= '9');
        if (!alphanumeric && c != '_' && c != '-' && c != '.' && c != '~') {
            return false;
        }
    }
    return true;
}