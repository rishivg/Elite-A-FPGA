OUTPUT_ARCH(riscv)
ENTRY(_start)

/* 32 KB on-chip RAM = 8192 words, matching Soc(ramWords = 8192). */
MEMORY { RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 32K }

SECTIONS {
  . = 0x0;
  .text   : { *(.init) *(.text*) }                  > RAM
  .rodata : { *(.rodata*) *(.srodata*) }            > RAM
  .data   : { *(.data*) *(.sdata*) }                > RAM
  . = ALIGN(4);
  _bss_start = .;
  .bss    : { *(.bss*) *(.sbss*) *(COMMON) }         > RAM
  . = ALIGN(4);
  _bss_end = .;
  _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
