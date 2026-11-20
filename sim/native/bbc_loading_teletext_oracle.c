/* Unchanged C SAA5050, driven through public pin/character/render APIs.
 * Fetch inputs and cursor control come from an independent original BBC.
 * This compares teletext cells without substituting the C CRTC's cold timing.
 */
#include "render.h"
#include "teletext.c"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <zlib.h>

static unsigned word(const unsigned char *p) { return p[0] | (p[1] << 8); }
static uint32_t clock_word(const unsigned char *p) {
    return word(p) | ((uint32_t)word(p + 2) << 16);
}
static void put(unsigned char *p, uint64_t value, unsigned count) {
    for (unsigned i = 0; i < count; i++)
        p[i] = value >> (8 * i);
}

int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    gzFile input = gzopen(argv[1], "rb"), output = gzopen(argv[2], "wb1");
    if (!input || !output)
        return 2;
    if (gzbuffer(input, 1 << 20) || gzbuffer(output, 1 << 20))
        return 2;
    struct teletext_struct *tt = teletext_create();
    unsigned char packet[20];
    unsigned count = 0, previous_vsync = 0, eligible = 0;
    int length;
    while ((length = gzread(input, packet, sizeof(packet))) == sizeof(packet)) {
        unsigned flags = packet[11];
        if (flags & 64)
            continue;
        unsigned cycle = clock_word(packet), h = word(packet + 4), ma = word(packet + 6),
                 line = word(packet + 8);
        unsigned ctrl = packet[12], skew = (packet[14] >> 4) & 3, ra = packet[10];
        unsigned vsync = (flags >> 1) & 1;
        if (previous_vsync && !vsync)
            teletext_VSYNC_changed(tt, 0);
        previous_vsync = vsync;
        teletext_RA_ISV_changed(tt, ra, (packet[14] & 3) == 3);
        unsigned display = (flags & 1) && skew != 3 && h >= skew && h < packet[13] + skew;
        teletext_data(tt, ma & 0x2000 ? packet[15] : 0, (ma & 0x2000) && display);
        struct render_character_2MHz pixels[2];
        teletext_render(tt, 0, pixels, NULL);
        teletext_render(tt, 1, pixels + 1, NULL);
        uint64_t rgb = 0;
        for (unsigned group = 0; group < 4; group++) {
            unsigned colors[3] = {0};
            uint32_t weights[4];
            for (unsigned i = 0; i < 4; i++)
                weights[i] = pixels[(group * 4 + i) / 8].host_pixels[(group * 4 + i) % 8];
            for (unsigned channel = 0; channel < 3; channel++) {
                unsigned shift = channel == 0 ? 16 : channel == 1 ? 8 : 0;
                int a = (weights[0] >> shift) & 255, c = (weights[3] >> shift) & 255,
                    w = (weights[1] >> shift) & 255;
                int b = (3 * w - a) / 2;
                if ((a != 0 && a != 255) || (b != 0 && b != 255) || (c != 0 && c != 255) ||
                    ((weights[2] >> shift) & 255) != (unsigned)((2 * b + c) / 3) ||
                    2 * b != 3 * w - a)
                    abort();
                colors[0] |= (a ? 1 : 0) << channel;
                colors[1] |= (b ? 1 : 0) << channel;
                colors[2] |= (c ? 1 : 0) << channel;
            }
            for (unsigned i = 0; i < 3; i++)
                rgb |= (uint64_t)colors[i] << (3 * (group * 3 + i));
        }
        if (flags & 128)
            rgb ^= (1ULL << 36) - 1;
        unsigned x = ((h - skew - 2) * 12) & 2047;
        unsigned y = (packet[14] & 3) == 3 ? (2 * line + ((flags >> 5) & 1)) & 1023 : line;
        unsigned char result[16];
        put(result, cycle, 4);
        put(result + 4, x | (y << 16), 4);
        put(result + 8, rgb, 8);
        if (gzwrite(output, result, sizeof(result)) != sizeof(result))
            return 2;
        count++;
        eligible += (ctrl & 2) && !(ctrl & 16);
    }
    if (length != 0 || !gzeof(input))
        return 2;
    if (gzclose(input) != Z_OK || gzclose(output) != Z_OK)
        return 2;
    teletext_destroy(tt);
    printf("{\"character_samples\":%u,\"teletext_samples\":%u}\n", count, eligible);
    return 0;
}
