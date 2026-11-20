/* The unmodified ADC uses its public externally-clocked path. These bindings
 * provide allocation, an unused timer registration, and the observable CB1 pin.
 * They never supply or change conversion results. */
#include "log.h"
#include "timing.h"
#include "util.h"
#include "via.h"
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
int oracle_cb1;
void *util_mallocz(size_t size) { return calloc(1, size); }
void util_free(void *p) { free(p); }
uint32_t timing_register_timer(struct timing_struct *p, const char *n, void *c, void *o) {
    (void)p;
    (void)n;
    (void)c;
    (void)o;
    return 0;
}
int64_t timing_stop_timer(struct timing_struct *p, uint32_t i) {
    (void)p;
    (void)i;
    abort();
}
int64_t timing_start_timer_with_value(struct timing_struct *p, uint32_t i, int64_t v) {
    (void)p;
    (void)i;
    (void)v;
    abort();
}
void timing_sync_countdown(struct timing_struct *p, int64_t v) {
    (void)p;
    (void)v;
    abort();
}
int64_t timing_get_countdown(struct timing_struct *p) {
    (void)p;
    abort();
}
void via_set_CB1(struct via_struct *p, int level) {
    (void)p;
    oracle_cb1 = level;
}
void log_do_log_max_count(uint32_t *p, int m, int s, const char *text, ...) {
    (void)p;
    (void)m;
    (void)s;
    (void)text;
    abort();
}
