/* Unchanged beebjit bitmap ULA renderer, through public rendering APIs.
 * Original Hitachi character clocks and RAM fetches supply its input pins.
 * No C CRTC startup phase or DUT output enters this character renderer.
 */
#include "bbc_options.h"
#include "render.h"
#include "teletext.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

static unsigned word(const unsigned char *p) { return p[0] | (p[1] << 8); }
static unsigned clock_word(const unsigned char *p) {
    return word(p) | ((uint32_t)word(p + 2) << 16);
}
int main(int argc, char **argv) {
    if (argc != 4)
        return 2;
    gzFile input = gzopen(argv[1], "rb"), output = gzopen(argv[3], "wb1");
    FILE *bus = fopen(argv[2], "r");
    if (!input || !output || !bus || gzbuffer(input, 1 << 20) || gzbuffer(output, 1 << 20))
        return 2;
    struct bbc_options options = {0};
    options.p_opt_flags = "video:border-chars=0 video:no-deinterlace-bitmap";
    struct teletext_struct *tt = teletext_create();
    struct render_struct *render = render_create(tt, &options);
    render_create_internal_buffer(render);
    if (render_get_width(render) != 640 || render_get_height(render) != 512)
        abort();
    for (unsigned i = 0; i < 8; i++)
        render_set_palette(render, i,
                           0xff000000 | ((i & 1) ? 255 : 0) | ((i & 2) ? 65280 : 0) |
                               ((i & 4) ? 16711680 : 0));
    for (unsigned i = 0; i < 16; i++)
        render_set_physical_color(render, i, 0);
    unsigned busClock = 0, address = 0, value = 0, transactions = 0;
    char kind[16];
    int fields = fscanf(bus, "%u %15s %u %u", &busClock, kind, &address, &value);
    unsigned char packet[20], pixels[8];
    unsigned slots = 0, bitmapSlots = 0, cell = 0, cursor = 0;
    int length;
    while ((length = gzread(input, packet, sizeof(packet))) == sizeof(packet)) {
        unsigned cycle = clock_word(packet);
        if (cycle != 16 + slots || packet[19])
            abort();
        while (fields == 4 && busClock <= cycle) {
            if (busClock != cycle)
                abort();
            if (!strcmp(kind, "ula") && (address & 1))
                render_set_physical_color(render, value >> 4, value & 15);
            transactions++;
            fields = fscanf(bus, "%u %15s %u %u", &busClock, kind, &address, &value);
        }
        unsigned flags = packet[11], control = packet[12], fast = !!(control & 16),
                 odd = !!(flags & 64), h = word(packet + 4), skew = (packet[14] >> 4) & 3;
        if (fast || !odd) {
            cell = packet[15];
            cursor = !!(flags & 128);
            /* This is raw CRTC DISPEN. The original C renderer applies RA3
             * blanking itself; no JavaScript scanline-display approximation
             * or expected RTL mask is substituted here.
             */
            render_set_DISPEN(render,
                              (flags & 1) && skew != 3 && h >= skew && h < packet[13] + skew);
            render_set_RA(render, packet[10]);
        }
        memset(pixels, 0, sizeof(pixels));
        if (!(control & 2)) {
            render_set_mode(render, fast, (control >> 2) & 3, 0);
            render_set_flash(render, !!(control & 1));
            render_prepare(render);
            /* Place this character in a visible renderer buffer through its
             * public beam API. Hitachi pin timing is checked separately;
             * monitor cropping is not used to omit any emitted RGB dots.
             */
            render_set_beam_pos(render, 256, 128);
            unsigned x = render_get_horiz_pos(render), y = render_get_vert_pos(render);
            uint32_t *position =
                render_get_buffer(render) + (y - 64) * render_get_width(render) + (x - 240);
            render_render(render, cell, !fast && odd);
            for (unsigned i = 0; i < 8; i++) {
                uint32_t rgb = position[i];
                if ((rgb & 255) != 0 && (rgb & 255) != 255)
                    abort();
                if (((rgb >> 8) & 255) != 0 && ((rgb >> 8) & 255) != 255)
                    abort();
                if (((rgb >> 16) & 255) != 0 && ((rgb >> 16) & 255) != 255)
                    abort();
                pixels[i] = (!!(rgb & 255) | (!!(rgb & 65280) << 1) | (!!(rgb & 16711680) << 2)) ^
                            (cursor ? 7 : 0);
            }
            bitmapSlots++;
        }
        if (gzwrite(output, pixels, sizeof(pixels)) != sizeof(pixels))
            return 2;
        slots++;
    }
    while (fields == 4 && busClock == slots + 16) {
        transactions++;
        fields = fscanf(bus, "%u %15s %u %u", &busClock, kind, &address, &value);
    }
    if (length != 0 || !gzeof(input) || fields != EOF)
        return 2;
    if (gzclose(input) != Z_OK || gzclose(output) != Z_OK || fclose(bus))
        return 2;
    render_destroy(render);
    teletext_destroy(tt);
    printf("{\"slots\":%u,\"bitmap_slots\":%u,\"bitmap_dots\":%u,\"transactions\":%u,"
           "\"uncompressed_bytes\":%llu}\n",
           slots, bitmapSlots, bitmapSlots * 8, transactions, (unsigned long long)slots * 8);
    return 0;
}
