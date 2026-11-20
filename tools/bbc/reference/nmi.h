// Physical NMI capture between accepted NMOS CPU cycles.
// The edge detector and latch bit come from the maintained m6502_tick prefix.
// Pipeline aging belongs to tick(), so it advances only on accepted cycles.
#ifndef BBC_REFERENCE_NMI_H
#define BBC_REFERENCE_NMI_H
static void bbc_sample_nmi(m6502_t *c, bool level) {
    uint64_t pins = (c->PINS & ~M6502_NMI) | (level ? M6502_NMI : 0);
    if (0 != ((pins & (pins ^ c->PINS)) & M6502_NMI)) {
        c->nmi_pip |= 0x100;
    }
    c->PINS = pins;
}
#endif
