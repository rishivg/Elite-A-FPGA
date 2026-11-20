#include "bbc.h"
#include "state_6502.h"
#include <stdlib.h>
/* This fixture connects no system VIA. Any IRQ call is an invalid fixture path,
 * so fail rather than emulate or synthesize an expected device result. */
struct state_6502 *bbc_get_6502(struct bbc_struct *p) {
    (void)p;
    abort();
}
void state_6502_set_irq_level(struct state_6502 *p, int irq, int level) {
    (void)p;
    (void)irq;
    (void)level;
    abort();
}
