/* Execute the unchanged independent C VIA through public APIs. No reference
 * state or expected data is copied into the DUT. */
#include "timing.h"
#include "via.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint32_t word(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    FILE *input = fopen(argv[1], "rb"), *output = fopen(argv[2], "wb");
    unsigned char header[12], row[8];
    if (!input || !output || fread(header, sizeof(header), 1, input) != 1 ||
        memcmp(header, "T2S1", 4) || word(header + 4) != 1536 ||
        fwrite(header, sizeof(header), 1, output) != 1)
        return 2;
    uint32_t observations = 0, differences = 0;
    for (unsigned id = 0; id < 1536; id++) {
        if (fread(row, sizeof(row), 1, input) != 1 || word(row) != id ||
            fwrite(row, sizeof(row), 1, output) != 1)
            return 2;
        const uint32_t count = word(row + 4);
        struct timing_struct *timing = timing_create(1);
        struct via_struct *via = via_create(k_via_user, 0, timing, NULL);
        via_power_on_reset(via);
        for (uint32_t i = 0; i < count; i++) {
            if (fread(row, sizeof(row), 1, input) != 1 || row[0] > 2 || row[1] > 15 || row[3])
                return 2;
            timing_advance_time_delta(timing, 2);
            uint8_t read = 0;
            if (row[0] == 1)
                via_write(via, row[1], row[2]);
            if (row[0] == 2)
                read = via_read(via, row[1]);
            uint8_t low = via_read_no_side_effects(via, 8), high = via_read_no_side_effects(via, 9);
            uint8_t flags = via_read_no_side_effects(via, 13);
            if (row[4] != low || row[5] != high || row[6] != flags || row[7] != read) {
                if (differences++ < 20)
                    fprintf(stderr, "case %u op %u: count %u/%u flags %u/%u read %u/%u\n", id, i,
                            low + high * 256, row[4] + row[5] * 256, flags, row[6], read, row[7]);
            }
            row[4] = low;
            row[5] = high;
            row[6] = flags;
            row[7] = read;
            if (fwrite(row, sizeof(row), 1, output) != 1)
                return 2;
            observations++;
        }
        via_destroy(via);
        timing_destroy(timing);
    }
    if (observations != word(header + 8) || fgetc(input) != EOF || fclose(input) || fclose(output))
        return 2;
    printf("Independent C T2 counter: %u observations in 1536 cases; %u differences from original "
           "JS\n",
           observations, differences);
    return differences ? 1 : 0;
}
