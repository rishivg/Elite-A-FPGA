// Interactive desktop harness — play fpgaleet on the real Verilated SoC.
//
// Drives the SAME `Soc` the SpinalSim testbenches verify, but with LIVE keyboard
// input and a real display instead of a scripted schedule:
//   * keyboard -> io_input / io_input2 (the two MMIO button words)
//   * 128x128 1-bit framebuffer -> a window (SDL2 graphical, or terminal half-blocks)
//
// Build modes (selected by the Makefile):
//   make play        -> -DUSE_SDL : opens a real graphical window (SDL2)
//   make play-smoke  -> no SDL    : headless N-frame self-check for CI
// Without USE_SDL and on a TTY it falls back to a Unicode-half-block terminal
// renderer. `--frames N` always runs headless (no window) for the smoke test.
//
// Controls:
//   W/A/S/D or arrows  pitch/roll      ,/.  throttle - / +
//   SPACE fire   X missile   Z energy-bomb
//   C chart-cycle   H hyperspace   K dock(autopilot)
//   M market   B buy   N sell   E equip-ship      Q/Esc quit

#include "VSoc.h"
#include "verilated.h"

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

#ifdef USE_SDL
#include <SDL2/SDL.h>
#else
#include <csignal>
#include <fcntl.h>
#include <termios.h>
#endif

static const int W = 128, H = 128;    // framebuffer dimensions
static const int VIEW_PIXELS = W * H; // 16384

// ---- Verilated SoC plumbing ---------------------------------------------
static VSoc *g_top = nullptr;

#ifdef USE_SDL
#include <vector>
// Audio: sample the PSG's mixed level (io_audioSample) every AUDIO_DECIM core
// ticks into a per-frame buffer that gets queued to SDL at AUDIO_HZ. Sampling
// across ALL ticks (incl. the framebuffer read) captures the real square wave;
// a one-pole DC blocker removes the unipolar offset so volume changes don't pop.
static const int AUDIO_HZ = 44100;
static const int AUDIO_DECIM = 23;    // core ticks per audio sample (~1 MHz "core")
static std::vector<int16_t> g_audio;  // samples accumulated this frame
static bool g_audio_on = false;       // gate sampling (off during reset/headless)
static int g_audio_div = 0;           // tick decimation counter
static double g_dc_x = 0, g_dc_y = 0; // DC-blocker state

static inline void audio_sample() {
    if (++g_audio_div < AUDIO_DECIM)
        return;
    g_audio_div = 0;
    double x = (double)(unsigned)g_top->io_audioSample;
    g_dc_y = x - g_dc_x + 0.995 * g_dc_y; // y[n] = x[n] - x[n-1] + 0.995*y[n-1]
    g_dc_x = x;
    long s = (long)(g_dc_y * 90.0); // gain into int16 range
    if (s > 32767)
        s = 32767;
    if (s < -32768)
        s = -32768;
    g_audio.push_back((int16_t)s);
}
#endif

static void tick() { // one rising edge
    g_top->clk = 0;
    g_top->eval();
    g_top->clk = 1;
    g_top->eval();
#ifdef USE_SDL
    if (g_audio_on)
        audio_sample();
#endif
}

static bool wait_frame() { // clock until the firmware presents a frame
    for (long g = 0; g < 6'000'000; g++) {
        if (g_top->io_frameReady)
            return true;
        tick();
    }
    return false;
}

static void read_framebuffer(uint8_t *px) { // readSync: set addr, clock, sample
    for (int a = 0; a < VIEW_PIXELS; a++) {
        g_top->io_fbRdAddr = a;
        tick();
        px[a] = (uint8_t)(g_top->io_fbRdData & 1);
    }
}

static long count_lit(const uint8_t *px) {
    long lit = 0;
    for (int i = 0; i < VIEW_PIXELS; i++)
        lit += px[i];
    return lit;
}

// Release the presented frame so the firmware loops to the next one.
static void ack_frame() {
    g_top->io_frameAck = 1;
    tick();
    g_top->io_frameAck = 0;
}

static void reset_soc() {
    g_top->reset = 1;
    g_top->io_frameAck = 0;
    g_top->io_input = 0;
    g_top->io_input2 = 0;
    g_top->io_fbRdAddr = 0;
    for (int i = 0; i < 16; i++)
        tick();
    g_top->reset = 0;
}

// ---- headless smoke run (CI; no window, no SDL needed) ------------------
static int run_headless(long frames) {
    uint8_t *px = new uint8_t[VIEW_PIXELS];
    long blank = 0, f = 0;
    for (; f < frames; f++) {
        if (!wait_frame()) {
            fprintf(stderr, "frame %ld never presented (trap=%d)\n", f, (int)g_top->io_trap);
            break;
        }
        read_framebuffer(px);
        long lit = count_lit(px);
        if (lit == 0)
            blank++;
        if (f % 16 == 0 || f == frames - 1)
            printf("frame %4ld  lit=%5ld  dbg=0x%08x  trap=%d\n", f, lit, (unsigned)g_top->io_dbg,
                   (int)g_top->io_trap);
        ack_frame();
    }
    printf("smoke: ran %ld frames, %ld blank, final dbg=0x%08x trap=%d\n", f, blank,
           (unsigned)g_top->io_dbg, (int)g_top->io_trap);
    bool ok = (f == frames) && (blank == 0) && (g_top->io_dbg == 0xCAFE) && !g_top->io_trap;
    delete[] px;
    return ok ? 0 : 1;
}

#ifdef USE_SDL
// Build the two input words from the current keyboard state. Held keys map
// straight to bits/axes; the firmware edge-detects toggles, so holding a toggle
// key fires it once (good), and holding fire fires continuously (also good).
static void sdl_inputs(const Uint8 *ks, uint32_t &in, uint32_t &in2) {
    int roll = (ks[SDL_SCANCODE_D] || ks[SDL_SCANCODE_RIGHT]) -
               (ks[SDL_SCANCODE_A] || ks[SDL_SCANCODE_LEFT]);
    int pitch =
        (ks[SDL_SCANCODE_W] || ks[SDL_SCANCODE_UP]) - (ks[SDL_SCANCODE_S] || ks[SDL_SCANCODE_DOWN]);
    int thr = ks[SDL_SCANCODE_PERIOD] - ks[SDL_SCANCODE_COMMA];
    uint32_t b3 = 0;
    if (ks[SDL_SCANCODE_SPACE])
        b3 |= 1u << 0; // fire
    if (ks[SDL_SCANCODE_C])
        b3 |= 1u << 1; // chart cycle
    if (ks[SDL_SCANCODE_H])
        b3 |= 1u << 2; // hyperspace
    if (ks[SDL_SCANCODE_K])
        b3 |= 1u << 3; // dock autopilot
    if (ks[SDL_SCANCODE_M])
        b3 |= 1u << 4; // market
    if (ks[SDL_SCANCODE_B])
        b3 |= 1u << 5; // buy
    if (ks[SDL_SCANCODE_N])
        b3 |= 1u << 6; // sell
    if (ks[SDL_SCANCODE_E])
        b3 |= 1u << 7; // equip
    uint32_t w2 = 0;
    if (ks[SDL_SCANCODE_X])
        w2 |= 1u << 0; // missile
    if (ks[SDL_SCANCODE_Z])
        w2 |= 1u << 1; // energy bomb
    in = ((b3 & 0xff) << 24) | (((uint8_t)thr) << 16) | (((uint8_t)pitch) << 8) | ((uint8_t)roll);
    in2 = w2;
}

static const char *LEGEND =
    "controls: WASD/arrows fly | ,/. throttle | SPACE fire | X missile | "
    "Z bomb | C chart | H hyperspace | K dock | M market | B buy | N sell | "
    "E equip | F11 fullscreen | F1 help | Q/Esc quit";

static int run_sdl() {
    const int SCALE = 6; // initial 128 -> 768 px window
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO) != 0) {
        fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }
    // Nearest-neighbour scaling so the chunky 1-bit framebuffer stays crisp.
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "0");

    SDL_Window *win = SDL_CreateWindow(
        "fpgaleet  —  WASD fly  SPACE fire  X missile  C chart  M market  E equip  Q quit",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, W * SCALE, H * SCALE, SDL_WINDOW_RESIZABLE);
    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_PRESENTVSYNC);
    // Logical size + integer scale: the view fills the window crisply (with
    // letterboxing at non-integer multiples) on any resize / fullscreen.
    SDL_RenderSetLogicalSize(ren, W, H);
    SDL_RenderSetIntegerScale(ren, SDL_TRUE);
    SDL_Texture *tex =
        SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING, W, H);

    // Audio: 16-bit mono, queued (no callback) — push one frame's worth of PSG
    // samples per video frame; SDL plays them at AUDIO_HZ.
    SDL_AudioSpec want, have;
    SDL_zero(want);
    want.freq = AUDIO_HZ;
    want.format = AUDIO_S16SYS;
    want.channels = 1;
    want.samples = 1024;
    SDL_AudioDeviceID adev = SDL_OpenAudioDevice(nullptr, 0, &want, &have, 0);
    if (adev) {
        g_audio_on = true;
        SDL_PauseAudioDevice(adev, 0);
    } else
        fprintf(stderr, "audio disabled: %s\n", SDL_GetError());

    printf("%s\n", LEGEND);

    uint8_t *px = new uint8_t[VIEW_PIXELS];
    uint32_t *argb = new uint32_t[VIEW_PIXELS];
    bool quit = false, fullscreen = false;

    while (!quit) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT)
                quit = true;
            if (e.type == SDL_KEYDOWN) {
                SDL_Scancode sc = e.key.keysym.scancode;
                if (sc == SDL_SCANCODE_Q || sc == SDL_SCANCODE_ESCAPE)
                    quit = true;
                else if (sc == SDL_SCANCODE_F11) {
                    fullscreen = !fullscreen;
                    SDL_SetWindowFullscreen(win, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
                } else if (sc == SDL_SCANCODE_F1) {
                    printf("%s\n", LEGEND);
                }
            }
        }
        const Uint8 *ks = SDL_GetKeyboardState(nullptr);
        uint32_t in, in2;
        sdl_inputs(ks, in, in2);
        g_top->io_input = in;
        g_top->io_input2 = in2;

        g_audio.clear();
        if (!wait_frame()) {
            fprintf(stderr, "frame never presented (trap=%d)\n", (int)g_top->io_trap);
            break;
        }
        read_framebuffer(px);
        // Queue this frame's PSG audio, but bound latency: skip if the device
        // backlog grows past ~0.2 s (host outrunning AUDIO_HZ).
        if (adev && SDL_GetQueuedAudioSize(adev) < (Uint32)(AUDIO_HZ / 5 * sizeof(int16_t)))
            SDL_QueueAudio(adev, g_audio.data(), (Uint32)(g_audio.size() * sizeof(int16_t)));

        for (int i = 0; i < VIEW_PIXELS; i++)
            argb[i] = px[i] ? 0xFF66FF99u /* phosphor green */ : 0xFF001018u;
        SDL_UpdateTexture(tex, nullptr, argb, W * sizeof(uint32_t));
        SDL_RenderClear(ren);
        SDL_RenderCopy(ren, tex, nullptr, nullptr);
        SDL_RenderPresent(ren); // vsync-paced
        ack_frame();
    }

    g_audio_on = false;
    if (adev)
        SDL_CloseAudioDevice(adev);
    delete[] px;
    delete[] argb;
    SDL_DestroyTexture(tex);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}
#else
// ---- terminal fallback (raw stdin + Unicode half-blocks) ----------------
static struct termios g_orig_termios;
static bool g_raw = false;
static void restore_tty() {
    if (g_raw) {
        tcsetattr(STDIN_FILENO, TCSANOW, &g_orig_termios);
        g_raw = false;
    }
    printf("\033[?25h\033[0m\n");
    fflush(stdout);
}
static void on_signal(int) {
    restore_tty();
    _exit(0);
}
static void enter_raw() {
    tcgetattr(STDIN_FILENO, &g_orig_termios);
    struct termios t = g_orig_termios;
    t.c_lflag &= ~(ICANON | ECHO);
    tcsetattr(STDIN_FILENO, TCSANOW, &t);
    int fl = fcntl(STDIN_FILENO, F_GETFL, 0);
    fcntl(STDIN_FILENO, F_SETFL, fl | O_NONBLOCK);
    g_raw = true;
    printf("\033[?25l\033[2J");
    atexit(restore_tty);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
}
static const int HOLD = 5;
struct Controls {
    int roll = 0, pitch = 0, thr = 0, roll_s = 0, pitch_s = 0, thr_s = 0, btn3[8] = {0},
        in2[2] = {0};
    void axis(int &h, int &s, int v) {
        s = v;
        h = HOLD;
    }
    void key(char c) {
        switch (c) {
        case 'a':
        case 'D':
            axis(roll, roll_s, -1);
            break;
        case 'd':
        case 'C':
            axis(roll, roll_s, +1);
            break;
        case 'w':
        case 'A':
            axis(pitch, pitch_s, +1);
            break;
        case 's':
        case 'B':
            axis(pitch, pitch_s, -1);
            break;
        case ',':
            axis(thr, thr_s, -1);
            break;
        case '.':
            axis(thr, thr_s, +1);
            break;
        case ' ':
            btn3[0] = HOLD;
            break;
        case 'c':
            btn3[1] = HOLD;
            break;
        case 'h':
            btn3[2] = HOLD;
            break;
        case 'k':
            btn3[3] = HOLD;
            break;
        case 'm':
            btn3[4] = HOLD;
            break;
        case 'b':
            btn3[5] = HOLD;
            break;
        case 'n':
            btn3[6] = HOLD;
            break;
        case 'e':
            btn3[7] = HOLD;
            break;
        case 'x':
            in2[0] = HOLD;
            break;
        case 'z':
            in2[1] = HOLD;
            break;
        default:
            break;
        }
    }
    void step(uint32_t &in, uint32_t &in2w) {
        auto dec = [](int &h) {
            if (h > 0)
                h--;
        };
        int r = roll > 0 ? roll_s : 0, p = pitch > 0 ? pitch_s : 0, t = thr > 0 ? thr_s : 0;
        uint32_t b3 = 0;
        for (int i = 0; i < 8; i++)
            if (btn3[i] > 0)
                b3 |= (1u << i);
        uint32_t w2 = 0;
        for (int i = 0; i < 2; i++)
            if (in2[i] > 0)
                w2 |= (1u << i);
        in = ((b3 & 0xff) << 24) | ((uint8_t)t << 16) | ((uint8_t)p << 8) | (uint8_t)r;
        in2w = w2;
        dec(roll);
        dec(pitch);
        dec(thr);
        for (int i = 0; i < 8; i++)
            dec(btn3[i]);
        for (int i = 0; i < 2; i++)
            dec(in2[i]);
    }
};
static void render_tty(const uint8_t *px) {
    static char buf[64 * (128 * 3 + 8) + 16];
    char *o = buf;
    o += sprintf(o, "\033[H");
    for (int ty = 0; ty < H / 2; ty++) {
        for (int x = 0; x < W; x++) {
            int top = px[(2 * ty) * W + x], bot = px[(2 * ty + 1) * W + x];
            const char *g = top && bot ? "█" : top ? "▀" : bot ? "▄" : " ";
            o += sprintf(o, "%s", g);
        }
        *o++ = '\n';
    }
    *o = 0;
    fputs(buf, stdout);
    fflush(stdout);
}
static int run_terminal() {
    enter_raw();
    Controls ctrl;
    uint8_t *px = new uint8_t[VIEW_PIXELS];
    for (;;) {
        char c;
        while (read(STDIN_FILENO, &c, 1) == 1) {
            if (c == 'q' || c == 'Q') {
                restore_tty();
                return 0;
            }
            ctrl.key(c);
        }
        uint32_t in, in2;
        ctrl.step(in, in2);
        g_top->io_input = in;
        g_top->io_input2 = in2;
        if (!wait_frame())
            break;
        read_framebuffer(px);
        render_tty(px);
        ack_frame();
        usleep(40'000);
    }
    restore_tty();
    delete[] px;
    return 0;
}
#endif

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    long max_frames = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--frames")) {
            if (++i == argc) {
                fprintf(stderr, "--frames needs a positive integer\n");
                return 2;
            }
            char *end = nullptr;
            errno = 0;
            max_frames = strtol(argv[i], &end, 10);
            if (errno == ERANGE || end == argv[i] || *end || max_frames <= 0) {
                fprintf(stderr, "--frames needs a positive integer\n");
                return 2;
            }
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 2;
        }
    }

    g_top = new VSoc;
    reset_soc();

    int rc;
    if (max_frames > 0)
        rc = run_headless(max_frames); // CI smoke (no window)
#ifdef USE_SDL
    else
        rc = run_sdl(); // real graphical window
#else
    else
        rc = run_terminal(); // terminal fallback
#endif

    delete g_top;
    return rc;
}
