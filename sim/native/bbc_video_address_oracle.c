/* Include byte-identical pinned video.c to call its actual static address reader.
   The module is separately compiled without LTO. No model state is injected. */
#include "video.c"
#include <stdio.h>
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
    FILE *out = fopen(argv[1], "wb");
    if (!out)
        return 2;
    uint64_t cases = 0;
    for (unsigned plane = 0; plane < 2; plane++) {
        for (unsigned a = 0; a < 65536; a++)
            memory[a] = (a >> (plane * 8));
        for (unsigned flags = 0; flags < 8; flags++) {
            video_ula_write(v, 0, ((flags & 1) ? 16 : 0) | ((flags & 2) ? 2 : 0));
            for (unsigned scroll = 0; scroll < 4; scroll++)
                for (unsigned ma = 0; ma < 16384; ma++)
                    for (unsigned ra = 0; ra < 8; ra++) {
                        const unsigned wrap[] = {0x4000, 0x2000, 0x5000, 0x2800};
                        unsigned value =
                            video_read_data_byte(v, !!(flags & 4), ma, ra, wrap[scroll]);
                        if (fputc(value, out) == EOF)
                            return 2;
                        cases++;
                    }
        }
    }
    if (fclose(out))
        return 2;
    printf("Independent video address planes: %llu byte observations\n", (unsigned long long)cases);
    video_destroy(v);
    render_destroy(r);
    teletext_destroy(tt);
    timing_destroy(t);
    return 0;
}
