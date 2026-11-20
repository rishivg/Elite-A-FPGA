// One compiled RTL model serves every independent CPU qualification mode.
#include <iostream>
#include <stdexcept>
#include <string>
int cpu_functional_test(int, char **);
int cpu_timing_test(int, char **);
int cpu_interrupt_test();
int cpu_reset_test(int, char **);
int cpu_bus_test(int, char **);
int cpu_interrupt_bus_test(int, char **);
int cpu_official_bus_test(int, char **);
int main(int argc, char **argv) {
    try {
        if (argc < 2)
            throw std::runtime_error(
                "Expected functional, timing, bus, official-bus, interrupt or reset mode");
        const std::string mode = argv[1];
        if (mode == "functional")
            return cpu_functional_test(argc - 1, argv + 1);
        if (mode == "timing")
            return cpu_timing_test(argc - 1, argv + 1);
        if (mode == "official-bus")
            return cpu_official_bus_test(argc - 1, argv + 1);
        if (mode == "interrupt-bus")
            return cpu_interrupt_bus_test(argc - 1, argv + 1);
        if (mode == "bus")
            return cpu_bus_test(argc - 1, argv + 1);
        if (mode == "reset")
            return cpu_reset_test(argc - 1, argv + 1);
        if (argc != 2)
            throw std::runtime_error("Unexpected qualification arguments");
        if (mode == "interrupt")
            return cpu_interrupt_test();
        throw std::runtime_error("Unknown CPU qualification mode");
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
