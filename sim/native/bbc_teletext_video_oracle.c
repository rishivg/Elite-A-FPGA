/* Observe unchanged, checksum-pinned beebjit video.c and teletext.c.
 * The macro redirects only the video-to-teletext call through a read-only
 * observer, which calls the original function first. Neither original source
 * nor model state is patched. Public APIs establish and run this fixture.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define teletext_data observe_teletext_data
#define render_render observe_render_render
#include "video.c"
#undef teletext_data
#undef render_render
#include "render.c"
#include "teletext.c"
static struct video_struct *observed;
static FILE *rows;
static unsigned field, last_odd, pixels;
static int cursor_cases;
static int cursor_corners;
void observe_teletext_data(struct teletext_struct *tt, uint8_t data, int dispen) {
    teletext_data(tt, data, dispen);
    if (cursor_cases)
        return;
    unsigned odd = observed->is_odd_frame;
    unsigned h = observed->horiz_counter;
    if (field < 20 || field > 21 || h < observed->skew_dispen_index + 2 ||
        h >= 42 + observed->skew_dispen_index || observed->vert_counter >= 25)
        return;
    struct render_character_2MHz out[2];
    teletext_render(tt, 0, &out[0], NULL);
    teletext_render(tt, 1, &out[1], NULL);
    unsigned scan = tt->double_active ? tt->scanline + (tt->second_character_row_of_double ? 10 : 0)
                                      : 2 * tt->scanline + tt->crtc_ra0;
    unsigned hidden = tt->is_concealed || (tt->flash_active && !tt->flash_visible_this_frame) ||
                      (tt->second_character_row_of_double && !tt->double_active);
    uint8_t *glyph = tt->p_render_character + scan * 16;
    unsigned fg = tt->render_fg_color, bg = tt->bg_color;
    unsigned dots[12];
    for (unsigned g = 0; g < 4; g++) {
        unsigned a = hidden ? 0 : glyph[g * 4] / 85, c = hidden ? 0 : glyph[g * 4 + 3] / 85,
                 w = hidden ? 0 : glyph[g * 4 + 1] / 85;
        if (a % 3 || c % 3 || w < a / 3 || (w - a / 3) % 2)
            abort();
        dots[g * 3] = a / 3;
        dots[g * 3 + 1] = (w - a / 3) / 2;
        dots[g * 3 + 2] = c / 3;
    }
    // Require the reconstructed binary dots to reproduce every pixel emitted
    // by the unchanged original renderer, including its resampling weights.
    for (unsigned g = 0; g < 4; g++) {
        unsigned a = dots[g * 3], b = dots[g * 3 + 1], c = dots[g * 3 + 2];
        unsigned weights[] = {3 * a, a + 2 * b, 2 * b + c, 3 * c};
        for (unsigned i = 0; i < 4; i++) {
            unsigned weight = weights[i] * 85;
            unsigned expected = (weight * fg + (255 - weight) * bg) | 0xff000000;
            unsigned actual = out[(g * 4 + i) / 8].host_pixels[(g * 4 + i) % 8];
            if (actual != expected) {
                fprintf(stderr, "Original dot reconstruction failed\n");
                abort();
            }
        }
    }
    unsigned y = (observed->vert_counter * 10 + observed->scanline_counter / 2) * 2 + odd;
    for (unsigned x = 0; x < 12; x++) {
        unsigned color = dots[x] ? fg : bg;
        fprintf(rows, "%u %u %u %u %u %u\n", field, (h - observed->skew_dispen_index - 2) * 12 + x,
                y, ((color >> 16) & 255) ? 255 : 0, ((color >> 8) & 255) ? 255 : 0,
                (color & 255) ? 255 : 0);
        pixels++;
    }
}

void observe_render_render(struct render_struct *r, uint8_t data, int odd_tick) {
    uint32_t *position = r->p_render_pos;
    int valid = position && position <= r->p_render_pos_row_max;
    if (r->vert_beam_pos & 1)
        position += r->width;
    render_render(r, data, odd_tick);
    if (!cursor_cases || !valid || field < 20 || field > 21)
        return;
    static uint32_t cell[16];
    if (!odd_tick) {
        memcpy(cell, position, 8 * sizeof(*cell));
        return;
    }
    memcpy(cell + 8, position, 8 * sizeof(*cell));
    unsigned h = observed->horiz_counter, skew = observed->skew_dispen_index;
    unsigned first_cell = cursor_corners ? 0 : skew + 2;
    unsigned last_cell = cursor_corners ? 47 : skew + 42;
    if (h < first_cell || h >= last_cell || observed->vert_counter >= 1)
        return;
    unsigned y = (observed->scanline_counter / 2) * 2 + observed->is_odd_frame;
    // Recover all twelve binary dots from all sixteen rendered pixels after
    // the original renderer applies its own cursor. Every channel's four
    // resampling weights must reconstruct exactly; no pixels are ignored.
    for (unsigned group = 0; group < 4; ++group) {
        unsigned colors[3] = {0};
        for (unsigned channel = 0; channel < 3; ++channel) {
            unsigned values[4];
            for (unsigned i = 0; i < 4; ++i)
                values[i] = (cell[group * 4 + i] >> (channel * 8)) & 255;
            unsigned a = values[0] / 255, c = values[3] / 255, w = values[1] / 85;
            if (values[0] % 255 || values[3] % 255 || values[1] % 85 || w < a || (w - a) % 2)
                abort();
            unsigned b = (w - a) / 2;
            if (b > 1 || values[2] != (2 * b + c) * 85)
                abort();
            colors[0] |= a << channel;
            colors[1] |= b << channel;
            colors[2] |= c << channel;
        }
        for (unsigned dot = 0; dot < 3; ++dot) {
            unsigned color = colors[dot];
            fprintf(rows, "%u %u %u %u %u %u\n", field, (h - first_cell) * 12 + group * 3 + dot, y,
                    (color & 4) ? 255 : 0, (color & 2) ? 255 : 0, (color & 1) ? 255 : 0);
            pixels++;
        }
    }
}
static void painted(void *p, int a, int b, int c) {
    (void)p;
    (void)a;
    (void)b;
    (void)c;
}
int main(int argc, char **argv) {
    if (argc != 2 &&
        (argc != 3 || (strcmp(argv[2], "cursor") && strcmp(argv[2], "cursor-corners"))))
        return 2;
    cursor_cases = argc == 3;
    cursor_corners = cursor_cases && !strcmp(argv[2], "cursor-corners");
    FILE *out = fopen(argv[1], "w");
    if (!out)
        return 2;
    unsigned cases = cursor_corners ? 512 : cursor_cases ? 64 : 4;
    fprintf(out, "cases %u\n", cases);
    for (unsigned case_id = 0; case_id < cases; case_id++) {
        unsigned pattern = cursor_corners ? (case_id >> 1) & 1 : cursor_cases ? 2 : case_id & 1;
        unsigned skew = cursor_cases ? case_id & 1 : case_id >> 1;
        unsigned cursor_skew = (case_id >> 1) & 3, mask = case_id >> 3;
        if (cursor_corners) {
            cursor_skew = (case_id >> 6) & 3;
            mask = 7;
        }
        // The original renderer's public border option exposes the entire
        // declared comparison window instead of clipping its right-hand cells.
        struct bbc_options opts = {.p_opt_flags = cursor_corners
                                                      ? "video:always-render,video:border-chars=8"
                                                      : "video:always-render",
                                   .p_log_flags = ""};
        int fast = 0;
        unsigned text[] = {1,  69, 76, 73,   84, 69, 45,   65, 32, 2,  70, 80, 71,
                           65, 32, 19, 0x7f, 30, 26, 0x3f, 31, 32, 13, 65, 66, 67,
                           12, 8,  65, 9,    24, 65, 7,    65, 29, 32, 28, 32};
        uint8_t memory[65536];
        for (unsigned a = 0; a < 65536; a++)
            memory[a] = pattern == 2 ? 32
                        : pattern    ? text[(a & 1023) % (sizeof(text) / sizeof(text[0]))]
                                     : ((a * 17) ^ (a >> 3)) & 127;
        struct timing_struct *t = timing_create(1);
        struct teletext_struct *tt = teletext_create();
        struct render_struct *r = render_create(tt, &opts);
        render_create_internal_buffer(r);
        observed = video_create(memory, NULL, 0, t, r, tt, NULL, painted, NULL, &fast, &opts);
        video_power_on_reset(observed);
        video_apply_wall_time_delta(observed, 20000);
        for (unsigned i = 0; i < 16; i++) {
            video_crtc_write(observed, 0, i);
            video_crtc_write(observed, 1, 0);
        }
        video_ula_write(observed, 0, 0);
        unsigned regs[] = {63, 40, 51, 0x24, 30, 0, 25, 28, 3 | (skew << 4),
                           18, 32, 8,  0x28, 0,  0, 0};
        unsigned control = cursor_cases ? 2 | (mask << 5) : 2;
        if (cursor_cases) {
            regs[6] = 1;
            regs[8] |= cursor_skew << 6;
            regs[10] = 0;
            regs[11] = 18;
            regs[14] = 0x28;
            regs[15] = 7;
            if (cursor_corners) {
                static const unsigned ranges[][2] = {{0, 18}, {3, 5}, {18, 1}, {31, 31}};
                static const unsigned positions[] = {0, 7, 38, 39, 40, 41, 79, 0x3fff};
                unsigned range = (case_id >> 2) & 3;
                unsigned blink = (case_id >> 4) & 3;
                unsigned position = (0x2800 + positions[case_id >> 6]) & 0x3fff;
                regs[10] = ranges[range][0] | (blink << 5);
                regs[11] = ranges[range][1];
                regs[14] = position >> 8;
                regs[15] = position & 255;
            }
        }
        rows = tmpfile();
        field = last_odd = pixels = 0;
        unsigned steps = 0;
        while (field < 22 && steps < 2000000) {
            int64_t n = timing_get_countdown(t);
            timing_advance_time(t, n - 1);
            video_advance_crtc_timing(observed);
            if (observed->is_odd_frame != last_odd) {
                field++;
                last_odd = observed->is_odd_frame;
            }
            if (steps < 32)
                video_crtc_write(observed, steps % 2, steps % 2 ? regs[steps / 2] : steps / 2);
            if (steps == 32)
                video_ula_write(observed, 0, control);
            steps++;
        }
        fprintf(out, "case %u 3 0 %u %u 33 %u\n", case_id, pattern, steps, pixels);
        for (unsigned i = 0; i < 16; i++)
            fprintf(out, "1 0 %u\n1 1 %u\n", i, regs[i]);
        fprintf(out, "2 0 %u\n", control);
        rewind(rows);
        int c;
        while ((c = fgetc(rows)) != EOF)
            fputc(c, out);
        fclose(rows);
        if (field != 22 || pixels != (cursor_corners ? 11280 : cursor_cases ? 9600 : 240000)) {
            fprintf(stderr, "Incomplete original C fields\n");
            return 2;
        }
        video_destroy(observed);
        render_destroy(r);
        teletext_destroy(tt);
        timing_destroy(t);
    }
    if (fclose(out))
        return 2;
    printf("{\"cases\":%u,\"pixels\":%u}\n", cases,
           cases * (cursor_corners ? 11280
                    : cursor_cases ? 9600
                                   : 240000));
    return 0;
}
