#include "bbc_options.h"
#include "render.h"
#include "teletext.h"
#include "timing.h"
#include "video.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    struct bbc_options opts = {.p_opt_flags = "", .p_log_flags = ""};
    int fast = 0;
    uint8_t memory[65536] = {0};
    struct timing_struct *t = timing_create(1);
    struct teletext_struct *tt = teletext_create();
    struct render_struct *r = render_create(tt, &opts);
    struct video_struct *v =
        video_create(memory, NULL, 1, t, r, tt, NULL, NULL, NULL, &fast, &opts);
    video_power_on_reset(v);
    for (unsigned i = 0; i < 16; i++) {
        video_crtc_write(v, 0, i);
        video_crtc_write(v, 1, 0);
    }
    FILE *out = fopen(argv[1], "wb");
    if (!out)
        return 2;
    for (unsigned reg = 0; reg < 32; reg++)
        for (unsigned val = 0; val < 256; val++) {
            video_crtc_write(v, 0, reg);
            video_crtc_write(v, 1, val);
            for (unsigned addr = 0; addr < 2; addr++)
                if (fputc(video_crtc_read(v, addr), out) == EOF)
                    return 2;
        }
    if (fclose(out))
        return 2;
    video_destroy(v);
    render_destroy(r);
    teletext_destroy(tt);
    timing_destroy(t);
    puts("Unmodified independent CRTC: 8192 register/data writes, 16384 bus reads");
    return 0;
}
