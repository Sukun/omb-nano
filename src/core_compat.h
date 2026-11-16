#ifndef CORE_COMPAT_H
#define CORE_COMPAT_H

// Force-included for every C++ translation unit in the Apollo3 2.2.1 build.
// The SFE_ARTEMIS_MODULE variant defines no default SPI interface
// (VARIANT_SPI_INTFCS == 0), but Adafruit and SparkFun u-blox reference the
// global `SPI` object in default arguments and SPI init overloads. Provide an
// inert stand-in so those libraries compile and link; it is never started or
// used (this board's SD card is driven over software SPI).
// The build force-includes this platform's SPI.h immediately before this file
// (core sources are compiled without the library include paths), so SPIClass is
// available here in every translation unit.
#if VARIANT_SPI_INTFCS == 0
namespace core_compat {

class PlaceholderSpi : public arduino::SPIClass {
  public:
    uint8_t transfer(uint8_t) override { return 0; }
    uint16_t transfer16(uint16_t) override { return 0; }
    void transfer(void *, size_t) override {}
    void usingInterrupt(int) override {}
    void notUsingInterrupt(int) override {}
    void beginTransaction(arduino::SPISettings) override {}
    void endTransaction(void) override {}
    void attachInterrupt() override {}
    void detachInterrupt() override {}
    void begin() override {}
    void end() override {}
};

}  // namespace core_compat

extern core_compat::PlaceholderSpi SPI;
#endif

#endif
