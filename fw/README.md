# Demo firmware

`make fw` builds the PicoRV32 C firmware with `riscv64-unknown-elf-gcc` for
RV32IM/ILP32. Override the prefix with `make -C fw RISCV=<prefix>`. ELF,
disassembly, binary and little-endian 32-bit hex images go to `build/fw/`.

`main.c` contains the game loop and boot self-tests. `mmio.h` defines renderer,
input, sound and persistence registers. `crt0.S` initializes the stack and BSS,
and `link.ld` defines their memory layout.

[Demo validation](../docs/elite-a-validation.md) covers the native SoC checks
and original-byte kernels.
