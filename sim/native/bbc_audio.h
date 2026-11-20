#pragma once
#include <cstdint>

// 32 MHz master clocks, 500 kHz observations. Sample the completed interval
// after its last rising edge: sample zero describes clocks 0 through 63.
constexpr bool bbc_audio_interval_complete(uint64_t cycle) { return (cycle & 63) == 63; }
