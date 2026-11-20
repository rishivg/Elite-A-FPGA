# Firmware build: RV32IM bare-metal -> hex image for the SoC RAM.
RISCV   ?= riscv64-unknown-elf
CC      := $(RISCV)-gcc
OBJCOPY := $(RISCV)-objcopy
OBJDUMP := $(RISCV)-objdump

CFLAGS := -march=rv32im -mabi=ilp32 -nostdlib -nostartfiles -ffreestanding \
          -mno-relax -Os -Wall -Wextra -ffunction-sections -Wl,--gc-sections

OUT := ../build/fw
SRC := crt0.S main.c

.PHONY: all clean
all: $(OUT)/firmware.hex

$(OUT)/firmware.elf: $(SRC) mmio.h link.ld
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -T link.ld $(SRC) -o $@
	$(OBJDUMP) -d $@ > $(OUT)/firmware.dis

$(OUT)/firmware.bin: $(OUT)/firmware.elf
	$(OBJCOPY) -O binary $< $@

$(OUT)/firmware.hex: $(OUT)/firmware.bin
	python3 ../tools/bin2hex.py $< $@

clean:
	rm -rf $(OUT)
