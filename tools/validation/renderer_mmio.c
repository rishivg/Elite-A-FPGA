// CPU fixture driver. Tables are generated from executed original 6502 code.
#include "mmio.h"
#define REG(offset) MMIO_REG(offset)
static int transform(const u32 *row, u32 transfer) {
    REG(ELITE_VECTOR) = row[0];
    REG(0x7c) = row[1];
    for (u32 word = 0; word < 5; word++)
        REG(ELITE_MATRIX + 4 * word) = row[2 + word];
    // Operand registers accept full-word stores only.
    *(volatile unsigned char *)0x10000078u = 0;
    if (REG(ELITE_VECTOR) != row[0])
        return 1;
    REG(ELITE_POSE_CONTROL) = ELITE_START | transfer;
    u32 guard = 0;
    while (!(REG(ELITE_POSE_CONTROL) & 2u) && guard < 1000u)
        guard++;
    return guard >= 1000u || REG(ELITE_POSE_RESULT) != row[7] || REG(0x9c) != row[8];
}
int main(void) {
    for (u32 i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        REG(ELITE_NORMAL) = cases[i][0];
        if (!cases[i][5])
            REG(ELITE_DOTS) = cases[i][1];
        REG(ELITE_CONFIG) = cases[i][2];
        // A byte store must not corrupt the full-word-only operands.
        *(volatile unsigned char *)0x10000064u = 0;
        if (REG(ELITE_NORMAL) != cases[i][0]) {
            REG(0) = 0xBAD10000u | i;
            return 1;
        }
        REG(ELITE_FACE_CONTROL) = 1;
        u32 guard = 0;
        while (!(REG(ELITE_FACE_CONTROL) & 2u) && guard < 1000u)
            guard++;
        if (guard >= 1000u || REG(ELITE_FACE_RESULT) != cases[i][3] ||
            REG(ELITE_DOTS) != cases[i][4]) {
            REG(0) = 0xBAD20000u | i;
            return 1;
        }
        REG(ELITE_FACE_CONTROL) = 0;
        if (REG(ELITE_FACE_CONTROL) != 2u) {
            REG(0) = 0xBAD30000u | i;
            return 1;
        }
    }
    for (u32 i = 0; i < sizeof(matrices) / sizeof(matrices[0]); i++) {
        if (transform(matrices[i], 0u)) {
            REG(0) = 0xBAD40000u | i;
            return 1;
        }
    }
    for (u32 i = 0; i < sizeof(pipelines) / sizeof(pipelines[0]); i++) {
        const u32 *row = pipelines[i];
        REG(ELITE_NORMAL) = row[9];
        REG(ELITE_DOTS) = 0xabcdefu;
        REG(ELITE_CONFIG) = row[10] | 7u;
        if (transform(row, 2u)) {
            REG(0) = 0xBAD50000u | i;
            return 1;
        }
        u32 dots = (row[7] & 255u) | ((row[7] >> 8) & 0xff00u) | ((row[8] & 255u) << 16);
        u32 signs =
            ((row[7] >> 15) & 1u) | (((row[7] >> 31) & 1u) << 1) | (((row[8] >> 15) & 1u) << 2);
        if (REG(ELITE_DOTS) != dots || REG(ELITE_CONFIG) != (row[10] | signs) ||
            REG(ELITE_FACE_CONTROL) != 0u) {
            REG(0) = 0xBAD60000u | i;
            return 1;
        }
        REG(ELITE_FACE_CONTROL) = 1u;
        u32 guard = 0;
        while (!(REG(ELITE_FACE_CONTROL) & 2u) && guard < 1000u)
            guard++;
        if (guard >= 1000u || REG(ELITE_FACE_RESULT) != row[11] || REG(ELITE_DOTS) != row[12]) {
            REG(0) = 0xBAD70000u | i;
            return 1;
        }
    }
    for (u32 i = 0; i < sizeof(raw_matrices) / sizeof(raw_matrices[0]); i++) {
        const u32 *row = raw_matrices[i];
        if (transform(row, 4u | (row[14] << 3))) {
            REG(0) = 0xBAD80000u | i;
            return 1;
        }
        for (u32 word = 0; word < 5; word++) {
            if (REG(ELITE_MATRIX + 4 * word) != row[9 + word]) {
                REG(0) = 0xBAD90000u | i;
                return 1;
            }
        }
    }
    for (u32 i = 0; i < sizeof(raw_pipelines) / sizeof(raw_pipelines[0]); i++) {
        const u32 *row = raw_pipelines[i];
        REG(ELITE_NORMAL) = row[9];
        REG(ELITE_DOTS) = 0xabcdefu;
        REG(ELITE_CONFIG) = row[10] | 7u;
        if (transform(row, 6u)) {
            REG(0) = 0xBADA0000u | i;
            return 1;
        }
        REG(ELITE_FACE_CONTROL) = 1u;
        u32 guard = 0;
        while (!(REG(ELITE_FACE_CONTROL) & 2u) && guard < 1000u)
            guard++;
        if (guard >= 1000u || REG(ELITE_FACE_RESULT) != row[11] || REG(ELITE_DOTS) != row[12]) {
            REG(0) = 0xBADB0000u | i;
            return 1;
        }
    }
    for (u32 i = 0; i < sizeof(vertices) / sizeof(vertices[0]); i++) {
        const u32 *row = vertices[i];
        for (u32 word = 0; word < 3; word++)
            REG(ELITE_WORLD + 4 * word) = row[9 + word];
        *(volatile unsigned char *)0x100000a0u = 0;
        if (REG(ELITE_WORLD) != row[9]) {
            REG(0) = 0xBADC0000u | i;
            return 1;
        }
        if (transform(row, ELITE_NORMALIZE | ELITE_TRANSPOSE | ELITE_VERTEX)) {
            REG(0) = 0xBADD0000u | i;
            return 1;
        }
        if (REG(ELITE_REDUCED) != row[12] || REG(0xb0) != row[13] || REG(ELITE_SCREEN) != row[14]) {
            REG(0) = 0xBADE0000u | i;
            return 1;
        }
        for (u32 word = 0; word < 5; word++) {
            if (REG(ELITE_MATRIX + 4 * word) != row[15 + word]) {
                REG(0) = 0xBADF0000u | i;
                return 1;
            }
        }
    }
    REG(0) = 0xE11EA001u;
    return 0;
}
