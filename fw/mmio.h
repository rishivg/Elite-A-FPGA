#ifndef FPGALEET_MMIO_H
#define FPGALEET_MMIO_H

/* PicoRV32 public MMIO ABI. Legacy game registers use word indices. */
#define MMIO_BASE 0x10000000u
#define MMIO ((volatile unsigned int *)MMIO_BASE)
#define MMIO_REG(offset) (*(volatile unsigned int *)(MMIO_BASE + (offset)))
enum {
    DBG = 0,
    CTRL = 1,
    FRAME = 2,
    INPUT = 3,
    M0 = 4,
    LP0 = 13,
    LP1 = 14,
    LCOL = 15,
    T0 = 16,
    CCTR = 19,
    CRAD = 20,
    CCOL = 21,
    INPUT2 = 22,
    PSG = 23,
    MODEL = 24
};
#define START 1u
#define CLEAR 2u
#define START_LINE 4u
#define START_CIRCLE 8u

/* Released-code kernels use byte offsets, packed little-endian original bytes.
 * These are signed magnitudes, not Q8.8. Full-word operand stores are required.
 */
enum {
    ELITE_NORMAL = 0x64,
    ELITE_DOTS = 0x68,
    ELITE_CONFIG = 0x6c,
    ELITE_FACE_CONTROL = 0x70,
    ELITE_FACE_RESULT = 0x74,
    ELITE_VECTOR = 0x78,
    ELITE_MATRIX = 0x80,
    ELITE_POSE_CONTROL = 0x94,
    ELITE_POSE_RESULT = 0x98,
    ELITE_WORLD = 0xa0,
    ELITE_REDUCED = 0xac,
    ELITE_SCREEN = 0xb4
};
enum {
    ELITE_START = 1u,
    ELITE_TRANSFER_FACE = 2u,
    ELITE_NORMALIZE = 4u,
    ELITE_TRANSPOSE = 8u,
    ELITE_VERTEX = 16u,
    ELITE_BUSY = 1u,
    ELITE_DONE = 2u
};

#endif
