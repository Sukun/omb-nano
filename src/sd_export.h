#ifndef SD_EXPORT_H
#define SD_EXPORT_H

#include "Arduino.h"

// Read-only SD card export over the debug serial port (USB / CH340, 1 Mbps).
//
// Host -> board, one ASCII command per line:
//   LIST              list the files in the card root
//   GET <name>        stream one file
//
// Board -> host:
//   SDX-READY sd=0|1                      start-up done, export line is listening
//   SDX-LIST-BEGIN
//   SDX-FILE <name> <size>
//   SDX-LIST-END <count>
//   SDX-BEGIN <name> <size>
//   SDX-DATA <hex>                        up to 64 source bytes per line
//   SDX-END <size> <crc32>
//   SDX-ERR <code> [<name>]               NO-SD, NOT-FOUND, NOT-FILE, OPEN, READ,
//                                         BAD-NAME, OPEN-ROOT, UNKNOWN
//
// The exporter only reads the card: it never creates, renames, deletes or
// overwrites files. Bytes are hex encoded so any file content survives the
// text link, and the CRC32 in SDX-END lets the host verify completeness.
class SDExporter {
  public:
    // Non-blocking: consumes only bytes already waiting in the serial input
    // buffer and answers at most one complete command line per call.
    void poll(void);

    // Announce that the export line is listening. Also drops any partial host
    // input, so commands typed during the boot capture are not answered late.
    void print_ready(void);

    void discard_input(void);

  private:
    void handle_line(char *line);
    void handle_list(void);
    void handle_get(const char *name);
    bool valid_name(const char *name) const;

    static constexpr size_t line_max {40};
    char line_[line_max] {};
    size_t line_length_ {0};
};

extern SDExporter board_sd_export;

#endif