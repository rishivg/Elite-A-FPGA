#pragma once
#include <VVia6522.h>
#include <array>
#include <charconv>
#include <cstdint>
#include <ranges>
#include <sstream>

#ifndef BBC_VIA_MASTER_TICKS
#define BBC_VIA_MASTER_TICKS 1
#endif
#ifndef BBC_VIA_CPU_ENDPOINT
#define BBC_VIA_CPU_ENDPOINT 0
#endif

// Electrical bus adapter for the unchanged external model's MOSIO driver.
// It decodes bus phases into accepted RTL events; all serial data, flags and
// output pins come from the DUT. No reference device state is restored.
class Vvia_core {
    VVia6522 via;
    bool previousClk = false, previousPhi = false;
    bool selected = false, reading = true, writeLatch = false;
    unsigned address = 0, data = 0, ddra = 0, ddrb = 0;

  public:
    uint8_t clk = 0, rst = 0, ifr = 0;
    uint64_t bus_i = 0, bus_o = 0;
    Vvia_core() {
        via.io_tick = via.io_phi2 = via.io_read = via.io_write = via.io_address = via.io_data = 0;
        via.io_ca1 = via.io_ca2 = via.io_cb1 = via.io_cb2 = 0;
        via.io_ca2AfterWrite = via.io_cb1Pulse = 0;
        via.io_portAIn = via.io_portBIn = 255;
        via.reset = 1;
        via.eval();
        for (unsigned edge = 0; edge < BBC_VIA_MASTER_TICKS; ++edge) {
            via.clk = 0;
            via.eval();
            via.clk = 1;
            via.eval();
        }
        via.clk = 0;
        via.eval();
        via.reset = 0;
    }
    auto contextp() { return via.contextp(); }
    void final() { via.final(); }
    void eval() {
        const bool phi = (bus_i >> 36) & 1;
        const bool rising = clk && !previousClk;
        // The unchanged driver samples IRQ before PHI2 falls. The accepted
        // one-MHz endpoint is the final high-phase host edge, before that
        // sample; register writes follow in the first low-phase host edge.
        const bool tick = phi && previousPhi;
        const bool write = BBC_VIA_CPU_ENDPOINT ? phi && previousPhi && selected && !reading
                                                : previousPhi && !phi && writeLatch;
        const bool read = phi && previousPhi && selected && reading;
        via.reset = rst || !((bus_i >> 35) & 1);
        via.io_tick = tick;
        via.io_phi2 = phi;
        via.io_write = write;
        via.io_read = read;
        via.io_address = write ? address : ((bus_i >> 28) & 15);
        via.io_data = write ? data : ((bus_i >> 20) & 255);
        via.io_portAIn = (bus_i >> 4) & 255;
        via.io_portBIn = (bus_i >> 12) & 255;
        via.io_ca1 = bus_i & 1;
        via.io_ca2 = (bus_i >> 1) & 1;
        via.io_cb1 = (bus_i >> 2) & 1;
        via.io_cb2 = (bus_i >> 3) & 1;
        // Like the CPU, sample the register bus before the accepted edge.
        // SR may shift on that edge; returning its updated latch would make
        // a coincident read observe the next bit rather than the sampled bus.
        unsigned sampledRead = 0;
        if (rising) {
            // One MOSIO host period is 250 ns. Dense profiles execute every
            // master edge. The final high-phase edge accepts tick/read;
            // a retained write accepts at PHI1 entry, before low-phase
            // latches settle. PHI2 and pins hold throughout the host period.
            for (unsigned edge = 0; edge < BBC_VIA_MASTER_TICKS; ++edge) {
                const bool accepted = edge + 1 == BBC_VIA_MASTER_TICKS;
                via.io_tick = accepted && tick;
                via.io_read = accepted && read;
                via.io_write = (BBC_VIA_CPU_ENDPOINT ? accepted : edge == 0) && write;
                via.clk = 0;
                via.eval();
                if (accepted)
                    sampledRead = via.io_result;
                via.clk = 1;
                via.eval();
            }
        } else {
            via.clk = clk;
            via.eval();
        }
        if (rising) {
            if (via.reset)
                ddra = ddrb = 0;
            if (via.reset)
                writeLatch = false;
            else if (write && address == 3)
                ddra = data;
            else if (write && address == 2)
                ddrb = data;
            previousPhi = phi;
            if (phi) {
                selected = ((bus_i >> 34) & 1) && !((bus_i >> 33) & 1);
                reading = (bus_i >> 32) & 1;
                address = (bus_i >> 28) & 15;
                data = (bus_i >> 20) & 255;
                // The NMOS write latch persists while CS remains active,
                // including a following read. Decode the bus, not CPU intent.
                if (!selected)
                    writeLatch = false;
                else if (!reading)
                    writeLatch = true;
            }
            via.io_tick = via.io_write = via.io_read = 0;
            via.eval();
        }
        previousClk = clk;
        const unsigned result = rising && read ? sampledRead : via.io_result;
        via.io_address = 13;
        via.eval();
        ifr = via.io_result;
        via.io_address = write ? address : ((bus_i >> 28) & 15);
        via.eval();
        bus_o = (uint64_t(result) << 38) | (uint64_t(via.io_portB) << 30) |
                (uint64_t(via.io_portA) << 22) | (uint64_t(ddrb) << 14) | (uint64_t(ddra) << 6) |
                (uint64_t(via.io_cb2Pin) << 5) | (uint64_t(via.io_cb1Pin) << 4) |
                (uint64_t(via.io_ca2Pin) << 3) | (uint64_t(via.io_cb1Output) << 1) |
                (uint64_t(via.io_cb2Output) << 2) | uint64_t(!via.io_irq);
    }
};
