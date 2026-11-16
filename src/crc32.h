#ifndef CRC32_H
#define CRC32_H

#include <stddef.h>
#include <stdint.h>

// CRC-32 (IEEE 802.3, reflected, polynomial 0xEDB88320), the same value that
// Python's binascii.crc32 returns. The serial SD export sends it after every
// file so the host can prove it received exactly the bytes stored on the card.
constexpr uint32_t crc32_initial_value {0xFFFFFFFFu};

inline uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t length) {
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return crc;
}

inline uint32_t crc32_final(uint32_t crc) {
    return ~crc;
}

#endif