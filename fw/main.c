/* PicoRV32 demo firmware: flight, combat, navigation, trading and commander state.
 * Draws a 128x128 monochrome framebuffer through the renderer MMIO interface.
 * Geometry uses Q8.8 arithmetic. The BBC backend runs the original game binary
 * separately; this firmware implements simplified game rules. */

#include "mmio.h"

#define Q(x) ((int)((x) * 256))
#define ONE 256
#define FXMUL(a, b) (((a) * (b)) >> 8)

static const short sintab[64] = {
    0,    25,   50,   74,   98,   121,  142,  162,  181,  198,  213,  226,  237,  245,  251,  255,
    256,  255,  251,  245,  237,  226,  213,  198,  181,  162,  142,  121,  98,   74,   50,   25,
    0,    -25,  -50,  -74,  -98,  -121, -142, -162, -181, -198, -213, -226, -237, -245, -251, -255,
    -256, -255, -251, -245, -237, -226, -213, -198, -181, -162, -142, -121, -98,  -74,  -50,  -25,
};
static int isin(int k) { return sintab[k & 63]; }
static int icos(int k) { return sintab[(k + 16) & 63]; }

typedef struct {
    int x, y, z;
} v3;

/* Ships */
enum { SHIP_EMPTY = 0, SHIP_ALIVE = 1, SHIP_EXPLODING = 2 };
enum { BEHAVIOR_STATIC = 0, BEHAVIOR_HOSTILE = 1 };
/* Ship roles: traders drift and
 * carry cargo, pirates/police are hostile, canisters are scoopable loot, and
 * asteroids mine into mineral splinters. */
enum {
    ROLE_TRADER = 0,
    ROLE_PIRATE = 1,
    ROLE_POLICE = 2,
    ROLE_CANISTER = 3,
    ROLE_ASTEROID = 4,
    ROLE_CONSTRICTOR = 5
};
/* Ship blueprints (modelIdx written to MMIO[MODEL]); must match the order of
 * Renderer.models in rtl/render/Renderer.scala. */
enum {
    MODEL_COBRA = 0,
    MODEL_SIDEWINDER = 1,
    MODEL_VIPER = 2,
    MODEL_KRAIT = 3,
    MODEL_CANISTER = 4,
    MODEL_ASTEROID = 5,
    MODEL_CONSTRICTOR = 6
};

#define NSHIPS_MAX 12
#define SHIP_HITS_INIT 3
#define BOOM_LIFETIME 24

typedef struct {
    int type; /* SHIP_EMPTY / SHIP_ALIVE / SHIP_EXPLODING */
    v3 pos;   /* view-space Q8.8 */
    int m[9]; /* row-major model->view rotation */
    int alpha;
    int beta;
    int delta;
    int hits;       /* shields remaining while alive */
    int boom_age;   /* frames since SHIP_EXPLODING transition */
    int behavior;   /* BEHAVIOR_STATIC / BEHAVIOR_HOSTILE */
    int model;      /* MODEL_* blueprint index for the Renderer */
    int role;       /* ROLE_* — what this slot is in the living world */
    int aggression; /* 0..63; threshold against 6-bit LCG draw */
    int fire_cooldown;
    int fire_visual; /* frames remaining to draw enemy fire line */
} ship_t;

static ship_t bubble[NSHIPS_MAX];

/* Player + laser state */
static int p_alpha = 0;
static int p_beta = 0;
static int p_delta = 4;

#define ROLL_MAX 1
#define PITCH_MAX 1
#define SPEED_MIN 1
#define SPEED_MAX 12 /* Cobra's top speed; the default for speed_max */
#define LASER_HEAT_MAX 32

/* Player ship: the current hull's index into ships[] and the live
 * stats derived from it. speed_max is what flight clamps to; cargo_cap (declared
 * with the market state) is the hold size. apply_ship_stats() recomputes them. */
static int player_ship = 1; /* 1 = Cobra Mk III */
static int speed_max = SPEED_MAX;
static void apply_ship_stats(void); /* defined with the shipyard, below */

static int laser_heat = 0;
static int fire_active = 0;
static int energy = 15;
static int player_shields = 15;
static int hit_flash = 0;
static int dying = 0;
static unsigned frame_no = 0;

/* Ship equipment
 * Upgrades bought on the Equip screen against `credits`. Declared up here
 * because laser_step()'s energy regen + the combat damage path reference
 * them. One-shot kit is tracked in the equip_owned bitmask; fuel/missiles/
 * cargo bay/laser tier are scalar ship state. */
#define MISSILES_MAX 4
enum {
    EQ_ECM = 1u << 0,
    EQ_FUELSCOOP = 1u << 1,
    EQ_ESCAPEPOD = 1u << 2,
    EQ_ENERGYBOMB = 1u << 3,
    EQ_ENERGYUNIT = 1u << 4,
    EQ_DOCKCOMP = 1u << 5,
    EQ_GALHYPER = 1u << 6
};
static int missiles = 0;
static int laser_tier = 1; /* 1=pulse, 2=beam/mining, 3=military */
static unsigned equip_owned = 0;

/* In-flight missile + lock state. */
static int missile_active = 0;  /* a missile is currently chasing a target */
static int missile_target = -1; /* bubble slot it's homing on */
static int missile_age = 0;     /* frames since launch */
static int missile_lock = -1;   /* slot under the reticle this frame */
static int missile_btn_prev = 0;
static int bomb_btn_prev = 0;
static int save_btn_prev = 0;
static int load_btn_prev = 0;
static int save_flash = 0;  /* overlay timer; +ve = SAVED, -ve = LOADED */
static int status_mode = 0; /* commander status screen (INPUT2 bit4) */
static int status_btn_prev = 0;

static int laser_damage(void) { return laser_tier; }

/* Navigation + hyperspace */
#define FUEL_MAX 70 /* arbitrary scale; HYPERSPACE_COST drains per jump */
#define HYPERSPACE_COST 7
#define HYPER_FLASH_FRAMES 12
static int fuel = FUEL_MAX;
/* chart_mode cycles: 0 = space view, 1 = galactic chart, 2 = system data. */
enum { CHART_OFF = 0, CHART_MAP = 1, CHART_DATA = 2, CHART_NMODES = 3 };
static int chart_mode = 0;
static int chart_btn_prev = 0; /* edge-detect chart toggle button */
static int hyper_btn_prev = 0; /* edge-detect hyperspace button */
static int cursor_x = 64;      /* chart cursor (screen coords, top region) */
static int cursor_y = 48;
static int current_sys = 0;    /* index 0..255 of current system in current galaxy */
static int current_galaxy = 0; /* 0..7, advanced by the galactic hyperdrive */
static int target_sys = 0;     /* index of selected jump destination */
static int hyper_flash = 0;    /* witchspace flash counter */

/* DOCKIT autopilot */
#define DOCK_TIMER_FRAMES 30
#define DOCK_RANGE_Q2 (Q(5) * Q(5))    /* hit threshold for "docked" */
#define DOCK_CRUISE_Q2 (Q(20) * Q(20)) /* speed up beyond this */
static int dockit_active = 0;
static int dockit_btn_prev = 0;
static int dock_timer = 0;

/* Stardust */
#define NSTARS 18
typedef struct {
    v3 pos;
} star_t;
static star_t stars[NSTARS];

/* Explosion particle pool */
#define NPARTS_MAX 32
#define BOOM_PARTICLES 14
#define PART_LIFETIME 16
#define PART_SPEED 80

typedef struct {
    int active;
    v3 pos;
    v3 vel;
    int age;
} part_t;
static part_t parts[NPARTS_MAX];

/* Scratch + shared utilities */
static int clampi(int x, int lo, int hi) { return x < lo ? lo : x > hi ? hi : x; }
static int signum(int x) { return (x > 0) - (x < 0); }

static int sb(unsigned w, int sh) {
    int v = (int)((w >> sh) & 0xFF);
    return (v & 0x80) ? (v - 256) : v;
}

static unsigned rng = 0xACE1u;
static unsigned rand_word(void) {
    rng = rng * 1103515245u + 12345u;
    return (rng >> 16) & 0xFFFFu;
}
static int prand_signed(int span) {
    unsigned v = rand_word();
    return ((int)(v % (2 * span + 1))) - span;
}

/* Q8.8 vector / matrix helpers */
static void v3_rot_plane(v3 *p, int i, int j, int k) {
    int *q = (int *)p;
    int c = icos(k), s = isin(k);
    int a = FXMUL(c, q[i]) + FXMUL(s, q[j]);
    int b = -FXMUL(s, q[i]) + FXMUL(c, q[j]);
    q[i] = a;
    q[j] = b;
}

static void m_rot_rows(int *m, int i, int j, int k) {
    int c = icos(k), s = isin(k);
    for (int col = 0; col < 3; col++) {
        int a = m[i * 3 + col], b = m[j * 3 + col];
        m[i * 3 + col] = FXMUL(c, a) + FXMUL(s, b);
        m[j * 3 + col] = -FXMUL(s, a) + FXMUL(c, b);
    }
}

static void m_rot_cols(int *m, int i, int j, int k) {
    int c = icos(k), s = isin(k);
    for (int row = 0; row < 3; row++) {
        int a = m[row * 3 + i], b = m[row * 3 + j];
        m[row * 3 + i] = FXMUL(c, a) + FXMUL(s, b);
        m[row * 3 + j] = -FXMUL(s, a) + FXMUL(c, b);
    }
}

static unsigned isqrt32(unsigned x) {
    unsigned r = 0, b = 1u << 30;
    while (b > x)
        b >>= 2;
    while (b) {
        if (x >= r + b) {
            x -= r + b;
            r = (r >> 1) + b;
        } else
            r >>= 1;
        b >>= 2;
    }
    return r;
}

static void v3_normalize(int *v) {
    int n2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    if (n2 <= 0)
        return;
    int n = (int)isqrt32((unsigned)n2);
    if (n == 0)
        return;
    v[0] = (v[0] * ONE) / n;
    v[1] = (v[1] * ONE) / n;
    v[2] = (v[2] * ONE) / n;
}

static void m_tidy(int *m) {
    int row[3];
    for (int base = 0; base < 9; base += 3) {
        row[0] = m[base];
        row[1] = m[base + 1];
        row[2] = m[base + 2];
        v3_normalize(row);
        m[base] = row[0];
        m[base + 1] = row[1];
        m[base + 2] = row[2];
    }
}

/* Generic 3-vector cross product (a × b) on Q8.8 inputs. */
static void v3_cross(const int *a, const int *b, int *out) {
    out[0] = FXMUL(a[1], b[2]) - FXMUL(a[2], b[1]);
    out[1] = FXMUL(a[2], b[0]) - FXMUL(a[0], b[2]);
    out[2] = FXMUL(a[0], b[1]) - FXMUL(a[1], b[0]);
}

/* Flight + MVEIT */
static int track(int cur, int in, int lim) {
    if (in > 0)
        return clampi(cur + 1, -lim, lim);
    if (in < 0)
        return clampi(cur - 1, -lim, lim);
    return cur - signum(cur);
}

static void flight_step(unsigned in) {
    p_alpha = track(p_alpha, sb(in, 0), ROLL_MAX);
    p_beta = track(p_beta, sb(in, 8), PITCH_MAX);
    int sp = sb(in, 16);
    if (sp > 0)
        p_delta = clampi(p_delta + 1, SPEED_MIN, speed_max);
    else if (sp < 0)
        p_delta = clampi(p_delta - 1, SPEED_MIN, speed_max);
}

static void mveit(ship_t *s) {
    if (p_beta) {
        v3_rot_plane(&s->pos, 1, 2, -p_beta);
        m_rot_rows(s->m, 1, 2, -p_beta);
    }
    if (p_alpha) {
        v3_rot_plane(&s->pos, 0, 1, -p_alpha);
        m_rot_rows(s->m, 0, 1, -p_alpha);
    }
    s->pos.z -= p_delta * 16;
    if (s->beta)
        m_rot_cols(s->m, 1, 2, s->beta);
    if (s->alpha)
        m_rot_cols(s->m, 0, 1, s->alpha);
    if (s->delta) {
        s->pos.x += FXMUL(s->m[2], s->delta);
        s->pos.y += FXMUL(s->m[5], s->delta);
        s->pos.z += FXMUL(s->m[8], s->delta);
    }
    if (p_alpha || p_beta || s->alpha || s->beta)
        m_tidy(s->m);
}

/* Slot management */
static int nwshp(int px, int py, int pz, int yaw_index, int alpha, int beta, int delta,
                 int behavior, int aggression, int hits) {
    for (int i = 0; i < NSHIPS_MAX; i++) {
        if (bubble[i].type == SHIP_EMPTY) {
            bubble[i].type = SHIP_ALIVE;
            bubble[i].pos.x = px;
            bubble[i].pos.y = py;
            bubble[i].pos.z = pz;
            bubble[i].m[0] = ONE;
            bubble[i].m[1] = 0;
            bubble[i].m[2] = 0;
            bubble[i].m[3] = 0;
            bubble[i].m[4] = ONE;
            bubble[i].m[5] = 0;
            bubble[i].m[6] = 0;
            bubble[i].m[7] = 0;
            bubble[i].m[8] = ONE;
            if (yaw_index)
                m_rot_cols(bubble[i].m, 0, 2, yaw_index);
            bubble[i].alpha = alpha;
            bubble[i].beta = beta;
            bubble[i].delta = delta;
            bubble[i].hits = hits;
            bubble[i].boom_age = 0;
            bubble[i].behavior = behavior;
            /* Pick a hull: hostiles fly fighters (Viper/Krait), drifters are
             * traders (Cobra/Sidewinder); vary by slot so a scene shows a mix. */
            bubble[i].model = (behavior == BEHAVIOR_HOSTILE)
                                  ? ((i & 1) ? MODEL_KRAIT : MODEL_VIPER)
                                  : ((i & 1) ? MODEL_SIDEWINDER : MODEL_COBRA);
            bubble[i].role = (behavior == BEHAVIOR_HOSTILE) ? ROLE_PIRATE : ROLE_TRADER;
            bubble[i].aggression = aggression;
            bubble[i].fire_cooldown = 0;
            bubble[i].fire_visual = 0;
            return i;
        }
    }
    return -1;
}

static void res2(void) {
    for (int i = 0; i < NSHIPS_MAX; i++)
        bubble[i].type = SHIP_EMPTY;
    for (int i = 0; i < NPARTS_MAX; i++)
        parts[i].active = 0;
}

/* Laser */
static void laser_step(unsigned in) {
    int fire_btn = (in >> 24) & 1;
    if (fire_btn && laser_heat < LASER_HEAT_MAX) {
        fire_active = 1;
        laser_heat++;
        if (energy > 0)
            energy--;
    } else {
        fire_active = 0;
        if (laser_heat > 0)
            laser_heat--;
        /* Extra Energy Unit regenerates shields/energy twice as fast. */
        unsigned mask = (equip_owned & EQ_ENERGYUNIT) ? 1u : 3u;
        if ((frame_no & mask) == 0 && energy < 15)
            energy++;
    }
}

/* Sound: SN76489-style PSG sound effects
 * One byte per write to MMIO[PSG] feeds the chip's latch/data protocol. A tiny
 * SFX engine drives short, decaying tone/noise bursts on game events; sfx_step()
 * runs once per frame and ramps the active effect's volume down, silencing the
 * chip when it ends. */
static void psg_write(unsigned b) { MMIO[PSG] = b & 0xFFu; }

static void psg_tone(int ch, int period, int vol) {
    psg_write(0x80u | (ch << 5) | (period & 0xF)); /* latch: low 4 bits */
    psg_write((period >> 4) & 0x3F);               /* data: high 6 bits */
    psg_write(0x80u | (ch << 5) | 0x10u | (vol & 0xF));
}
static void psg_noise(int ctrl, int vol) {
    psg_write(0x80u | (3 << 5) | (ctrl & 0x7));
    psg_write(0x80u | (3 << 5) | 0x10u | (vol & 0xF));
}
static void psg_silence(void) {
    for (int c = 0; c < 3; c++)
        psg_write(0x80u | (c << 5) | 0x10u | 0xF);
    psg_write(0x80u | (3 << 5) | 0x10u | 0xF);
}

enum { SFX_NONE = 0, SFX_LASER, SFX_BOOM, SFX_HYPER, SFX_CHORD };
static int sfx_kind = SFX_NONE, sfx_timer = 0, sfx_dur = 1;

static void sfx_play(int kind, int dur) {
    sfx_kind = kind;
    sfx_timer = dur;
    sfx_dur = dur;
}

static void sfx_step(void) {
    if (sfx_timer <= 0) {
        if (sfx_kind != SFX_NONE) {
            psg_silence();
            sfx_kind = SFX_NONE;
        }
        return;
    }
    int t = sfx_timer--;               /* counts dur .. 1 */
    int vol = 15 - (t * 15) / sfx_dur; /* loud (0) at start -> quiet (15) */
    switch (sfx_kind) {
    case SFX_LASER:
        psg_tone(0, 90, vol);
        break; /* zap */
    case SFX_BOOM:
        psg_noise(0x4, vol);
        break; /* white-noise burst */
    case SFX_HYPER:
        psg_tone(0, 60 + (sfx_dur - t) * 12, vol / 2);
        break;      /* descending pitch */
    case SFX_CHORD: /* boot chord */
        psg_tone(0, 160, vol);
        psg_tone(1, 127, vol);
        psg_tone(2, 107, vol);
        break;
    }
}

/* Explosion particles */
static void spawn_explosion(const v3 *at) {
    sfx_play(SFX_BOOM, 8);
    int spawned = 0;
    for (int i = 0; i < NPARTS_MAX && spawned < BOOM_PARTICLES; i++) {
        if (parts[i].active)
            continue;
        parts[i].active = 1;
        parts[i].pos.x = at->x;
        parts[i].pos.y = at->y;
        parts[i].pos.z = at->z;
        parts[i].vel.x = prand_signed(PART_SPEED);
        parts[i].vel.y = prand_signed(PART_SPEED);
        parts[i].vel.z = prand_signed(PART_SPEED);
        parts[i].age = 0;
        spawned++;
    }
}

/* Stardust */
static void star_respawn(star_t *s, int far) {
    s->pos.x = prand_signed(Q(40));
    s->pos.y = prand_signed(Q(30));
    s->pos.z = far;
}

static void stars_init(void) {
    for (int i = 0; i < NSTARS; i++)
        star_respawn(&stars[i], Q(8) + (i * Q(5)));
}

/* MMIO render helpers */
static void draw_ship(ship_t *s, unsigned flags) {
    MMIO[MODEL] = (unsigned)s->model; /* select this ship's blueprint */
    for (int i = 0; i < 9; i++)
        MMIO[M0 + i] = (unsigned)s->m[i];
    MMIO[T0 + 0] = (unsigned)s->pos.x;
    MMIO[T0 + 1] = (unsigned)s->pos.y;
    MMIO[T0 + 2] = (unsigned)s->pos.z;
    MMIO[CTRL] = START | flags;
    while (!(MMIO[CTRL] & 2u)) {
    }
}

static void clear_only(void) {
    static const int id[9] = {ONE, 0, 0, 0, ONE, 0, 0, 0, ONE};
    for (int i = 0; i < 9; i++)
        MMIO[M0 + i] = (unsigned)id[i];
    MMIO[T0 + 0] = 0;
    MMIO[T0 + 1] = 0;
    MMIO[T0 + 2] = (unsigned)Q(-1);
    MMIO[CTRL] = START | CLEAR;
    while (!(MMIO[CTRL] & 2u)) {
    }
}

/* 8x8 text font
 *
 * Hand-rolled 8x8 monochrome glyphs (5x7-ish shapes centred in 8x8 cells).
 * Indexed by `ASCII - 0x20` so the table covers 0x20 (space) through 0x5F.
 * Each glyph is 8 bytes — one per row, MSB = leftmost pixel. text_putc()
 * iterates set bits and emits plot_point() through the line-draw MMIO.
 * Cost: ~20-40 plot_points per glyph; ~300 per "FOO BAR" string. Cheap.
 */
static const unsigned char font8x8[96][8] = {
    /* 0x20 ' ' */ {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
    /* 0x21 '!' */ {0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x00},
    /* 0x22 '"' */ {0x6C, 0x6C, 0x6C, 0x00, 0x00, 0x00, 0x00, 0x00},
    /* 0x23 '#' */ {0x6C, 0xFE, 0x6C, 0x6C, 0x6C, 0xFE, 0x6C, 0x00},
    /* 0x24 '$' */ {0x18, 0x7E, 0xC0, 0x7C, 0x06, 0xFC, 0x18, 0x00},
    /* 0x25 '%' */ {0xC2, 0xC6, 0x0C, 0x18, 0x30, 0x66, 0xC6, 0x00},
    /* 0x26 '&' */ {0x38, 0x6C, 0x38, 0x76, 0xDC, 0xCC, 0x76, 0x00},
    /* 0x27 '\''*/ {0x18, 0x18, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00},
    /* 0x28 '(' */ {0x18, 0x30, 0x60, 0x60, 0x60, 0x30, 0x18, 0x00},
    /* 0x29 ')' */ {0x60, 0x30, 0x18, 0x18, 0x18, 0x30, 0x60, 0x00},
    /* 0x2A '*' */ {0x00, 0x6C, 0x38, 0xFE, 0x38, 0x6C, 0x00, 0x00},
    /* 0x2B '+' */ {0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00},
    /* 0x2C ',' */ {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30},
    /* 0x2D '-' */ {0x00, 0x00, 0x00, 0xFE, 0x00, 0x00, 0x00, 0x00},
    /* 0x2E '.' */ {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00},
    /* 0x2F '/' */ {0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0, 0x80, 0x00},
    /* 0x30 '0' */ {0x7C, 0xC6, 0xCE, 0xDE, 0xF6, 0xE6, 0xC6, 0x7C},
    /* 0x31 '1' */ {0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00},
    /* 0x32 '2' */ {0x7C, 0xC6, 0x06, 0x1C, 0x30, 0x60, 0xFE, 0x00},
    /* 0x33 '3' */ {0x7C, 0xC6, 0x06, 0x3C, 0x06, 0xC6, 0x7C, 0x00},
    /* 0x34 '4' */ {0x1C, 0x3C, 0x6C, 0xCC, 0xFE, 0x0C, 0x0C, 0x00},
    /* 0x35 '5' */ {0xFE, 0xC0, 0xFC, 0x06, 0x06, 0xC6, 0x7C, 0x00},
    /* 0x36 '6' */ {0x3C, 0x60, 0xC0, 0xFC, 0xC6, 0xC6, 0x7C, 0x00},
    /* 0x37 '7' */ {0xFE, 0x06, 0x0C, 0x18, 0x30, 0x30, 0x30, 0x00},
    /* 0x38 '8' */ {0x7C, 0xC6, 0xC6, 0x7C, 0xC6, 0xC6, 0x7C, 0x00},
    /* 0x39 '9' */ {0x7C, 0xC6, 0xC6, 0x7E, 0x06, 0x0C, 0x78, 0x00},
    /* 0x3A ':' */ {0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x18, 0x00},
    /* 0x3B ';' */ {0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x18, 0x30},
    /* 0x3C '<' */ {0x06, 0x0C, 0x18, 0x30, 0x18, 0x0C, 0x06, 0x00},
    /* 0x3D '=' */ {0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00, 0x00, 0x00},
    /* 0x3E '>' */ {0x60, 0x30, 0x18, 0x0C, 0x18, 0x30, 0x60, 0x00},
    /* 0x3F '?' */ {0x7C, 0xC6, 0x0C, 0x18, 0x18, 0x00, 0x18, 0x00},
    /* 0x40 '@' */ {0x7C, 0xC6, 0xDE, 0xDE, 0xDC, 0xC0, 0x7C, 0x00},
    /* 0x41 'A' */ {0x38, 0x6C, 0xC6, 0xC6, 0xFE, 0xC6, 0xC6, 0x00},
    /* 0x42 'B' */ {0xFC, 0xC6, 0xC6, 0xFC, 0xC6, 0xC6, 0xFC, 0x00},
    /* 0x43 'C' */ {0x7C, 0xC6, 0xC0, 0xC0, 0xC0, 0xC6, 0x7C, 0x00},
    /* 0x44 'D' */ {0xF8, 0xCC, 0xC6, 0xC6, 0xC6, 0xCC, 0xF8, 0x00},
    /* 0x45 'E' */ {0xFE, 0xC0, 0xC0, 0xF8, 0xC0, 0xC0, 0xFE, 0x00},
    /* 0x46 'F' */ {0xFE, 0xC0, 0xC0, 0xF8, 0xC0, 0xC0, 0xC0, 0x00},
    /* 0x47 'G' */ {0x7C, 0xC6, 0xC0, 0xCE, 0xC6, 0xC6, 0x7C, 0x00},
    /* 0x48 'H' */ {0xC6, 0xC6, 0xC6, 0xFE, 0xC6, 0xC6, 0xC6, 0x00},
    /* 0x49 'I' */ {0x7E, 0x18, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00},
    /* 0x4A 'J' */ {0x06, 0x06, 0x06, 0x06, 0x06, 0xC6, 0x7C, 0x00},
    /* 0x4B 'K' */ {0xC6, 0xCC, 0xD8, 0xF0, 0xD8, 0xCC, 0xC6, 0x00},
    /* 0x4C 'L' */ {0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xFE, 0x00},
    /* 0x4D 'M' */ {0xC6, 0xEE, 0xFE, 0xD6, 0xC6, 0xC6, 0xC6, 0x00},
    /* 0x4E 'N' */ {0xC6, 0xE6, 0xF6, 0xDE, 0xCE, 0xC6, 0xC6, 0x00},
    /* 0x4F 'O' */ {0x7C, 0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0x7C, 0x00},
    /* 0x50 'P' */ {0xFC, 0xC6, 0xC6, 0xFC, 0xC0, 0xC0, 0xC0, 0x00},
    /* 0x51 'Q' */ {0x7C, 0xC6, 0xC6, 0xC6, 0xD6, 0xCC, 0x76, 0x00},
    /* 0x52 'R' */ {0xFC, 0xC6, 0xC6, 0xFC, 0xD8, 0xCC, 0xC6, 0x00},
    /* 0x53 'S' */ {0x7C, 0xC6, 0xC0, 0x7C, 0x06, 0xC6, 0x7C, 0x00},
    /* 0x54 'T' */ {0xFF, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00},
    /* 0x55 'U' */ {0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0x7C, 0x00},
    /* 0x56 'V' */ {0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0x6C, 0x38, 0x00},
    /* 0x57 'W' */ {0xC6, 0xC6, 0xC6, 0xD6, 0xFE, 0xEE, 0xC6, 0x00},
    /* 0x58 'X' */ {0xC6, 0xC6, 0x6C, 0x38, 0x6C, 0xC6, 0xC6, 0x00},
    /* 0x59 'Y' */ {0xC6, 0xC6, 0xC6, 0x6C, 0x38, 0x18, 0x18, 0x00},
    /* 0x5A 'Z' */ {0xFE, 0x06, 0x0C, 0x18, 0x30, 0x60, 0xFE, 0x00},
    /* 0x5B '[' */ {0x3C, 0x30, 0x30, 0x30, 0x30, 0x30, 0x3C, 0x00},
    /* 0x5C '\' */ {0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06, 0x02, 0x00},
    /* 0x5D ']' */ {0x3C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x3C, 0x00},
    /* 0x5E '^' */ {0x10, 0x38, 0x6C, 0xC6, 0x00, 0x00, 0x00, 0x00},
    /* 0x5F '_' */ {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF},
    /* 0x60..0x7F is unused and remains zero */
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0},
};

#define GLYPH_W 6 /* effective glyph width incl. trailing pad */
#define GLYPH_H 8

static void plot_line(int x0, int y0, int x1, int y1, unsigned color);

static void text_putc(int x, int y, int c) {
    if (c < 0x20 || c > 0x7F)
        c = 0x20;
    const unsigned char *g = font8x8[c - 0x20];
    for (int row = 0; row < GLYPH_H; row++) {
        unsigned char rb = g[row];
        if (!rb)
            continue;
        for (int bit = 0; bit < 8; bit++) {
            if (rb & (0x80u >> bit)) {
                /* one-pixel "line" via the shared line-draw MMIO */
                plot_line(x + bit, y + row, x + bit, y + row, 1);
            }
        }
    }
}

static void text_print(int x, int y, const char *s) {
    int cx = x;
    while (*s) {
        text_putc(cx, y, (unsigned char)*s);
        cx += GLYPH_W;
        s++;
    }
}

static void plot_line(int x0, int y0, int x1, int y1, unsigned color) {
    MMIO[LP0] = ((unsigned)y0 << 16) | ((unsigned)x0 & 0xFFFFu);
    MMIO[LP1] = ((unsigned)y1 << 16) | ((unsigned)x1 & 0xFFFFu);
    MMIO[LCOL] = color;
    MMIO[CTRL] = START_LINE;
    while (!(MMIO[CTRL] & 2u)) {
    }
}
static void plot_point(int x, int y) { plot_line(x, y, x, y, 1); }

static void plot_circle(int cx, int cy, int radius, unsigned color) {
    MMIO[CCTR] = ((unsigned)cy << 16) | ((unsigned)cx & 0xFFFFu);
    MMIO[CRAD] = (unsigned)radius & 0x3FFu;
    MMIO[CCOL] = color;
    MMIO[CTRL] = START_CIRCLE;
    while (!(MMIO[CTRL] & 2u)) {
    }
}

/* Closed ellipse as a 16-segment line loop (rx/ry are pixel semi-axes). Used
 * for the planet's meridian + equator great-circle arcs — a face-on meridian
 * projects to an ellipse, so a vertical and a flattened-horizontal ellipse
 * over the disc read as a globe. icos/isin are Q8.8, so FXMUL(r, icos(a))
 * lands directly in pixels. */
#define ELLIPSE_SEG 16
static void plot_ellipse(int cx, int cy, int rx, int ry) {
    int px = cx + rx, py = cy; /* angle 0: (cx+rx, cy) */
    for (int i = 1; i <= ELLIPSE_SEG; i++) {
        int a = (i * 64) / ELLIPSE_SEG; /* sweep 0..64 (full turn) */
        int nx = cx + FXMUL(rx, icos(a));
        int ny = cy + FXMUL(ry, isin(a));
        plot_line(px, py, nx, ny, 1);
        px = nx;
        py = ny;
    }
}

/* Geometry / projection */
#define CX 64
#define CY 48
#define FOCAL 160
#define VIEW_H_PX 96
#define CROSS_HALF 5

static int project(const v3 *p, int *sx_out, int *sy_out) {
    if (p->z <= 0)
        return 0;
    int sx = CX + (FOCAL * p->x) / p->z;
    int sy = CY - (FOCAL * p->y) / p->z;
    if (sx < 0 || sx > 127 || sy < 0 || sy >= VIEW_H_PX)
        return 0;
    *sx_out = sx;
    *sy_out = sy;
    return 1;
}

/* Galaxy generator
 * Three 16-bit words evolve by a Tribonacci recurrence (a' = a+b+c).
 * The demo uses CAFE/F00D/BEEF seeds; its systems differ from released Elite-A.
 * Bit-rotating the seeds selects each of the eight galaxy layouts.
 *
 * System data taps from the seed bytes:
 *   x          = high byte of s[1]      0..255 galactic chart x
 *   y          = high byte of s[0]      0..255 galactic chart y
 *   economy    = low  3 bits of s[0]    0..7
 *   government = bits 3..5 of s[1]      0..7
 *   population = bits 4..9 of s[2]      0..63
 *   productivity = low byte of s[2]     0..255 (Elite uses a derived "productivity")
 */
typedef struct {
    unsigned char x, y;
    unsigned char economy;
    unsigned char government;
    unsigned char population;
    unsigned char productivity;
} system_t;

static unsigned short galaxy_seed[3] = {0xCAFE, 0xF00D, 0xBEEF};

/* Per-galaxy rotation: same idea as Elite's tweak() / galactic shift, but
 * applied to the demo seeds. Bit-rotates each word left by 1 to derive the next
 * galaxy from the previous. Iterating this 7 times after galaxy 0 gives
 * galaxies 1..7 (8 galaxies total). */
static unsigned short rot16(unsigned short w) { return (unsigned short)((w << 1) | (w >> 15)); }

static void galaxy_init(unsigned char idx) {
    galaxy_seed[0] = 0xCAFE;
    galaxy_seed[1] = 0xF00D;
    galaxy_seed[2] = 0xBEEF;
    for (unsigned char i = 0; i < idx; i++) {
        galaxy_seed[0] = rot16(galaxy_seed[0]);
        galaxy_seed[1] = rot16(galaxy_seed[1]);
        galaxy_seed[2] = rot16(galaxy_seed[2]);
    }
}

static void galaxy_step(unsigned short *s) {
    unsigned int t = (unsigned int)s[0] + (unsigned int)s[1] + (unsigned int)s[2];
    s[0] = s[1];
    s[1] = s[2];
    s[2] = (unsigned short)(t & 0xFFFFu);
}

static void galaxy_extract(const unsigned short *s, system_t *sys) {
    sys->x = (unsigned char)((s[1] >> 8) & 0xFFu);
    sys->y = (unsigned char)((s[0] >> 8) & 0xFFu);
    sys->economy = (unsigned char)(s[0] & 0x07u);
    sys->government = (unsigned char)((s[1] >> 3) & 0x07u);
    sys->population = (unsigned char)((s[2] >> 4) & 0x3Fu);
    sys->productivity = (unsigned char)(s[2] & 0xFFu);
}

/* Boot self-test: run the PRNG over the first 8 systems of galaxy 0 and
 * compare against locked test vectors that match `tools/oracle/galaxy.py`.
 * Returns 1 if all 8 match, else 0. Caller signals via DBG so the existing
 * SocSim (which asserts DBG==0xCAFE) doubles as a galaxy self-check —
 * any drift between firmware C and Python oracle fails the boot. */
static const unsigned char GALAXY0_X[8] = {0xBE, 0x79, 0x28, 0x61, 0x04, 0x8F, 0xF6, 0x8A};
static const unsigned char GALAXY0_Y[8] = {0xF0, 0xBE, 0x79, 0x28, 0x61, 0x04, 0x8F, 0xF6};

static int galaxy_self_test(void) {
    unsigned short s[3];
    galaxy_init(0);
    s[0] = galaxy_seed[0];
    s[1] = galaxy_seed[1];
    s[2] = galaxy_seed[2];
    for (int i = 0; i < 8; i++) {
        galaxy_step(s);
        system_t sys;
        galaxy_extract(s, &sys);
        if (sys.x != GALAXY0_X[i] || sys.y != GALAXY0_Y[i])
            return 0;
    }
    return 1;
}

/* Replay the PRNG up to the `idx`-th system in the current galaxy, leaving the
 * three 16-bit seeds in s[] — the exact seed state galaxy_extract reads for that
 * system, and the seed the name generator twists. Same enumeration as the chart
 * routines. */
static void get_system_seed(int idx, unsigned short *s) {
    galaxy_init(current_galaxy);
    s[0] = galaxy_seed[0];
    s[1] = galaxy_seed[1];
    s[2] = galaxy_seed[2];
    for (int i = 0; i <= idx; i++)
        galaxy_step(s);
}

/* Resolve the system_t for the `idx`-th system by replaying to its seed. */
static void get_system(int idx, system_t *out) {
    unsigned short s[3];
    get_system_seed(idx, s);
    galaxy_extract(s, out);
}

/* Procedural system names
 *
 * Port of Elite's `cpl` digram name generator onto the demo galaxy seeds. A
 * system's name is 3 or 4 two-letter tokens (4 iff bit 6 of s0_lo is set);
 * each token index is `s2_hi & %11111` (0 = emit nothing this round), looked up
 * in the 32-entry digram table; the seed is twisted (= galaxy_step, which IS
 * Elite's TT54: s2' = s0+s1+s2) between tokens. A '?' as a token's second
 * letter marks a one-letter token (print the first letter only). Names are 0..8
 * letters; the generator works on a local copy so it never disturbs the seed. */
static const char DIGRAMS[65] = "ALLEXEGEZACEBISOUSESARMAINDIREA?ERATENBERALAVETIEDORQUANTEISRION";

static void system_name_from_seed(unsigned short s0, unsigned short s1, unsigned short s2,
                                  char *buf) {
    unsigned short s[3] = {s0, s1, s2};
    int pairs = (s[0] & 0x40) ? 4 : 3; /* bit 6 of s0_lo */
    int n = 0;
    for (int i = 0; i < pairs; i++) {
        int idx = (s[2] >> 8) & 0x1F; /* s2_hi & %11111 */
        if (idx != 0) {
            buf[n++] = DIGRAMS[idx * 2];
            char c2 = DIGRAMS[idx * 2 + 1];
            if (c2 != '?')
                buf[n++] = c2; /* '?' => one-letter token */
        }
        galaxy_step(s); /* TT54 twist */
    }
    buf[n] = '\0';
}

/* Name of the `idx`-th system in the current galaxy into buf (>= 9 bytes). */
static void system_name(int idx, char *buf) {
    unsigned short s[3];
    get_system_seed(idx, s);
    system_name_from_seed(s[0], s[1], s[2], buf);
}

/* Boot self-test, mirrored by tools/oracle/names.py: a 16-bit rolling hash over
 * the first 8 system names of galaxy 0. Folded into the DBG sentinel alongside
 * galaxy/cmdr so any C/Python drift in the digram generator fails the boot.
 * Runs at startup with current_galaxy == 0. */
#define NAMES_HASH 0xBD3E
static int names_self_test(void) {
    unsigned h = 0;
    char name[9];
    for (int idx = 0; idx < 8; idx++) {
        system_name(idx, name);
        for (int j = 0; name[j]; j++)
            h = (h * 31u + (unsigned char)name[j]) & 0xFFFFu;
        h = (h * 31u) & 0xFFFFu; /* name separator */
    }
    return h == NAMES_HASH;
}

/* Procedural system descriptions: the "goat soup" generator
 *
 * Port of Elite's PDESC: a per-system seeded RNG drives a recursive phrase
 * grammar that builds a sentence like "THE PLANET BELARARI IS REASONABLY WELL
 * KNOWN FOR ITS ANCIENT OCEANS." The vocabulary words are the real Elite ones
 * (FABLED / PLAGUED / KILLER / GARGLE BLASTERS / ...); the grammar is a bounded
 * subset of the full token tree (recursion <= 3 deep). The RNG is seeded from
 * the system's own s1/s2 seeds so each system always gets the same description.
 * tools/oracle/desc.py mirrors every rng call site in order; the parity is
 * locked via DESC_HASH in the DBG sentinel. */
static const char *const D_ADJ[5] = {"FABLED", "NOTABLE", "WELL KNOWN", "FAMOUS", "NOTED"};
static const char *const D_INTENS[5] = {"MOST ", "REASONABLY ", "VERY ", "MILDLY ", ""};
static const char *const D_ANCIENT[5] = {"ANCIENT", "STRANGE", "GREAT", "VAST", "PINK"};
static const char *const D_FOREST[5] = {"TROPICAL", "DENSE", "RAIN", "IMPENETRABLE", "EXUBERANT"};
static const char *const D_AFFLICT[5] = {"BESET", "PLAGUED", "RAVAGED", "CURSED", "SCOURGED"};
static const char *const D_FREQ[5] = {"FREQUENT", "OCCASIONAL", "UNPREDICTABLE", "DREADFUL",
                                      "DEADLY"};
static const char *const D_KILLER[5] = {"KILLER", "DEADLY", "EVIL", "LETHAL", "VICIOUS"};
static const char *const D_GOODADJ[5] = {"FABULOUS", "EXOTIC", "HOOPY", "UNUSUAL", "EXCITING"};
static const char *const D_GOODN[5] = {"CUISINE", "NIGHT LIFE", "CASINOS", "SIT COMS",
                                       "GARGLE BLASTERS"};
static const char *const D_INHAB[5] = {"ANCIENT", "EXCEPTIONAL", "ECCENTRIC", "INGRAINED",
                                       "STRANGE"};
static const char *const D_CUSTOM[5] = {"SHYNESS", "SILLINESS", "MATING TRADITIONS", "", ""};
static const char *const D_OBJECT[5] = {"FOOD BLENDERS", "TOURISTS", "POETRY", "DISCOS",
                                        "GARGLE BLASTERS"};
static const char *const D_DULL[5] = {"N UNREMARKABLE", " BORING", " DULL", " TEDIOUS",
                                      " REVOLTING"};
static const char *const D_PLACE[5] = {"PLANET", "WORLD", "PLACE", "DUMP", "BACKWATER"};

static unsigned long desc_rng;
static char desc_buf[200];
static int desc_len;

static unsigned desc_rand(unsigned n) {
    desc_rng = desc_rng * 1103515245ul + 12345ul;
    return (unsigned)((desc_rng >> 16) % n);
}
static void d_str(const char *s) {
    while (*s && desc_len < (int)sizeof(desc_buf) - 1)
        desc_buf[desc_len++] = *s++;
}
static void d_pick(const char *const *tbl) { d_str(tbl[desc_rand(5)]); }

static void d_thing(void) {
    switch (desc_rand(3)) {
    case 0:
        d_str("MOUNTAINS");
        break;
    case 1:
        d_pick(D_FOREST);
        d_str(" FORESTS");
        break;
    default:
        d_str("OCEANS");
        break;
    }
}
static void d_custom(void) {
    unsigned k = desc_rand(5);
    if (k == 3) {
        d_str("LOATHING OF ");
        d_pick(D_OBJECT);
    } else if (k == 4) {
        d_str("LOVE FOR ");
        d_pick(D_OBJECT);
    } else
        d_str(D_CUSTOM[k]);
}
static void d_feature(void) {
    switch (desc_rand(3)) {
    case 0:
        d_str("ITS ");
        d_pick(D_ANCIENT);
        d_str(" ");
        d_thing();
        break;
    case 1:
        d_str("ITS INHABITANTS' ");
        d_pick(D_INHAB);
        d_str(" ");
        d_custom();
        break;
    default:
        d_str("ITS ");
        d_pick(D_GOODADJ);
        d_str(" ");
        d_pick(D_GOODN);
        break;
    }
}
static void d_calamity(void) {
    switch (desc_rand(5)) {
    case 0:
        d_pick(D_FREQ);
        d_str(" CIVIL WAR");
        break;
    case 1:
        d_pick(D_KILLER);
        d_str(" CREATURES");
        break;
    case 2:
        d_str("A ");
        d_pick(D_KILLER);
        d_str(" DISEASE");
        break;
    case 3:
        d_pick(D_FREQ);
        d_str(" EARTHQUAKES");
        break;
    default:
        d_pick(D_FREQ);
        d_str(" SOLAR ACTIVITY");
        break;
    }
}
static void d_intens_adj_for(void) { /* "<intens><adj> FOR " */
    d_pick(D_INTENS);
    d_pick(D_ADJ);
    d_str(" FOR ");
}
static void d_predicate(void) {
    switch (desc_rand(5)) {
    case 0:
        d_intens_adj_for();
        d_feature();
        break;
    case 1:
        d_intens_adj_for();
        d_feature();
        d_str(" AND ");
        d_feature();
        break;
    case 2:
        d_pick(D_AFFLICT);
        d_str(" BY ");
        d_calamity();
        break;
    case 3:
        d_intens_adj_for();
        d_feature();
        d_str(" BUT ");
        d_pick(D_AFFLICT);
        d_str(" BY ");
        d_calamity();
        break;
    default:
        d_str("A");
        d_pick(D_DULL);
        d_str(" ");
        d_pick(D_PLACE);
        break;
    }
}

/* Build the description for system `idx` (current galaxy) into `out` (>=200 B). */
static void describe_system(int idx, char *out) {
    unsigned short s[3];
    char name[9];
    get_system_seed(idx, s);
    system_name_from_seed(s[0], s[1], s[2], name);
    desc_rng = ((unsigned long)s[1] << 16) | (unsigned long)s[2]; /* seed from s1/s2 */
    desc_len = 0;
    switch (desc_rand(5)) { /* subject (Elite tokens 86-90) */
    case 1:
        d_str("THE PLANET ");
        d_str(name[0] ? name : "?");
        break;
    case 2:
        d_str("THE WORLD ");
        d_str(name[0] ? name : "?");
        break;
    case 3:
        d_str("THIS PLANET");
        break;
    case 4:
        d_str("THIS WORLD");
        break;
    default:
        d_str(name[0] ? name : "?");
        break;
    }
    d_str(" IS ");
    d_predicate();
    d_str(".");
    for (int i = 0; i < desc_len; i++)
        out[i] = desc_buf[i];
    out[desc_len] = '\0';
}

#define DESC_HASH 0x7F20
static int desc_self_test(void) {
    unsigned h = 0;
    char buf[200];
    for (int idx = 0; idx < 8; idx++) {
        describe_system(idx, buf);
        for (int j = 0; buf[j]; j++)
            h = (h * 31u + (unsigned char)buf[j]) & 0xFFFFu;
        h = (h * 31u) & 0xFFFFu;
    }
    return h == DESC_HASH;
}

/* Forward declarations: witchspace_tick (below) re-runs scene_init / stars_init
 * / planet_init / sun_init / station_init after the witchspace flash; those
 * bodies are defined later in this file. */
static void scene_init(void);
static void stars_init(void);
static void planet_init(void);
static void sun_init(void);
static void station_init(void);
static void market_gen(void);

/* Galactic chart
 *
 * Render mode: when chart_mode is on, the firmware suspends MVEIT/ship-AI/
 * combat and draws all 256 systems of the current galaxy as single pixels
 * over the space view region. Each system's (x, y) (both 0..255) maps to
 * (x/2, y/3) — 256/2 = 128 px wide, 256/3 ~= 85 px tall, fits in viewH=96
 * with headroom for a frame.
 *
 * Cursor: a small cross at (cursor_x, cursor_y) the player nudges via the
 * stick (roll/pitch deltas in chart mode). Each frame we find the system
 * closest to the cursor and call it the new target_sys; that system is
 * highlighted with a small box and is the destination if the player
 * triggers hyperspace. */

#define CHART_X_SCALE 2 /* x/CHART_X_SCALE maps to 0..127 */
#define CHART_Y_SCALE 3 /* y/CHART_Y_SCALE maps to 0..85  */

static int chart_dot_x(int sx) { return sx / CHART_X_SCALE; }
static int chart_dot_y(int sy) { return sy / CHART_Y_SCALE; }

/* Find the system whose chart-dot lies closest to (cx, cy). Linear scan over
 * 256 systems, using the same enumeration as chart drawing. The nearest
 * system is selected by Manhattan distance. */
static int nearest_system(int cx, int cy) {
    unsigned short s[3];
    galaxy_init(current_galaxy);
    s[0] = galaxy_seed[0];
    s[1] = galaxy_seed[1];
    s[2] = galaxy_seed[2];
    int best = 0, best_d = 1 << 30;
    for (int i = 0; i < 256; i++) {
        galaxy_step(s);
        system_t sys;
        galaxy_extract(s, &sys);
        int dx = chart_dot_x(sys.x) - cx;
        int dy = chart_dot_y(sys.y) - cy;
        if (dx < 0)
            dx = -dx;
        if (dy < 0)
            dy = -dy;
        int d = dx + dy;
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    return best;
}

/* Get the (chart_dot_x, chart_dot_y) of the i-th system in the current galaxy. */
static void chart_pos_for_system(int idx, int *out_x, int *out_y) {
    unsigned short s[3];
    galaxy_init(current_galaxy);
    s[0] = galaxy_seed[0];
    s[1] = galaxy_seed[1];
    s[2] = galaxy_seed[2];
    system_t sys = {0, 0, 0, 0, 0, 0};
    for (int i = 0; i <= idx; i++) {
        galaxy_step(s);
        galaxy_extract(s, &sys);
    }
    *out_x = chart_dot_x(sys.x);
    *out_y = chart_dot_y(sys.y);
}

static void draw_chart_dots(void) {
    unsigned short s[3];
    galaxy_init(current_galaxy);
    s[0] = galaxy_seed[0];
    s[1] = galaxy_seed[1];
    s[2] = galaxy_seed[2];
    for (int i = 0; i < 256; i++) {
        galaxy_step(s);
        system_t sys;
        galaxy_extract(s, &sys);
        int cx = chart_dot_x(sys.x);
        int cy = chart_dot_y(sys.y);
        if (cx >= 0 && cx < 128 && cy >= 0 && cy < VIEW_H_PX)
            plot_point(cx, cy);
    }
}

/* Render an integer as 3-digit decimal at (x, y) using the 8x8 font. */
static void text_print_num3(int x, int y, int n) {
    if (n < 0)
        n = 0;
    if (n > 999)
        n = 999;
    char buf[4];
    buf[0] = '0' + (n / 100);
    buf[1] = '0' + ((n / 10) % 10);
    buf[2] = '0' + (n % 10);
    buf[3] = 0;
    text_print(x, y, buf);
}

/* 4-digit decimal (for equipment prices in whole credits, up to 9999). */
static void text_print_num4(int x, int y, int n) {
    if (n < 0)
        n = 0;
    if (n > 9999)
        n = 9999;
    char buf[5];
    buf[0] = '0' + (n / 1000);
    buf[1] = '0' + ((n / 100) % 10);
    buf[2] = '0' + ((n / 10) % 10);
    buf[3] = '0' + (n % 10);
    buf[4] = 0;
    text_print(x, y, buf);
}

static void draw_chart(void) {
    /* Border + dots */
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);
    /* Top-left: galaxy header. Top-right: the selected target's name, which
     * updates live as the cursor moves over the chart. */
    text_print(3, 2, "GAL");
    text_print_num3(28, 2, current_galaxy);
    char name[9];
    system_name(target_sys, name);
    text_print(58, 2, name[0] ? name : "?");
    draw_chart_dots();

    /* Highlight current system: small filled square */
    int cx, cy;
    chart_pos_for_system(current_sys, &cx, &cy);
    for (int dx = -1; dx <= 1; dx++)
        for (int dy = -1; dy <= 1; dy++)
            plot_point(cx + dx, cy + dy);

    /* Highlight target system: ring around it */
    int tx, ty;
    chart_pos_for_system(target_sys, &tx, &ty);
    plot_line(tx - 3, ty - 3, tx + 3, ty - 3, 1);
    plot_line(tx - 3, ty + 3, tx + 3, ty + 3, 1);
    plot_line(tx - 3, ty - 3, tx - 3, ty + 3, 1);
    plot_line(tx + 3, ty - 3, tx + 3, ty + 3, 1);

    /* Cursor cross */
    plot_line(cursor_x - 4, cursor_y, cursor_x + 4, cursor_y, 1);
    plot_line(cursor_x, cursor_y - 4, cursor_x, cursor_y + 4, 1);
}

static void chart_step(unsigned in) {
    int roll = sb(in, 0);
    int pitch = sb(in, 8);
    cursor_x = clampi(cursor_x + roll, 4, 123);
    cursor_y = clampi(cursor_y + pitch, 4, VIEW_H_PX - 5);
    target_sys = nearest_system(cursor_x, cursor_y);
}

/* System-data screen (chart_mode == CHART_DATA): inspect the TARGET system's
 * procedurally-generated profile before committing to a jump. */
static const char *ECONOMY_NAMES[8] = {"RICH IND",  "AVG IND",   "POOR IND", "MAIN IND",
                                       "MAIN AGRI", "RICH AGRI", "AVG AGRI", "POOR AGRI"};
static const char *GOV_NAMES[8] = {"ANARCHY",   "FEUDAL", "MULTI-GOV", "DICTATOR",
                                   "COMMUNIST", "CONFED", "DEMOCRACY", "CORPORATE"};

/* Word-wrapped text from (x0, y), wrapping at the right border, line height 9,
 * stopping when it runs out of the view region. Splits on spaces. */
static void text_print_wrap(int x0, int y, const char *s) {
    int x = x0;
    char word[40];
    while (*s) {
        while (*s == ' ')
            s++;
        int wl = 0;
        while (*s && *s != ' ' && wl < 39)
            word[wl++] = *s++;
        word[wl] = '\0';
        if (wl == 0)
            continue;
        if (x + wl * GLYPH_W > 124) {
            x = x0;
            y += 9;
        } /* wrap to next line */
        if (y > VIEW_H_PX - 9)
            break; /* no room left */
        text_print(x, y, word);
        x += (wl + 1) * GLYPH_W; /* word + a space */
    }
}

static void draw_sysdata(void) {
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);

    char name[9], desc[200];
    system_t sys;
    get_system(target_sys, &sys);
    system_name(target_sys, name);
    text_print(3, 2, name[0] ? name : "?"); /* the system's procedural name */
    text_print(82, 2, "GAL");
    text_print_num3(106, 2, current_galaxy);

    /* Compact stat block (3 lines), then the procedural description fills the
     * lower two-thirds of the view. */
    text_print(3, 12, ECONOMY_NAMES[sys.economy & 7]);
    text_print(3, 21, GOV_NAMES[sys.government & 7]);
    text_print(3, 30, "POP");
    text_print_num3(28, 30, sys.population);
    text_print(64, 30, "PRD");
    text_print_num3(88, 30, sys.productivity);
    plot_line(3, 40, 124, 40, 1);

    describe_system(target_sys, desc);
    text_print_wrap(3, 45, desc);
}

/* Hyperspace */
static void witchspace_jump(void) {
    if (fuel < HYPERSPACE_COST)
        return;
    if (target_sys == current_sys)
        return;
    fuel -= HYPERSPACE_COST;
    current_sys = target_sys;
    /* Flush the bubble + start the witchspace flash; the new system's scene
     * gets re-seeded on flash exit so the player blinks into a new layout. */
    res2();
    hyper_flash = HYPER_FLASH_FRAMES;
    chart_mode = 0;
    sfx_play(SFX_HYPER, HYPER_FLASH_FRAMES);
}

/* Galactic Hyperdrive: a one-shot jump to
 * the next of the 8 galaxies. Consumes the EQ_GALHYPER fit (like BBC Elite),
 * needs no fuel, and lands at system 0 of the new galaxy. */
static void galactic_jump(void) {
    if (!(equip_owned & EQ_GALHYPER))
        return;
    equip_owned &= ~EQ_GALHYPER;
    current_galaxy = (current_galaxy + 1) & 7;
    current_sys = 0;
    target_sys = 0;
    res2();
    hyper_flash = HYPER_FLASH_FRAMES;
    chart_mode = 0;
    sfx_play(SFX_HYPER, HYPER_FLASH_FRAMES);
}

static void witchspace_tick(void) {
    if (hyper_flash <= 0)
        return;
    hyper_flash--;
    if (hyper_flash == 0) {
        /* Land in the new system: rebuild scene + stars + planet/sun/station,
         * perturb the LCG seed so the procedural scene is visibly different. */
        rng = 0xACE1u ^ (unsigned)(current_sys * 0x9E3779B1u) ^
              (unsigned)(current_galaxy * 0x85EBCA6Bu);
        scene_init();
        stars_init();
        planet_init();
        sun_init();
        station_init();
        market_gen(); /* new system => fresh market prices/quantities */
    }
}

static void draw_witchspace_flash(void) {
    if (hyper_flash <= 0)
        return;
    /* Fill the space view with diagonal flashes for the witchspace tunnel
     * read. Cheap stylisation — 8 lines from corners to centre. */
    for (int i = 0; i < 8; i++) {
        int x = (i * 16);
        plot_line(x, 0, CX, CY, 1);
        plot_line(x, VIEW_H_PX - 1, CX, CY, 1);
    }
    text_print(CX - 30, 8, "WITCHSPACE");
}

static void draw_death_overlay(void) {
    if (dying <= 0)
        return;
    text_print(CX - 27, CY - 4, "GAME OVER");
}

static void draw_save_flash(void) {
    if (save_flash == 0)
        return;
    text_print(CX - 15, 8, save_flash > 0 ? "SAVED" : "LOADED");
    if (save_flash > 0)
        save_flash--;
    else
        save_flash++;
}

/* Trading economy
 *
 * Elite's market model, ported behaviour-not-binary (this project's own seeds
 * drive the galaxy, so absolute numbers differ from BBC Elite; the *formula*
 * is faithful). Each system's market is generated from its economy index and a
 * per-system fluctuation byte (its productivity tap), so it's stable for the
 * duration of a visit and regenerates on hyperspace arrival.
 *
 *   price_units = (base + economy*gradient + (fluct & mask)) & 0xFF
 *   price       = price_units * 4        (tenths of a credit)
 *   quantity    = clamp(base_qty - economy*gradient + (fluct & mask), 0, 63)
 *
 * Gradient is signed: agricultural goods get cheaper / more plentiful as the
 * economy index climbs toward poor-agricultural, industrial goods the reverse.
 * The oracle tools/oracle/market.py mirrors this bit-for-bit. */
#define NGOODS 17
#define CARGO_MAX 20 /* tonnes of hold space */

typedef struct {
    const char *abbr;   /* 3-char display tag */
    short base;         /* base price, 0..255 */
    signed char grad;   /* economic gradient (signed) */
    unsigned char bqty; /* base availability, pre-clamp */
    unsigned char mask; /* AND-mask applied to the fluctuation byte */
} good_t;

static const good_t goods[NGOODS] = {
    {"FOO", 19, -2, 12, 0x01},  /* Food        */
    {"TEX", 20, -1, 14, 0x03},  /* Textiles    */
    {"RAD", 65, -3, 8, 0x07},   /* Radioactives*/
    {"SLV", 40, -5, 30, 0x1F},  /* Slaves      */
    {"LIQ", 83, -5, 28, 0x0F},  /* Liquor/Wines*/
    {"LUX", 196, 8, 6, 0x03},   /* Luxuries    */
    {"NAR", 235, 4, 4, 0x38},   /* Narcotics   */
    {"COM", 154, 14, 18, 0x03}, /* Computers   */
    {"MAC", 117, 6, 20, 0x07},  /* Machinery   */
    {"ALY", 78, 1, 24, 0x1F},   /* Alloys      */
    {"FIR", 124, 13, 12, 0x07}, /* Firearms    */
    {"FUR", 176, -9, 30, 0x3F}, /* Furs        */
    {"MIN", 32, -1, 40, 0x07},  /* Minerals    */
    {"GLD", 97, -1, 35, 0x1F},  /* Gold        */
    {"PLT", 171, -2, 32, 0x0F}, /* Platinum    */
    {"GEM", 45, -1, 16, 0x07},  /* Gem-Stones  */
    {"ALN", 250, 15, 2, 0x07},  /* Alien Items */
};

static int price_tenths[NGOODS];         /* current market price, 0.1 Cr */
static unsigned char market_qty[NGOODS]; /* units available to buy, 0..63 */
static unsigned char cargo[NGOODS];      /* tonnes held by the player */
static long credits = 1000;              /* in 0.1 Cr; start 100.0 Cr */
static unsigned kills = 0;               /* combat kills (commander rating) */
static int cargo_cap = CARGO_MAX;        /* grows to LARGE_CARGO_CAP on upgrade */
static int legal_status = 0;             /* Elite's FIST: 0=Clean, 1-49=Offender, 50+=Fugitive */

/* Commit a crime (kill an innocent): raise the legal-status byte, clamped. */
static void commit_crime(int n) { legal_status = clampi(legal_status + n, 0, 255); }
/* Elite's FIST bands: 0 = Clean, 1-49 = Offender, 50+ = Fugitive. */
static int legal_class(int v) { return v == 0 ? 0 : v < 50 ? 1 : 2; }
static const char *const LEGAL_NAMES[3] = {"CLEAN", "OFFENDER", "FUGITIVE"};
static const char *legal_name(void) { return LEGAL_NAMES[legal_class(legal_status)]; }

/* Boot self-test (mirrored by tools/oracle/status.py): the legal-status band
 * boundaries. Folded into the DBG sentinel so threshold drift fails the boot. */
static int status_self_test(void) {
    return legal_class(0) == 0 && legal_class(1) == 1 && legal_class(49) == 1 &&
           legal_class(50) == 2 && legal_class(255) == 2;
}

/* Mission 1: hunt the Constrictor
 *
 * The classic Elite mission: a stolen prototype with reinforced shields is
 * hiding in a system; track it down and destroy it for a bounty. The hunt is
 * offered from the start (mission_target_sys is the home system for the demo);
 * the Constrictor spawns into that system's scene, and destroying it pays out
 * and closes the mission for good. */
enum { MISSION_NONE = 0, MISSION_ACTIVE = 1, MISSION_DONE = 2 };
#define MISSION_BOUNTY 50000 /* 5000.0 Cr */
static int mission_state = MISSION_ACTIVE;
static int mission_target_sys = 0; /* the Constrictor's hideout */
static int mission_flash = 40;     /* briefing/debrief overlay timer (briefing at boot) */

/* Destroying the target is the only way out of ACTIVE; DONE/NONE are terminal. */
static int mission_next(int state) { return state == MISSION_ACTIVE ? MISSION_DONE : state; }
static int mission_self_test(void) {
    return mission_next(MISSION_ACTIVE) == MISSION_DONE &&
           mission_next(MISSION_DONE) == MISSION_DONE &&
           mission_next(MISSION_NONE) == MISSION_NONE && MISSION_BOUNTY > 0;
}

/* Briefing (while hunting) / debrief (on success) overlay, shown for a few
 * frames after the mission is offered and again when the target is destroyed. */
static void draw_mission_overlay(void) {
    if (mission_flash <= 0)
        return;
    mission_flash--;
    if (mission_state == MISSION_DONE) {
        text_print(CX - 48, 6, "TARGET DESTROYED");
        text_print(CX - 42, 16, "WELL DONE CMDR");
    } else if (mission_state == MISSION_ACTIVE) {
        char name[9];
        system_name(mission_target_sys, name);
        text_print(CX - 48, 6, "PRIORITY MISSION");
        text_print(CX - 48, 16, "HUNT CONSTRICTOR");
        text_print(CX - 24, 26, name[0] ? name : "?");
    }
}

static int market_mode = 0;
static int market_sel = 0;
static int market_cooldown = 0;
static int market_btn_prev = 0;
static int buy_btn_prev = 0;
static int sell_btn_prev = 0;

/* (Re)generate the market for the current system. */
static void market_gen(void) {
    system_t sys;
    get_system(current_sys, &sys);
    int econ = sys.economy;       /* 0..7 */
    int fluct = sys.productivity; /* 0..255, fixed per system */
    for (int i = 0; i < NGOODS; i++) {
        int g = goods[i].grad;
        int pu = (goods[i].base + econ * g + (fluct & goods[i].mask)) & 0xFF;
        price_tenths[i] = pu * 4;
        int q = goods[i].bqty - econ * g + (fluct & goods[i].mask);
        if (q < 0)
            q = 0;
        if (q > 63)
            q = 63;
        market_qty[i] = (unsigned char)q;
    }
}

static int cargo_used(void) {
    int t = 0;
    for (int i = 0; i < NGOODS; i++)
        t += cargo[i];
    return t;
}

static void market_buy(int i) {
    if (i < 0 || i >= NGOODS)
        return;
    if (market_qty[i] == 0)
        return;
    if (cargo_used() >= cargo_cap)
        return;
    if (credits < price_tenths[i])
        return;
    credits -= price_tenths[i];
    cargo[i]++;
    market_qty[i]--;
}

static void market_sell(int i) {
    if (i < 0 || i >= NGOODS)
        return;
    if (cargo[i] == 0)
        return;
    credits += price_tenths[i];
    cargo[i]--;
    market_qty[i]++;
}

/* Commander rating: kill tally -> Elite's nine-rung ladder (own thresholds). */
static const char *RATINGS[9] = {"HARMLESS",  "MOSTLY HARMLESS", "POOR",
                                 "AVERAGE",   "ABOVE AVERAGE",   "COMPETENT",
                                 "DANGEROUS", "DEADLY",          "ELITE"};
static const unsigned RATING_KILLS[9] = {0, 2, 4, 8, 16, 32, 64, 128, 256};

static int commander_rating(void) {
    int r = 0;
    for (int i = 0; i < 9; i++)
        if (kills >= RATING_KILLS[i])
            r = i;
    return r;
}

/* Commander save / load
 *
 * Ports Elite's commander-file idea: the persistent player state is packed
 * into a fixed little-endian byte layout with a tamper/corruption checksum
 * appended, exactly as Elite saved the commander to tape/disk. On a real
 * board this buffer would live in battery-backed RAM or be streamed to host
 * storage; in sim it lives in RAM, which is enough to exercise the format
 * and the checksum 1:1.
 *
 * Layout (CMDR_* byte offsets), all multi-byte fields little-endian:
 *   [0..3]   credits      (32-bit, tenths of a credit)
 *   [4..5]   kills         (16-bit commander rating tally)
 *   [6]      fuel
 *   [7]      cargo_cap
 *   [8]      missiles
 *   [9]      laser_tier
 *   [10]     equip_owned  (7-bit equipment bitmask)
 *   [11]     current_sys
 *   [12]     current_galaxy
 *   [13]     target_sys
 *   [14]     legal_status   (Elite's FIST)
 *   [15]     player_ship    (current hull index)
 *   [16]     mission_state  (mission 1 progress)
 *   [17..33] cargo[NGOODS]  (17 tonnes-held bytes)
 *   [34]     checksum      (CMDR_LEN-1 == CMDR_SUM)
 */
#define CMDR_CREDITS 0
#define CMDR_KILLS 4
#define CMDR_FUEL 6
#define CMDR_CARGOCAP 7
#define CMDR_MISSILES 8
#define CMDR_LASER 9
#define CMDR_EQUIP 10
#define CMDR_SYS 11
#define CMDR_GALAXY 12
#define CMDR_TARGET 13
#define CMDR_LEGAL 14
#define CMDR_SHIP 15
#define CMDR_MISSION 16
#define CMDR_CARGO 17
#define CMDR_SUM (CMDR_CARGO + NGOODS) /* 34 */
#define CMDR_LEN (CMDR_SUM + 1)        /* 35 bytes incl. checksum */

/* The persistent commander state, gathered out of the live globals so the
 * serializer has one struct to walk (no by-value struct copies anywhere here
 * -> no implicit memcpy in the freestanding build; see the gotchas note). */
typedef struct {
    long credits;
    unsigned kills;
    int fuel, cargo_cap, missiles, laser_tier;
    unsigned equip_owned;
    int current_sys, current_galaxy, target_sys, legal_status, player_ship;
    int mission_state;
    unsigned char cargo[NGOODS];
} cmdr_t;

static void cmdr_capture(cmdr_t *c) {
    c->credits = credits;
    c->kills = kills;
    c->fuel = fuel;
    c->cargo_cap = cargo_cap;
    c->missiles = missiles;
    c->laser_tier = laser_tier;
    c->equip_owned = equip_owned;
    c->current_sys = current_sys;
    c->current_galaxy = current_galaxy;
    c->target_sys = target_sys;
    c->legal_status = legal_status;
    c->player_ship = player_ship;
    c->mission_state = mission_state;
    for (int i = 0; i < NGOODS; i++)
        c->cargo[i] = cargo[i];
}

static void cmdr_apply(const cmdr_t *c) {
    credits = c->credits;
    kills = c->kills;
    fuel = c->fuel;
    cargo_cap = c->cargo_cap;
    missiles = c->missiles;
    laser_tier = c->laser_tier;
    equip_owned = c->equip_owned;
    current_sys = c->current_sys;
    current_galaxy = c->current_galaxy;
    target_sys = c->target_sys;
    legal_status = c->legal_status;
    player_ship = c->player_ship;
    mission_state = c->mission_state;
    for (int i = 0; i < NGOODS; i++)
        cargo[i] = c->cargo[i];
}

/* Position-sensitive additive checksum: seed with the data length, then for
 * each byte add it into the accumulator and rotate the accumulator left by one.
 * Both the add (mod 256) and the rotate are bijections on the 8-bit state, so a
 * one-byte change shifts the running value and can never cancel back out — every
 * single-byte corruption is therefore detected, and the rotate makes the fold
 * order-sensitive so most byte transpositions are caught too. (Elite seeded its
 * CHECK routine with the data length and folded with add-then-EOR; this is the
 * same spirit, swapping the EOR for a rotate to get a strictly stronger
 * detector.) Any failed check rejects the load rather than silently accepting. */
static unsigned char cmdr_checksum(const unsigned char *buf) {
    unsigned c = CMDR_SUM; /* seed with the data length, Elite-style */
    for (int i = 0; i < CMDR_SUM; i++) {
        c = (c + buf[i]) & 0xFFu;
        c = ((c << 1) | (c >> 7)) & 0xFFu;
    }
    return (unsigned char)c;
}

static void put16(unsigned char *p, unsigned v) {
    p[0] = (unsigned char)(v & 0xFFu);
    p[1] = (unsigned char)((v >> 8) & 0xFFu);
}
static void put32(unsigned char *p, unsigned long v) {
    p[0] = (unsigned char)(v & 0xFFu);
    p[1] = (unsigned char)((v >> 8) & 0xFFu);
    p[2] = (unsigned char)((v >> 16) & 0xFFu);
    p[3] = (unsigned char)((v >> 24) & 0xFFu);
}
static unsigned get16(const unsigned char *p) { return (unsigned)p[0] | ((unsigned)p[1] << 8); }
static unsigned long get32(const unsigned char *p) {
    return (unsigned long)p[0] | ((unsigned long)p[1] << 8) | ((unsigned long)p[2] << 16) |
           ((unsigned long)p[3] << 24);
}

static void cmdr_serialize(const cmdr_t *c, unsigned char *buf) {
    put32(buf + CMDR_CREDITS, (unsigned long)c->credits);
    put16(buf + CMDR_KILLS, c->kills & 0xFFFFu);
    buf[CMDR_FUEL] = (unsigned char)c->fuel;
    buf[CMDR_CARGOCAP] = (unsigned char)c->cargo_cap;
    buf[CMDR_MISSILES] = (unsigned char)c->missiles;
    buf[CMDR_LASER] = (unsigned char)c->laser_tier;
    buf[CMDR_EQUIP] = (unsigned char)(c->equip_owned & 0xFFu);
    buf[CMDR_SYS] = (unsigned char)c->current_sys;
    buf[CMDR_GALAXY] = (unsigned char)c->current_galaxy;
    buf[CMDR_TARGET] = (unsigned char)c->target_sys;
    buf[CMDR_LEGAL] = (unsigned char)c->legal_status;
    buf[CMDR_SHIP] = (unsigned char)c->player_ship;
    buf[CMDR_MISSION] = (unsigned char)c->mission_state;
    for (int i = 0; i < NGOODS; i++)
        buf[CMDR_CARGO + i] = c->cargo[i];
    buf[CMDR_SUM] = cmdr_checksum(buf);
}

/* Returns 1 and fills *c if the checksum matches; 0 (and leaves *c untouched)
 * on a corrupt/tampered buffer. */
static int cmdr_deserialize(const unsigned char *buf, cmdr_t *c) {
    if (buf[CMDR_SUM] != cmdr_checksum(buf))
        return 0;
    c->credits = (long)get32(buf + CMDR_CREDITS);
    c->kills = get16(buf + CMDR_KILLS);
    c->fuel = buf[CMDR_FUEL];
    c->cargo_cap = buf[CMDR_CARGOCAP];
    c->missiles = buf[CMDR_MISSILES];
    c->laser_tier = buf[CMDR_LASER];
    c->equip_owned = buf[CMDR_EQUIP];
    c->current_sys = buf[CMDR_SYS];
    c->current_galaxy = buf[CMDR_GALAXY];
    c->target_sys = buf[CMDR_TARGET];
    c->legal_status = buf[CMDR_LEGAL];
    c->player_ship = buf[CMDR_SHIP];
    c->mission_state = buf[CMDR_MISSION];
    for (int i = 0; i < NGOODS; i++)
        c->cargo[i] = buf[CMDR_CARGO + i];
    return 1;
}

/* The single save slot (the "commander file"). save_valid guards a load
 * before anything has been saved this session. */
static unsigned char cmdr_slot[CMDR_LEN];
static int cmdr_slot_valid = 0;

static void cmdr_save(void) {
    cmdr_t c;
    cmdr_capture(&c);
    cmdr_serialize(&c, cmdr_slot);
    cmdr_slot_valid = 1;
}

static int cmdr_load(void) {
    cmdr_t c;
    if (!cmdr_slot_valid)
        return 0;
    if (!cmdr_deserialize(cmdr_slot, &c))
        return 0;
    cmdr_apply(&c);
    apply_ship_stats(); /* recompute speed from the loaded hull */
    return 1;
}

/* Boot self-test, mirrored bit-for-bit by tools/oracle/cmdr.py: round-trip a
 * known commander through serialize/deserialize, and confirm a one-byte
 * corruption is rejected by the checksum. Folded into the DBG sentinel
 * alongside galaxy_self_test so every SocSim that asserts DBG==0xCAFE also
 * gates save/load correctness. Operates entirely on locals — never touches
 * the live globals. */
static int cmdr_self_test(void) {
    cmdr_t a, b;
    unsigned char buf[CMDR_LEN];
    a.credits = 123456;
    a.kills = 0xBEEF;
    a.fuel = 55;
    a.cargo_cap = 35;
    a.missiles = 4;
    a.laser_tier = 3;
    a.equip_owned = 0x6D;
    a.current_sys = 200;
    a.current_galaxy = 7;
    a.target_sys = 13;
    a.legal_status = 55;
    a.player_ship = 2;
    a.mission_state = 1;
    for (int i = 0; i < NGOODS; i++)
        a.cargo[i] = (unsigned char)(i * 3 + 1);

    cmdr_serialize(&a, buf);
    if (!cmdr_deserialize(buf, &b))
        return 0; /* clean buffer loads */
    if (b.credits != a.credits || b.kills != a.kills || b.fuel != a.fuel ||
        b.cargo_cap != a.cargo_cap || b.missiles != a.missiles || b.laser_tier != a.laser_tier ||
        b.equip_owned != a.equip_owned || b.current_sys != a.current_sys ||
        b.current_galaxy != a.current_galaxy || b.target_sys != a.target_sys ||
        b.legal_status != a.legal_status || b.player_ship != a.player_ship ||
        b.mission_state != a.mission_state)
        return 0;
    for (int i = 0; i < NGOODS; i++)
        if (b.cargo[i] != a.cargo[i])
            return 0;

    buf[CMDR_CARGO] ^= 0x80u; /* corrupt one byte */
    if (cmdr_deserialize(buf, &b))
        return 0; /* must be rejected */
    return 1;
}

/* Commander status screen
 *
 * Port of Elite's STATUS / "red key f8" screen: the commander's name, the
 * present + hyperspace system names, flight condition, fuel, cash, legal
 * status, combat rating, and the fitted-equipment list. Toggled by INPUT2 bit4.
 * COMMANDER_NAME is fixed (no name entry) — JAMESON, the default Elite name. */
#define COMMANDER_NAME "JAMESON"

/* GREEN clear / YELLOW hostiles present / RED under fire — Elite's condition. */
static const char *condition_name(void) {
    if (hit_flash > 0)
        return "RED";
    for (int i = 0; i < NSHIPS_MAX; i++)
        if (bubble[i].type == SHIP_ALIVE && bubble[i].behavior == BEHAVIOR_HOSTILE)
            return "YELLOW";
    return "GREEN";
}

static void draw_status(void) {
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);

    char name[9];
    text_print(3, 2, "COMMANDER " COMMANDER_NAME);

    system_name(current_sys, name);
    text_print(3, 14, "PRESENT");
    text_print(58, 14, name[0] ? name : "?");
    system_name(target_sys, name);
    text_print(3, 23, "HYPERSP");
    text_print(58, 23, name[0] ? name : "?");

    text_print(3, 32, "CONDITION");
    text_print(64, 32, condition_name());
    text_print(3, 41, "FUEL");
    text_print_num3(40, 41, fuel);
    text_print(70, 41, "CR");
    text_print_num4(88, 41, (int)(credits / 10)); /* whole credits */
    text_print(3, 50, "LEGAL");
    text_print(46, 50, legal_name());
    text_print(3, 59, "RATING");
    text_print(52, 59, RATINGS[commander_rating()]);

    plot_line(3, 68, 124, 68, 1);

    /* Mission status (if any), then fitted equipment one item per line (clipped
     * to the view). The pulse laser is always present; the rest depend on
     * equip_owned / counts. */
    int y = 70;
    if (mission_state == MISSION_ACTIVE) {
        text_print(3, y, "MISN HUNT CONSTR");
        y += 8;
    } else if (mission_state == MISSION_DONE) {
        text_print(3, y, "MISN COMPLETE");
        y += 8;
    }
    text_print(3, y,
               laser_tier >= 3   ? "MILITARY LASER"
               : laser_tier == 2 ? "BEAM LASER"
                                 : "PULSE LASER");
    y += 8;
    if (missiles > 0) {
        text_print(3, y, "MISSILES");
        text_print_num3(72, y, missiles);
        y += 8;
    }
    if (cargo_cap > CARGO_MAX) {
        text_print(3, y, "LARGE CARGO BAY");
        y += 8;
    }
    if (equip_owned & EQ_ECM) {
        text_print(3, y, "ECM SYSTEM");
        y += 8;
    }
    if (equip_owned & EQ_FUELSCOOP) {
        text_print(3, y, "FUEL SCOOPS");
        y += 8;
    }
    if (equip_owned & EQ_ESCAPEPOD) {
        text_print(3, y, "ESCAPE POD");
        y += 8;
    }
    if (equip_owned & EQ_ENERGYBOMB) {
        text_print(3, y, "ENERGY BOMB");
        y += 8;
    }
    if (equip_owned & EQ_ENERGYUNIT) {
        text_print(3, y, "ENERGY UNIT");
        y += 8;
    }
    if (equip_owned & EQ_DOCKCOMP) {
        text_print(3, y, "DOCK COMPUTER");
        y += 8;
    }
    if (equip_owned & EQ_GALHYPER) {
        text_print(3, y, "GAL HYPERDRIVE");
        y += 8;
    }
}

static void market_step(unsigned in) {
    /* Move the selection with the pitch axis, rate-limited so one nudge steps
     * one row rather than racing through the list. */
    int pitch = sb(in, 8);
    if (market_cooldown > 0)
        market_cooldown--;
    if (market_cooldown == 0 && pitch != 0) {
        market_sel += signum(pitch);
        if (market_sel < 0)
            market_sel = 0;
        if (market_sel >= NGOODS)
            market_sel = NGOODS - 1;
        market_cooldown = 6;
    }
    int buy_btn = (in >> (24 + 5)) & 1;
    int sell_btn = (in >> (24 + 6)) & 1;
    if (buy_btn && !buy_btn_prev)
        market_buy(market_sel);
    if (sell_btn && !sell_btn_prev)
        market_sell(market_sel);
    buy_btn_prev = buy_btn;
    sell_btn_prev = sell_btn;
}

static void draw_market(void) {
    /* Border */
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);

    /* Header: cash (Cr) + commander rating index marker */
    text_print(3, 2, "CR");
    text_print_num3(18, 2, (int)(credits / 10));
    text_print(64, 2, RATINGS[commander_rating()]);

    /* Two columns of goods: left 0..8, right 9..16. Each cell "ABR ppp" with
     * ppp = price in whole credits. Selected cell gets a bracket box. */
    for (int i = 0; i < NGOODS; i++) {
        int col = (i < 9) ? 0 : 1;
        int row = (i < 9) ? i : (i - 9);
        int x = col ? 66 : 3;
        int y = 12 + row * 8;
        text_print(x, y, goods[i].abbr);
        text_print_num3(x + 22, y, price_tenths[i] / 10);
        if (i == market_sel) {
            plot_line(x - 2, y - 1, x + 44, y - 1, 1);
            plot_line(x - 2, y + 7, x + 44, y + 7, 1);
            plot_line(x - 2, y - 1, x - 2, y + 7, 1);
            plot_line(x + 44, y - 1, x + 44, y + 7, 1);
        }
    }

    /* Footer: selected good's availability + held cargo. */
    text_print(3, 86, "AV");
    text_print_num3(18, 86, market_qty[market_sel]);
    text_print(46, 86, "HD");
    text_print_num3(61, 86, cargo[market_sel]);
    text_print(92, 86, "T");
    text_print_num3(100, 86, cargo_used());
}

/* Equip Ship
 *
 * Elite's outfitter: 13 upgrades bought against `credits`. Each item is one of
 * a few kinds — refuel, buy a missile, expand the cargo bay, upgrade the laser
 * tier, or a one-shot piece of kit (ECM, fuel scoops, escape pod, energy bomb,
 * extra energy unit, docking computer, galactic hyperdrive). Purchases are
 * gated on cash and on not re-buying something you already have / can't use.
 * Effects wire into the rest of the sim: cargo bay -> market hold capacity,
 * laser tier -> combat damage, docking computer -> enables the DOCKIT autopilot,
 * energy unit -> faster regen, fuel -> tops up the hyperdrive tank.
 *
 * Prices are tenths of a credit (like the market), shown on screen in whole Cr.
 * tools/oracle/equip.py mirrors the table + purchase gates bit-for-bit. */
#define NEQUIP 13
#define LARGE_CARGO_CAP 35

enum eq_kind { EK_FUEL, EK_MISSILE, EK_CARGO, EK_LASER, EK_FLAG };

typedef struct {
    const char *name;   /* <=5 chars for the two-column layout */
    long price;         /* tenths of a credit */
    unsigned char kind; /* eq_kind */
    unsigned arg;       /* EK_LASER: target tier; EK_FLAG: ownership bit; else 0 */
} equip_t;

static const equip_t equip[NEQUIP] = {
    {"FUEL", 140, EK_FUEL, 0},                /*   14.0 Cr — refill tank   */
    {"MISSL", 300, EK_MISSILE, 0},            /*   30.0 Cr — one missile   */
    {"CARGO", 4000, EK_CARGO, 0},             /*  400.0 Cr — large bay     */
    {"ECM", 6000, EK_FLAG, EQ_ECM},           /*  600.0 Cr                 */
    {"BEAM", 10000, EK_LASER, 2},             /* 1000.0 Cr — beam laser    */
    {"SCOOP", 5250, EK_FLAG, EQ_FUELSCOOP},   /*  525.0 Cr                 */
    {"EPOD", 10000, EK_FLAG, EQ_ESCAPEPOD},   /* 1000.0 Cr                 */
    {"EBOMB", 9000, EK_FLAG, EQ_ENERGYBOMB},  /*  900.0 Cr                 */
    {"EUNIT", 15000, EK_FLAG, EQ_ENERGYUNIT}, /* 1500.0 Cr                 */
    {"DOCK", 15000, EK_FLAG, EQ_DOCKCOMP},    /* 1500.0 Cr — docking comp  */
    {"GHYP", 50000, EK_FLAG, EQ_GALHYPER},    /* 5000.0 Cr                 */
    {"MILSR", 60000, EK_LASER, 3},            /* 6000.0 Cr — military      */
    {"MINSR", 8000, EK_LASER, 2},             /*  800.0 Cr — mining        */
};

static int equip_mode = 0;
static int equip_sel = 0;
static int equip_cooldown = 0;
static int equip_btn_prev = 0;
static int equip_buy_prev = 0;

/* Can item i be bought right now (ignoring cash)? */
static int equip_available(int i) {
    const equip_t *e = &equip[i];
    switch (e->kind) {
    case EK_FUEL:
        return fuel < FUEL_MAX;
    case EK_MISSILE:
        return missiles < MISSILES_MAX;
    case EK_CARGO:
        return cargo_cap < LARGE_CARGO_CAP;
    case EK_LASER:
        return laser_tier < (int)e->arg;
    case EK_FLAG:
        return !(equip_owned & e->arg);
    }
    return 0;
}

static void equip_buy(int i) {
    if (i < 0 || i >= NEQUIP)
        return;
    const equip_t *e = &equip[i];
    if (!equip_available(i))
        return;
    if (credits < e->price)
        return;
    credits -= e->price;
    switch (e->kind) {
    case EK_FUEL:
        fuel = FUEL_MAX;
        break;
    case EK_MISSILE:
        missiles++;
        break;
    case EK_CARGO:
        cargo_cap = LARGE_CARGO_CAP;
        break;
    case EK_LASER:
        laser_tier = (int)e->arg;
        break;
    case EK_FLAG:
        equip_owned |= e->arg;
        break;
    }
}

static void equip_step(unsigned in) {
    int pitch = sb(in, 8);
    if (equip_cooldown > 0)
        equip_cooldown--;
    if (equip_cooldown == 0 && pitch != 0) {
        equip_sel += signum(pitch);
        if (equip_sel < 0)
            equip_sel = 0;
        if (equip_sel >= NEQUIP)
            equip_sel = NEQUIP - 1;
        equip_cooldown = 6;
    }
    int buy_btn = (in >> (24 + 5)) & 1; /* shares the market BUY button */
    if (buy_btn && !equip_buy_prev)
        equip_buy(equip_sel);
    equip_buy_prev = buy_btn;
}

static void draw_equip(void) {
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);

    text_print(3, 2, "CR");
    text_print_num4(18, 2, (int)(credits / 10));
    text_print(78, 2, "EQUIP");

    /* Two columns: left items 0..6, right 7..12. "NAME pppp" with price in Cr.
     * Items you already own / can't use are still listed; the selected one is
     * boxed and detailed in the footer. */
    for (int i = 0; i < NEQUIP; i++) {
        int col = (i < 7) ? 0 : 1;
        int row = (i < 7) ? i : (i - 7);
        int x = col ? 66 : 3;
        int y = 12 + row * 8;
        text_print(x, y, equip[i].name);
        text_print_num4(x + 34, y, (int)(equip[i].price / 10));
        if (i == equip_sel) {
            plot_line(x - 2, y - 1, x + 58, y - 1, 1);
            plot_line(x - 2, y + 7, x + 58, y + 7, 1);
            plot_line(x - 2, y - 1, x - 2, y + 7, 1);
            plot_line(x + 58, y - 1, x + 58, y + 7, 1);
        }
    }

    /* Footer: selected item status. */
    const equip_t *e = &equip[equip_sel];
    text_print(3, 86, equip[equip_sel].name);
    if (e->kind == EK_FUEL) {
        text_print(40, 86, "FU");
        text_print_num3(58, 86, fuel);
    } else if (e->kind == EK_MISSILE) {
        text_print(40, 86, "MI");
        text_print_num3(58, 86, missiles);
    } else if (equip_available(equip_sel)) {
        text_print(40, 86, (credits >= e->price) ? "BUY" : "POOR");
    } else {
        text_print(40, 86, "OWNED");
    }
}

/* Ship yard: buy and sell whole ships
 *
 * Elite-A's signature feature. Each hull has a price, a cargo hold, and a top
 * speed; you trade your current ship in (at a fraction of its price) toward a
 * new one. The shipyard shows the selected hull rotating in a hangar. */
typedef struct {
    const char *name;
    int model;
    long price;
    int cargo;
    int speed;
} ship_spec_t;
#define NSHIPS_SPEC 4
#define SHIP_TRADEIN_NUM 3 /* trade-in value = price * 3/4 */
#define SHIP_TRADEIN_DEN 4
/* Prices in tenths of a credit; kept under 99999 (9999.9 Cr) so the whole-credit
 * price fits the 4-digit display. */
static const ship_spec_t ships[NSHIPS_SPEC] = {
    {"SIDEWINDER", MODEL_SIDEWINDER, 2000, 5, 8}, /*  200 Cr — cheap, small, nimble */
    {"COBRA MK3", MODEL_COBRA, 95000, 20, 12},    /* 9500 Cr — the all-rounder      */
    {"VIPER", MODEL_VIPER, 80000, 10, 16},        /* 8000 Cr — fast interceptor     */
    {"KRAIT", MODEL_KRAIT, 50000, 8, 10},         /* 5000 Cr — mid pirate hull      */
};

/* Derive the live ship stats from player_ship (speed; the hold is carried in
 * cargo_cap, which is saved, so it isn't recomputed here). */
static void apply_ship_stats(void) { speed_max = ships[player_ship].speed; }

/* Net cost of trading hull `from` for hull `i` (negative = a refund): the new
 * ship's price minus 3/4 of the current ship's, traded in. Pure for testing. */
static long ship_net_cost_from(int i, int from) {
    return ships[i].price - ships[from].price * SHIP_TRADEIN_NUM / SHIP_TRADEIN_DEN;
}
static long ship_net_cost(int i) { return ship_net_cost_from(i, player_ship); }

/* Boot self-test (mirrored by tools/oracle/shipyard.py): the trade-in maths —
 * upgrading from the Cobra costs, downgrading refunds. Folded into DBG. */
static int ship_self_test(void) {
    return NSHIPS_SPEC == 4 &&
           ship_net_cost_from(2, 1) > 0     /* Cobra -> Viper costs the difference */
           && ship_net_cost_from(0, 1) < 0  /* Cobra -> Sidewinder refunds         */
           && ship_net_cost_from(1, 1) > 0; /* "buying" your own hull is never free */
}

static void ship_buy(int i) {
    if (i < 0 || i >= NSHIPS_SPEC || i == player_ship)
        return;
    long net = ship_net_cost(i);
    if (net > credits)
        return;     /* can't afford the difference */
    credits -= net; /* a downgrade (net<0) refunds */
    player_ship = i;
    cargo_cap = ships[i].cargo; /* a new hull comes without the bay */
    apply_ship_stats();
}

static int shipyard_mode = 0;
static int shipyard_sel = 0;
static int shipyard_cd = 0;
static int shipyard_btn_prev = 0;
static int shipyard_buy_prev = 0;

static void shipyard_step(unsigned in) {
    int pitch = sb(in, 8);
    if (shipyard_cd > 0)
        shipyard_cd--;
    if (shipyard_cd == 0 && pitch != 0) {
        shipyard_sel = clampi(shipyard_sel + signum(pitch), 0, NSHIPS_SPEC - 1);
        shipyard_cd = 6;
    }
    int buy_btn = (in >> (24 + 5)) & 1; /* shares the market/equip BUY button */
    if (buy_btn && !shipyard_buy_prev)
        ship_buy(shipyard_sel);
    shipyard_buy_prev = buy_btn;
}

/* Draw `model` spinning in the hangar (yaw from frame_no, fixed 3/4 tilt). */
static void draw_hangar_ship(int model) {
    int yaw = frame_no & 63;
    int c = icos(yaw), s = isin(yaw);
    int tc = icos(8), ts = isin(8); /* ~45 deg tilt about X */
    int m[9] = {c, 0, s, FXMUL(ts, s), tc, -FXMUL(ts, c), -FXMUL(tc, s), ts, FXMUL(tc, c)};
    for (int i = 0; i < 9; i++)
        MMIO[M0 + i] = (unsigned)m[i];
    MMIO[T0 + 0] = 0;
    MMIO[T0 + 1] = 0;
    MMIO[T0 + 2] = (unsigned)Q(9);
    MMIO[MODEL] = (unsigned)model;
    MMIO[CTRL] = START | CLEAR; /* clears the frame, then draws the hull */
    while (!(MMIO[CTRL] & 2u)) {
    }
}

static void draw_shipyard(void) {
    draw_hangar_ship(ships[shipyard_sel].model); /* hull first (clears the frame) */

    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);

    text_print(3, 2, "CR");
    text_print_num4(18, 2, (int)(credits / 10));
    text_print(74, 2, "SHIPS");

    /* List the hulls with price; the selected one is boxed; your current hull
     * is tagged. (Drawn over the lower rows so the hangar shows up top.) */
    for (int i = 0; i < NSHIPS_SPEC; i++) {
        int y = 56 + i * 9;
        text_print(3, y, ships[i].name);
        text_print_num4(66, y, (int)(ships[i].price / 10));
        if (i == player_ship)
            text_print(108, y, "<");
        if (i == shipyard_sel) {
            plot_line(1, y - 1, 120, y - 1, 1);
            plot_line(1, y + 7, 120, y + 7, 1);
        }
    }

    /* Footer: selected hull's stats + buy/own state. */
    const ship_spec_t *sp = &ships[shipyard_sel];
    text_print(3, 92, "CG");
    text_print_num3(18, 92, sp->cargo);
    text_print(44, 92, "SP");
    text_print_num3(60, 92, sp->speed);
    if (shipyard_sel == player_ship) {
        text_print(86, 92, "OWNED");
    } else {
        long net = ship_net_cost(shipyard_sel);
        text_print(86, 92, (net <= credits) ? "BUY" : "POOR");
    }
}

/* Planet */
/* A single distant body that grows as the player approaches. View-space pos
 * + a Q8.8 world-size; per-frame we apply player MVEIT inversely (so it
 * drifts with the world), advance toward the camera by player speed, project
 * to screen, and plot a Bresenham circle with radius = FOCAL*size/z. When
 * it crosses the near plane we respawn it far + off-axis so the cycle
 * repeats and the demo loops cleanly. */
typedef struct {
    v3 pos;
    int size;
} planet_t;
static planet_t planet;

static void planet_init(void) {
    planet.pos.x = Q(14);
    planet.pos.y = Q(6);
    planet.pos.z = Q(120);
    planet.size = Q(18);
}

static void planet_respawn(void) {
    /* Skew slightly off-axis so we don't fly straight through the centre. */
    planet.pos.x = prand_signed(Q(20)) + Q(8);
    planet.pos.y = prand_signed(Q(12));
    planet.pos.z = Q(180);
}

static void planet_step_and_draw(void) {
    v3 *p = &planet.pos;
    if (p_beta)
        v3_rot_plane(p, 1, 2, -p_beta);
    if (p_alpha)
        v3_rot_plane(p, 0, 1, -p_alpha);
    p->z -= p_delta * 16;
    if (p->z < Q(2)) {
        planet_respawn();
        return;
    }
    int sx, sy;
    if (p->z <= 0)
        return;
    sx = CX + (FOCAL * p->x) / p->z;
    sy = CY - (FOCAL * p->y) / p->z;
    int r = (FOCAL * planet.size) / p->z;
    if (r < 1)
        r = 1;
    if (r > 80)
        r = 80;
    plot_circle(sx, sy, r, 1);
    /* Great-circle arcs: a flattened horizontal ellipse = equator
     * and a narrow vertical ellipse = prime meridian. Both span the limb, so
     * the disc reads as a sphere once it's large enough to show curvature. */
    if (r >= 6) {
        plot_ellipse(sx, sy, r, r / 4); /* equator (tilted, flattened) */
        plot_ellipse(sx, sy, r / 3, r); /* prime meridian (vertical)   */
    }
}

/* Sun
 *
 * A second body, drawn as a *filled* disc (stacked horizontal lines) to
 * read distinctly from the wireframe planet. For each y from -r..+r, plot
 * a horizontal line of width 2*sqrt(r^2 - y^2). Cost is O(r) line draws
 * per frame; at r=20 that's ~40 plot_line calls — cheap. */
typedef struct {
    v3 pos;
    int size;
} sun_t;
static sun_t sun;

static void sun_init(void) {
    sun.pos.x = Q(-22);
    sun.pos.y = Q(-4);
    sun.pos.z = Q(150);
    sun.size = Q(12);
}

static void sun_respawn(void) {
    sun.pos.x = -prand_signed(Q(16)) - Q(8); /* keep on the left-ish */
    sun.pos.y = prand_signed(Q(8));
    sun.pos.z = Q(200);
}

static void sun_step_and_draw(void) {
    v3 *p = &sun.pos;
    if (p_beta)
        v3_rot_plane(p, 1, 2, -p_beta);
    if (p_alpha)
        v3_rot_plane(p, 0, 1, -p_alpha);
    p->z -= p_delta * 16;
    if (p->z < Q(2)) {
        sun_respawn();
        return;
    }
    int cx = CX + (FOCAL * p->x) / p->z;
    int cy = CY - (FOCAL * p->y) / p->z;
    int r = (FOCAL * sun.size) / p->z;
    if (r < 1)
        return;
    if (r > 40)
        r = 40;
    int r2 = r * r;
    for (int dy = -r; dy <= r; dy++) {
        /* w = sqrt(r^2 - dy^2) via isqrt32 (Q0, not Q8.8) */
        int w = (int)isqrt32((unsigned)(r2 - dy * dy));
        int y = cy + dy;
        if (y < 0 || y >= VIEW_H_PX)
            continue;
        plot_line(cx - w, y, cx + w, y, 1);
    }
}

/* Station
 *
 * A rotating hexagonal outline in the scene. View-space pos + size + a
 * rotation phase that advances each frame. We project the centre, compute
 * 6 hex vertices in screen space at angles 0..300° offset by the phase,
 * and connect them with 6 lines. Reads as a slowly-spinning station hull
 * even without geometry-correct 3D rotation. */
typedef struct {
    v3 pos;
    int size;
    int phase;
} station_t;
static station_t station;

static void station_init(void) {
    station.pos.x = Q(0);
    station.pos.y = Q(-2);
    station.pos.z = Q(60);
    station.size = Q(6);
    station.phase = 0;
}

static void station_respawn(void) {
    station.pos.x = prand_signed(Q(10));
    station.pos.y = prand_signed(Q(4));
    station.pos.z = Q(150);
    station.phase = 0;
}

static void station_step_and_draw(void) {
    v3 *p = &station.pos;
    if (p_beta)
        v3_rot_plane(p, 1, 2, -p_beta);
    if (p_alpha)
        v3_rot_plane(p, 0, 1, -p_alpha);
    p->z -= p_delta * 16;
    if (p->z < Q(2)) {
        station_respawn();
        return;
    }
    int cx = CX + (FOCAL * p->x) / p->z;
    int cy = CY - (FOCAL * p->y) / p->z;
    int r = (FOCAL * station.size) / p->z;
    if (r < 2)
        return;
    if (r > 40)
        r = 40;
    /* Six hex vertices: angle index k*64/6 (over the 64-step sintab), plus
     * the rotating phase offset. */
    int v_sx[6], v_sy[6];
    for (int i = 0; i < 6; i++) {
        int ang = (i * 64 / 6 + station.phase) & 63;
        /* Scale isin/icos (Q8.8) by r and back to integer pixels. */
        v_sx[i] = cx + ((icos(ang) * r) >> 8);
        v_sy[i] = cy + ((isin(ang) * r) >> 8);
    }
    for (int i = 0; i < 6; i++) {
        int j = (i + 1) % 6;
        plot_line(v_sx[i], v_sy[i], v_sx[j], v_sy[j], 1);
    }
    /* Slot at the front face: a horizontal line bisecting one edge. Reads as
     * "docking bay" — not 1:1 with Coriolis but the right silhouette beat. */
    int mx = (v_sx[0] + v_sx[1]) / 2;
    int my = (v_sy[0] + v_sy[1]) / 2;
    int nx = (v_sx[3] + v_sx[4]) / 2;
    int ny = (v_sy[3] + v_sy[4]) / 2;
    plot_line(mx, my, nx, ny, 1);

    station.phase = (station.phase + 1) & 63;
}

/* DOCKIT autopilot */
/* Steer player toward station, close range, trigger dock when in contact.
 * Sets p_alpha / p_beta / p_delta directly (bypassing flight_step) and
 * uses the existing MVEIT to apply the rotation to the world each frame. */
static void dockit_step(void) {
    if (station.pos.z < Q(2)) {
        /* Station behind / too close — bail out of autopilot. */
        dockit_active = 0;
        return;
    }
    /* Lateral steering: rotate so the station drifts toward (0, 0). The
     * signs here match the MVEIT player-inverse rotation conventions. */
    if (station.pos.x > Q(1))
        p_alpha = +ROLL_MAX;
    else if (station.pos.x < -Q(1))
        p_alpha = -ROLL_MAX;
    else
        p_alpha = 0;
    if (station.pos.y > Q(1))
        p_beta = -PITCH_MAX;
    else if (station.pos.y < -Q(1))
        p_beta = +PITCH_MAX;
    else
        p_beta = 0;
    /* Throttle: full speed when far, slow approach inside the cruise band. */
    long sx2 = (long)station.pos.x * station.pos.x;
    long sy2 = (long)station.pos.y * station.pos.y;
    long sz2 = (long)station.pos.z * station.pos.z;
    long d2 = sx2 + sy2 + sz2;
    if (d2 > DOCK_CRUISE_Q2)
        p_delta = speed_max;
    else if (d2 > DOCK_RANGE_Q2)
        p_delta = SPEED_MIN + 2;
    else {
        /* Docked! Start the timer + freeze the world. */
        dock_timer = DOCK_TIMER_FRAMES;
        dockit_active = 0;
        res2();
    }
}

static void dock_tick(void) {
    if (dock_timer <= 0)
        return;
    dock_timer--;
    if (dock_timer == 0) {
        /* Refit and re-launch into a fresh scene. */
        fuel = FUEL_MAX;
        energy = 15;
        player_shields = 15;
        laser_heat = 0;
        p_alpha = p_beta = 0;
        p_delta = 4;
        scene_init();
        stars_init();
        planet_init();
        sun_init();
        station_init();
    }
}

static void draw_dock_overlay(void) {
    if (dock_timer > 0)
        text_print(CX - 18, CY - 4, "DOCKED");
}

/* Stardust + particles per-frame */
static void stars_step_and_draw(void) {
    for (int i = 0; i < NSTARS; i++) {
        v3 *p = &stars[i].pos;
        if (p_beta)
            v3_rot_plane(p, 1, 2, -p_beta);
        if (p_alpha)
            v3_rot_plane(p, 0, 1, -p_alpha);
        p->z -= p_delta * 16;
        if (p->z < Q(1)) {
            star_respawn(&stars[i], Q(100));
            continue;
        }
        int sx, sy;
        if (project(p, &sx, &sy))
            plot_point(sx, sy);
    }
}

static void parts_step_and_draw(void) {
    for (int i = 0; i < NPARTS_MAX; i++) {
        if (!parts[i].active)
            continue;
        parts[i].pos.x += parts[i].vel.x;
        parts[i].pos.y += parts[i].vel.y;
        parts[i].pos.z += parts[i].vel.z;
        v3 *p = &parts[i].pos;
        if (p_beta)
            v3_rot_plane(p, 1, 2, -p_beta);
        if (p_alpha)
            v3_rot_plane(p, 0, 1, -p_alpha);
        p->z -= p_delta * 16;
        if (++parts[i].age >= PART_LIFETIME) {
            parts[i].active = 0;
            continue;
        }
        int sx, sy;
        if (project(p, &sx, &sy))
            plot_point(sx, sy);
    }
}

static void draw_laser(void) {
    if (!fire_active)
        return;
    plot_line(4, 92, CX, CY, 1);
    plot_line(123, 92, CX, CY, 1);
}

/* Tactics: look-at AI + enemy fire */
#define ENEMY_FIRE_RANGE_Q2 (Q(40) * Q(40))
#define ENEMY_CLOSE_RANGE_Q2 (Q(20) * Q(20))

/* Build a model->view rotation so the ship's nose points at the view origin
 * (the player). Returns 0 if the ship is too close to the view-up axis to
 * resolve `right` cleanly — caller leaves m unchanged that frame. */
static int look_at_player(ship_t *s) {
    int neg[3] = {-s->pos.x, -s->pos.y, -s->pos.z};
    int neg_n = (int)isqrt32((unsigned)(neg[0] * neg[0] + neg[1] * neg[1] + neg[2] * neg[2]));
    if (neg_n == 0)
        return 0;
    int fwd[3] = {(neg[0] * ONE) / neg_n, (neg[1] * ONE) / neg_n, (neg[2] * ONE) / neg_n};

    /* right = up × fwd  with up = (0, 1, 0)  =>  (fwd.z, 0, -fwd.x) */
    int right[3] = {fwd[2], 0, -fwd[0]};
    int rn =
        (int)isqrt32((unsigned)(right[0] * right[0] + right[1] * right[1] + right[2] * right[2]));
    if (rn < 16)
        return 0; /* near-parallel: leave m alone this frame */
    right[0] = (right[0] * ONE) / rn;
    right[1] = (right[1] * ONE) / rn;
    right[2] = (right[2] * ONE) / rn;

    int up[3];
    v3_cross(fwd, right, up);

    /* row-major m: row i = (right[i], up[i], fwd[i]) so that m's columns are
     * the ship's model basis vectors (right, up, fwd) expressed in view space. */
    s->m[0] = right[0];
    s->m[1] = up[0];
    s->m[2] = fwd[0];
    s->m[3] = right[1];
    s->m[4] = up[1];
    s->m[5] = fwd[1];
    s->m[6] = right[2];
    s->m[7] = up[2];
    s->m[8] = fwd[2];
    return 1;
}

static void enemy_fire_at_player(ship_t *s) {
    s->fire_visual = 3;
    /* E.C.M. System jams incoming fire (no shield damage). */
    if (equip_owned & EQ_ECM) {
        hit_flash = 1;
        return;
    }
    if (player_shields > 0)
        player_shields--;
    hit_flash = 2;
    if (player_shields == 0 && !dying) {
        if (equip_owned & EQ_ESCAPEPOD) {
            /* Escape pod: bail out instead of dying — lose cargo, flush the
             * bubble, restore shields, and the pod is consumed. */
            equip_owned &= ~EQ_ESCAPEPOD;
            for (int i = 0; i < NGOODS; i++)
                cargo[i] = 0;
            player_shields = 15;
            for (int i = 0; i < NSHIPS_MAX; i++)
                bubble[i].type = SHIP_EMPTY;
            hit_flash = 3;
            return;
        }
        /* Death: flush bubble, spawn cloud at view origin, run dying timer. */
        v3 at = {0, 0, Q(2)};
        spawn_explosion(&at);
        spawn_explosion(&at);
        for (int i = 0; i < NSHIPS_MAX; i++)
            bubble[i].type = SHIP_EMPTY;
        dying = 30;
    }
}

static void ship_ai(ship_t *s) {
    if (s->behavior != BEHAVIOR_HOSTILE)
        return;
    long dist2 = (long)s->pos.x * s->pos.x + (long)s->pos.y * s->pos.y + (long)s->pos.z * s->pos.z;

    look_at_player(s);
    s->delta = (dist2 > ENEMY_CLOSE_RANGE_Q2) ? 6 : 2;

    if (s->fire_cooldown > 0) {
        s->fire_cooldown--;
        return;
    }
    if (s->pos.z <= 0 || dist2 > ENEMY_FIRE_RANGE_Q2)
        return;
    if ((int)(rand_word() & 0x3F) < s->aggression) {
        enemy_fire_at_player(s);
        s->fire_cooldown = 14;
    }
}

static void draw_enemy_fire(ship_t *s) {
    if (s->fire_visual <= 0)
        return;
    int sx, sy;
    if (project(&s->pos, &sx, &sy))
        plot_line(sx, sy, CX, CY, 1);
    s->fire_visual--;
}

/* Missiles + energy bomb */
#define MISSILE_FLIGHT 10 /* frames from launch to impact */
#define LOCK_RANGE 40     /* screen Manhattan distance to acquire a lock */

/* Alive ship nearest the crosshair, or -1 if none is close enough to lock. */
static int find_target(void) {
    int best = -1, bestd = 1 << 30;
    for (int i = 0; i < NSHIPS_MAX; i++) {
        if (bubble[i].type != SHIP_ALIVE)
            continue;
        int sx, sy;
        if (!project(&bubble[i].pos, &sx, &sy))
            continue;
        int d = (sx > CX ? sx - CX : CX - sx) + (sy > CY ? sy - CY : CY - sy);
        if (d < bestd) {
            bestd = d;
            best = i;
        }
    }
    return (best >= 0 && bestd <= LOCK_RANGE) ? best : -1;
}

static void missile_launch(void) {
    if (missile_active || missiles <= 0)
        return;
    int t = find_target();
    if (t < 0)
        return;
    missiles--;
    missile_active = 1;
    missile_target = t;
    missile_age = 0;
}

static void missile_step(void) {
    if (!missile_active)
        return;
    ship_t *t = &bubble[missile_target];
    if (missile_target < 0 || t->type != SHIP_ALIVE) {
        missile_active = 0;
        return;
    }
    if (++missile_age >= MISSILE_FLIGHT) {
        spawn_explosion(&t->pos);
        t->type = SHIP_EXPLODING;
        t->boom_age = 0;
        kills++;
        missile_active = 0;
    }
}

/* Energy Bomb: one-shot — destroys every alive ship in the bubble. */
static void energy_bomb(void) {
    if (!(equip_owned & EQ_ENERGYBOMB))
        return;
    equip_owned &= ~EQ_ENERGYBOMB;
    for (int i = 0; i < NSHIPS_MAX; i++) {
        if (bubble[i].type == SHIP_ALIVE) {
            spawn_explosion(&bubble[i].pos);
            bubble[i].type = SHIP_EXPLODING;
            bubble[i].boom_age = 0;
            kills++;
        }
    }
    hit_flash = 3;
}

/* Living world: canisters, scooping, asteroids, police
 *
 * Combat and exploration now feed each other: destroyed ships and mined
 * asteroids scatter cargo canisters; with Fuel Scoops fitted you can scoop
 * them (or refuel by skimming the sun); and committing crimes summons police
 * Vipers whose own destruction makes you a worse criminal still. */
#define SCOOP_NEAR_Z Q(6) /* a canister within this view-z is at the scoop */
#define POLICE_CADENCE 90 /* frames between police reinforcements */

static int count_role(int role) {
    int n = 0;
    for (int i = 0; i < NSHIPS_MAX; i++)
        if (bubble[i].type == SHIP_ALIVE && bubble[i].role == role)
            n++;
    return n;
}

/* Pure rule helpers (mirrored + cross-checked by tools/oracle/world.py): how
 * many police hunt a wanted commander, and how many minerals an asteroid drops. */
static int police_max(int legal) { return legal >= 50 ? 2 : 1; }
static int mine_splinters(int tier) { return tier >= 2 ? 3 : 1; }

/* Boot self-test (mirrored by tools/oracle/world.py): the living-world rule
 * boundaries (police heat by legal band, minerals by laser tier). Folded into
 * the DBG sentinel so rule drift fails the boot. */
static int world_self_test(void) {
    return police_max(1) == 1 && police_max(49) == 1 && police_max(50) == 2 &&
           mine_splinters(1) == 1 && mine_splinters(2) == 3 && mine_splinters(3) == 3;
}

/* Spawn a slowly-tumbling cargo canister at view-space `at` (scoopable loot). */
static void spawn_canister(int x, int y, int z) {
    int s = nwshp(x, y, z, 0, 1, 1, 0, BEHAVIOR_STATIC, 0, 1);
    if (s >= 0) {
        bubble[s].model = MODEL_CANISTER;
        bubble[s].role = ROLE_CANISTER;
    }
}

/* Mining: a destroyed asteroid scatters mineral splinters (more with a beam /
 * mining laser). They are canisters, so they can be scooped like any cargo. */
static void mine_asteroid(int x, int y, int z, int tier) {
    int n = mine_splinters(tier);
    for (int k = 0; k < n; k++)
        spawn_canister(x + prand_signed(Q(3)), y + prand_signed(Q(3)), z);
}

/* Scoop a canister if it has reached the scoop (close, ahead, roughly centred)
 * and we have Fuel Scoops fitted with hold space. Returns 1 if scooped. */
static int try_scoop(ship_t *c) {
    if (c->pos.z <= Q(1) || c->pos.z > SCOOP_NEAR_Z)
        return 0;
    int sx, sy;
    if (!project(&c->pos, &sx, &sy))
        return 0;
    if (sx < CX - 16 || sx > CX + 16)
        return 0;
    if (!(equip_owned & EQ_FUELSCOOP) || cargo_used() >= cargo_cap)
        return 0;
    cargo[rand_word() % NGOODS]++; /* a tonne of some commodity */
    sfx_play(SFX_LASER, 3);
    return 1;
}

/* Skimming the sun with Fuel Scoops refuels the tank a little each frame. */
static void fuel_scoop_step(void) {
    if (!(equip_owned & EQ_FUELSCOOP) || fuel >= FUEL_MAX)
        return;
    if (sun.pos.z < Q(2) || sun.pos.z > Q(45))
        return; /* must be skimming it */
    if (sun.pos.x < -Q(28) || sun.pos.x > Q(28))
        return;
    if ((frame_no & 3) == 0)
        fuel = clampi(fuel + 1, 0, FUEL_MAX);
}

/* While you are wanted, police Vipers arrive to hunt you — more heat as a
 * Fugitive. They are ordinary hostiles tagged ROLE_POLICE so destroying one
 * counts as the serious crime it is. */
static int police_timer = 0;
static void police_step(void) {
    int maxp = police_max(legal_status);
    if (police_timer > 0)
        police_timer--;
    if (legal_status > 0 && police_timer == 0 && count_role(ROLE_POLICE) < maxp) {
        int s = nwshp(prand_signed(Q(8)), prand_signed(Q(6)), Q(55), 0, 0, 0, 0, BEHAVIOR_HOSTILE,
                      56, 4);
        if (s >= 0) {
            bubble[s].model = MODEL_VIPER;
            bubble[s].role = ROLE_POLICE;
        }
        police_timer = POLICE_CADENCE;
    }
}

/* Corner-bracket reticle around the currently locked target. */
static void draw_target_lock(void) {
    if (missile_lock < 0 || bubble[missile_lock].type != SHIP_ALIVE)
        return;
    int sx, sy;
    if (!project(&bubble[missile_lock].pos, &sx, &sy))
        return;
    plot_line(sx - 5, sy - 5, sx - 2, sy - 5, 1);
    plot_line(sx - 5, sy - 5, sx - 5, sy - 2, 1);
    plot_line(sx + 5, sy - 5, sx + 2, sy - 5, 1);
    plot_line(sx + 5, sy - 5, sx + 5, sy - 2, 1);
    plot_line(sx - 5, sy + 5, sx - 2, sy + 5, 1);
    plot_line(sx - 5, sy + 5, sx - 5, sy + 2, 1);
    plot_line(sx + 5, sy + 5, sx + 2, sy + 5, 1);
    plot_line(sx + 5, sy + 5, sx + 5, sy + 2, 1);
}

/* Missile in flight: a marker lerping from the cockpit toward its target. */
static void draw_missile(void) {
    if (!missile_active || bubble[missile_target].type != SHIP_ALIVE)
        return;
    int f = (missile_age * 256) / MISSILE_FLIGHT;
    v3 mp = {FXMUL(bubble[missile_target].pos.x, f), FXMUL(bubble[missile_target].pos.y, f),
             FXMUL(bubble[missile_target].pos.z, f)};
    int sx, sy;
    if (project(&mp, &sx, &sy)) {
        plot_line(CX, VIEW_H_PX - 1, sx, sy, 1);
        plot_line(sx - 2, sy, sx + 2, sy, 1);
        plot_line(sx, sy - 2, sx, sy + 2, 1);
    }
}

static void draw_hit_flash(void) {
    if (hit_flash <= 0)
        return;
    plot_line(0, 0, 127, 0, 1);
    plot_line(0, VIEW_H_PX - 1, 127, VIEW_H_PX - 1, 1);
    plot_line(0, 0, 0, VIEW_H_PX - 1, 1);
    plot_line(127, 0, 127, VIEW_H_PX - 1, 1);
    hit_flash--;
}

/* Dashboard */
#define DASH_TOP 96
#define BAR_X0 14 /* shifted right to leave room for 2-char label */
#define BAR_W_MAX 50
#define BAR_H 2

static void draw_bar(int y, int max_v, int v) {
    int w = (v * BAR_W_MAX) / max_v;
    if (w < 0)
        w = 0;
    if (w > BAR_W_MAX)
        w = BAR_W_MAX;
    for (int r = 0; r < BAR_H; r++)
        plot_line(BAR_X0, y + r, BAR_X0 + w, y + r, 1);
    for (int t = 0; t <= 4; t++) {
        int x = BAR_X0 + (t * BAR_W_MAX) / 4;
        plot_point(x, y + BAR_H);
    }
}

/* Scanner radar
 *
 * Top-down view of nearby ships occupying the right half of the dashboard
 * band. The radar oval shows ±SCAN_RANGE view units of (x, z); each alive
 * ship plots a single pixel at (SCAN_CX + ship.x * SCAN_HW / SCAN_RANGE,
 * SCAN_CY - ship.z * SCAN_HH / SCAN_RANGE). Hostiles draw a 1-pixel cross
 * instead of a single dot so they stand out from neutrals. Skips ships
 * outside the scanner range or behind the camera. */
#define SCAN_CX 104
#define SCAN_CY 112
#define SCAN_HW 22
#define SCAN_HH 9
#define SCAN_RANGE Q(60)

static void scanner_frame(void) {
    /* Oval-ish outline: top + bottom + sides — cheaper than a Bresenham
     * ellipse and still reads as "radar area". */
    plot_line(SCAN_CX - SCAN_HW, SCAN_CY - SCAN_HH, SCAN_CX + SCAN_HW, SCAN_CY - SCAN_HH, 1);
    plot_line(SCAN_CX - SCAN_HW, SCAN_CY + SCAN_HH, SCAN_CX + SCAN_HW, SCAN_CY + SCAN_HH, 1);
    plot_line(SCAN_CX - SCAN_HW, SCAN_CY - SCAN_HH, SCAN_CX - SCAN_HW, SCAN_CY + SCAN_HH, 1);
    plot_line(SCAN_CX + SCAN_HW, SCAN_CY - SCAN_HH, SCAN_CX + SCAN_HW, SCAN_CY + SCAN_HH, 1);
    /* Player marker at centre. */
    plot_point(SCAN_CX, SCAN_CY);
}

static void scanner_plot_ship(const ship_t *s) {
    if (s->pos.z <= 0 || s->pos.z > SCAN_RANGE)
        return;
    int ax = s->pos.x;
    if (ax < 0)
        ax = -ax;
    if (ax > SCAN_RANGE)
        return;
    int sx = SCAN_CX + (s->pos.x * SCAN_HW) / SCAN_RANGE;
    int sy = SCAN_CY - (s->pos.z * SCAN_HH) / SCAN_RANGE;
    if (sx < SCAN_CX - SCAN_HW || sx > SCAN_CX + SCAN_HW)
        return;
    if (sy < SCAN_CY - SCAN_HH || sy > SCAN_CY + SCAN_HH)
        return;
    if (s->behavior == BEHAVIOR_HOSTILE) {
        /* Hostiles: 1-pixel cross. */
        plot_point(sx, sy);
        plot_point(sx - 1, sy);
        plot_point(sx + 1, sy);
        plot_point(sx, sy - 1);
        plot_point(sx, sy + 1);
    } else {
        plot_point(sx, sy);
    }
}

static void draw_scanner(void) {
    scanner_frame();
    for (int i = 0; i < NSHIPS_MAX; i++) {
        if (bubble[i].type == SHIP_ALIVE)
            scanner_plot_ship(&bubble[i]);
    }
}

/* Bar labels at the left of each gauge.
 * Each label is 2 chars (12 px) at x=0, bars shifted right to BAR_X0 to fit. */
static void draw_bar_labeled(int y, int max_v, int v, const char *label) {
    text_print(0, y, label);
    draw_bar(y, max_v, v);
}

static void draw_dashboard(void) {
    plot_line(0, DASH_TOP, 127, DASH_TOP, 1);
    draw_bar_labeled(98, speed_max, p_delta, "SP");
    draw_bar_labeled(103, LASER_HEAT_MAX, laser_heat, "HT");
    draw_bar_labeled(108, 15, energy, "EN");
    draw_bar_labeled(113, 15, player_shields, "SH");
    draw_bar_labeled(118, FUEL_MAX, fuel, "FU");
    draw_scanner();
}

static int crosshair_hit(const v3 *view_pos) {
    int sx, sy;
    if (!project(view_pos, &sx, &sy))
        return 0;
    return (sx >= CX - CROSS_HALF && sx <= CX + CROSS_HALF && sy >= CY - CROSS_HALF &&
            sy <= CY + CROSS_HALF);
}

/* Scene + respawn */
static void scene_init(void) {
    res2();
    /* Static traders (5) — the working world the player flies through. Fewer
     * than before so dynamic spawns (canisters, police) have free bubble slots. */
    nwshp(Q(0), Q(0), Q(22), 0, 0, 0, 0, BEHAVIOR_STATIC, 0, 2);
    nwshp(Q(-7), Q(-3), Q(38), -8, 0, 0, 0, BEHAVIOR_STATIC, 0, 2);
    nwshp(Q(7), Q(3), Q(48), 8, 0, 0, 0, BEHAVIOR_STATIC, 0, 2);
    nwshp(Q(-9), Q(4), Q(35), -4, +1, 0, 0, BEHAVIOR_STATIC, 0, 2);
    nwshp(Q(8), Q(0), Q(85), 28, -1, 0, 6, BEHAVIOR_STATIC, 0, 2);
    /* Hostile fighters (2) — pursue + fire on the player. */
    nwshp(Q(9), Q(-4), Q(45), 0, 0, 0, 0, BEHAVIOR_HOSTILE, 40, 4);
    nwshp(Q(-8), Q(6), Q(62), 0, 0, 0, 0, BEHAVIOR_HOSTILE, 40, 4);
    /* Drifting asteroids (2) — shoot them for scoopable mineral splinters. */
    int a1 = nwshp(Q(-4), Q(2), Q(40), 0, +1, +1, 0, BEHAVIOR_STATIC, 0, 3);
    if (a1 >= 0) {
        bubble[a1].model = MODEL_ASTEROID;
        bubble[a1].role = ROLE_ASTEROID;
    }
    int a2 = nwshp(Q(5), Q(-2), Q(68), 0, +1, -1, 0, BEHAVIOR_STATIC, 0, 3);
    if (a2 >= 0) {
        bubble[a2].model = MODEL_ASTEROID;
        bubble[a2].role = ROLE_ASTEROID;
    }
    /* Mission 1: the Constrictor lurks in its target system — a tough, very
     * aggressive prototype. It only appears while the hunt is still on. */
    if (mission_state == MISSION_ACTIVE && current_sys == mission_target_sys) {
        int cc = nwshp(Q(2), Q(3), Q(52), 0, 0, 0, 0, BEHAVIOR_HOSTILE, 60, 8);
        if (cc >= 0) {
            bubble[cc].model = MODEL_CONSTRICTOR;
            bubble[cc].role = ROLE_CONSTRICTOR;
        }
    }
}

static void respawn_player(void) {
    player_shields = 15;
    energy = 15;
    laser_heat = 0;
    fire_active = 0;
    hit_flash = 0;
    p_alpha = p_beta = 0;
    p_delta = 4;
    scene_init();
    stars_init();
    planet_init();
    sun_init();
    station_init();
}

static void death_tick(void) {
    if (dying > 0 && --dying == 0)
        respawn_player();
}

/* Main loop */
int main(void) {
    /* DBG = 0xCAFE iff the firmware galaxy PRNG matches `tools/oracle/galaxy.py`
     * exactly; otherwise 0xDEAD. SocSim asserts on 0xCAFE, so any C/Python
     * drift fails the boot sanity test. */
    MMIO[DBG] = (galaxy_self_test() && cmdr_self_test() && names_self_test() && desc_self_test() &&
                 status_self_test() && world_self_test() && ship_self_test() && mission_self_test())
                    ? 0xCAFEu
                    : 0xDEADu;
    apply_ship_stats(); /* derive speed from the starting hull */
    scene_init();
    stars_init();
    planet_init();
    sun_init();
    station_init();
    market_gen();
    psg_silence();
    sfx_play(SFX_CHORD, 24); /* boot fanfare */
    for (;;) {
        unsigned in = (dying > 0 || hyper_flash > 0 || dock_timer > 0) ? 0u : MMIO[INPUT];
        unsigned in2 = (dying > 0 || hyper_flash > 0 || dock_timer > 0) ? 0u : MMIO[INPUT2];

        sfx_step(); /* advance the PSG sound effect (all modes) */

        /* INPUT2 buttons: bit0 = missile launch, bit1 = energy bomb,
         * bit2 = save commander, bit3 = load commander. */
        int missile_btn = (in2 >> 0) & 1;
        int bomb_btn = (in2 >> 1) & 1;
        int save_btn = (in2 >> 2) & 1;
        int load_btn = (in2 >> 3) & 1;
        int status_btn = (in2 >> 4) & 1;
        int yard_btn = (in2 >> 5) & 1;

        /* Edge-detect chart toggle (byte 3 bit 1), hyperspace (byte 3 bit 2),
         * DOCKIT autopilot (byte 3 bit 3). */
        int chart_btn = (in >> (24 + 1)) & 1;
        int hyper_btn = (in >> (24 + 2)) & 1;
        int dockit_btn = (in >> (24 + 3)) & 1;
        int market_btn = (in >> (24 + 4)) & 1;
        int equip_btn = (in >> (24 + 7)) & 1;
        /* Chart button cycles space -> galactic chart -> system data -> space. */
        if (chart_btn && !chart_btn_prev)
            chart_mode = (chart_mode + 1) % CHART_NMODES;
        /* Hyperspace: jump to the selected system, or — if you've selected the
         * one you're already in and own the Galactic Hyperdrive — jump galaxy. */
        if (hyper_btn && !hyper_btn_prev) {
            if (target_sys != current_sys)
                witchspace_jump();
            else
                galactic_jump();
        }
        /* DOCKIT autopilot requires a Docking Computer. */
        if (dockit_btn && !dockit_btn_prev && (equip_owned & EQ_DOCKCOMP))
            dockit_active = !dockit_active;
        if (market_btn && !market_btn_prev)
            market_mode = !market_mode;
        if (equip_btn && !equip_btn_prev)
            equip_mode = !equip_mode;
        if (missile_btn && !missile_btn_prev)
            missile_launch();
        if (bomb_btn && !bomb_btn_prev)
            energy_bomb();
        /* Save/load the commander. Load only after a save exists + checksum OK. */
        if (save_btn && !save_btn_prev) {
            cmdr_save();
            save_flash = 12;
        }
        if (load_btn && !load_btn_prev && cmdr_load())
            save_flash = -12;
        if (status_btn && !status_btn_prev)
            status_mode = !status_mode;
        if (yard_btn && !shipyard_btn_prev)
            shipyard_mode = !shipyard_mode;
        chart_btn_prev = chart_btn;
        hyper_btn_prev = hyper_btn;
        dockit_btn_prev = dockit_btn;
        market_btn_prev = market_btn;
        equip_btn_prev = equip_btn;
        missile_btn_prev = missile_btn;
        bomb_btn_prev = bomb_btn;
        save_btn_prev = save_btn;
        load_btn_prev = load_btn;
        status_btn_prev = status_btn;
        shipyard_btn_prev = yard_btn;

        if (status_mode) {
            clear_only();
            draw_status();
            draw_dashboard();
            MMIO[FRAME] = 1;
            while (MMIO[FRAME] & 1u) {
            }
            frame_no++;
            continue;
        }

        if (shipyard_mode) {
            shipyard_step(in);
            draw_shipyard(); /* draws its own hangar (clears the frame) */
            draw_dashboard();
            MMIO[FRAME] = 1;
            while (MMIO[FRAME] & 1u) {
            }
            frame_no++;
            continue;
        }

        if (equip_mode) {
            equip_step(in);
            clear_only();
            draw_equip();
            draw_dashboard();
            MMIO[FRAME] = 1;
            while (MMIO[FRAME] & 1u) {
            }
            frame_no++;
            continue;
        }

        if (market_mode) {
            market_step(in);
            clear_only();
            draw_market();
            draw_dashboard();
            MMIO[FRAME] = 1;
            while (MMIO[FRAME] & 1u) {
            }
            frame_no++;
            continue;
        }

        if (chart_mode) {
            clear_only();
            if (chart_mode == CHART_DATA) {
                draw_sysdata(); /* inspect target system (no cursor) */
            } else {
                chart_step(in); /* CHART_MAP: move cursor, pick target */
                draw_chart();
            }
            draw_dashboard();
            MMIO[FRAME] = 1;
            while (MMIO[FRAME] & 1u) {
            }
            frame_no++;
            continue;
        }

        if (dockit_active)
            dockit_step();
        else
            flight_step(in);
        int fire_was = fire_active;
        laser_step(in);
        if (fire_active && !fire_was && sfx_kind != SFX_BOOM)
            sfx_play(SFX_LASER, 4);

        unsigned flags = CLEAR;
        police_step(); /* summon the law if we're wanted */
        for (int i = 0; i < NSHIPS_MAX; i++) {
            if (bubble[i].type == SHIP_EMPTY)
                continue;
            mveit(&bubble[i]);

            if (bubble[i].type == SHIP_ALIVE) {
                /* Cargo canisters: scoop them or let them drift past — no AI,
                 * no shooting. */
                if (bubble[i].role == ROLE_CANISTER) {
                    if (try_scoop(&bubble[i]) || bubble[i].pos.z <= Q(1)) {
                        bubble[i].type = SHIP_EMPTY;
                    } else {
                        draw_ship(&bubble[i], flags);
                        flags = 0;
                    }
                    continue;
                }
                /* Asteroids drift past once behind the camera (no AI). */
                if (bubble[i].role == ROLE_ASTEROID && bubble[i].pos.z <= Q(1)) {
                    bubble[i].type = SHIP_EMPTY;
                    continue;
                }
                if (bubble[i].role != ROLE_ASTEROID)
                    ship_ai(&bubble[i]); /* hostile: look-at + close + fire */
                if (fire_active && crosshair_hit(&bubble[i].pos)) {
                    bubble[i].hits -= laser_damage(); /* beam/military hit harder */
                    if (bubble[i].hits <= 0) {
                        spawn_explosion(&bubble[i].pos);
                        bubble[i].type = SHIP_EXPLODING;
                        bubble[i].boom_age = 0;
                        if (bubble[i].role == ROLE_ASTEROID) {
                            /* mining yields scoopable minerals, not a kill */
                            mine_asteroid(bubble[i].pos.x, bubble[i].pos.y, bubble[i].pos.z,
                                          laser_tier);
                        } else {
                            kills++; /* commander rating advances on a kill */
                            /* Murdering an innocent trader is a crime; murdering
                             * the police is a far worse one. Pirates are fair game. */
                            if (bubble[i].role == ROLE_TRADER)
                                commit_crime(12);
                            else if (bubble[i].role == ROLE_POLICE)
                                commit_crime(30);
                            else if (bubble[i].role == ROLE_CONSTRICTOR &&
                                     mission_state == MISSION_ACTIVE) {
                                mission_state = mission_next(mission_state); /* -> DONE */
                                credits += MISSION_BOUNTY;                   /* reward */
                                mission_flash = 40;                          /* debrief */
                                sfx_play(SFX_CHORD, 24);
                            }
                            /* destroyed ships scatter some cargo as loot */
                            if ((rand_word() & 1) == 0)
                                spawn_canister(bubble[i].pos.x, bubble[i].pos.y, bubble[i].pos.z);
                        }
                    }
                }
                if (bubble[i].type == SHIP_ALIVE) {
                    draw_ship(&bubble[i], flags);
                    flags = 0;
                }
            } else if (bubble[i].type == SHIP_EXPLODING) {
                bubble[i].boom_age++;
                if (bubble[i].boom_age >= BOOM_LIFETIME)
                    bubble[i].type = SHIP_EMPTY;
            }
        }
        if (flags == CLEAR)
            clear_only();

        /* Missile lock (reticle on nearest target) + in-flight missile homing. */
        missile_lock = find_target();
        missile_step();

        /* Sun first (filled, biggest), then planet outline, then station
         * (rotating hex), then ships + chrome on top. */
        sun_step_and_draw();
        fuel_scoop_step(); /* skim the sun for fuel (needs Fuel Scoops) */
        planet_step_and_draw();
        station_step_and_draw();
        parts_step_and_draw();
        stars_step_and_draw();
        draw_laser();
        draw_target_lock();
        draw_missile();
        for (int i = 0; i < NSHIPS_MAX; i++) {
            if (bubble[i].type == SHIP_ALIVE)
                draw_enemy_fire(&bubble[i]);
        }
        draw_hit_flash();
        draw_witchspace_flash();
        draw_death_overlay();
        draw_save_flash();
        draw_mission_overlay();
        draw_dock_overlay();
        witchspace_tick();
        dock_tick();
        draw_dashboard();
        death_tick();

        MMIO[FRAME] = 1;
        while (MMIO[FRAME] & 1u) {
        }
        frame_no++;
    }
    return 0;
}
