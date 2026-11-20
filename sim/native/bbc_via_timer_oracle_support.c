/* The independent VIA owns all timing, registers and interrupt flags. This
 * headless user-port fixture observes IRQ delivery and aborts on any attempt
 * to call an unattached system-port peripheral. */
#include "bbc.h"
#include "cmos.h"
#include "keyboard.h"
#include "sound.h"
#include "state_6502.h"
#include <stdlib.h>
static int irqLevel;
struct state_6502 *bbc_get_6502(struct bbc_struct *p) {
    (void)p;
    return NULL;
}
void state_6502_set_irq_level(struct state_6502 *p, int irq, int level) {
    (void)p;
    (void)irq;
    irqLevel = level;
}
int state_6502_check_any_irq_firing(struct state_6502 *p) {
    (void)p;
    return irqLevel;
}
void state_6502_fire_irq_timer(struct state_6502 *p) {
    (void)p;
    abort();
}
struct keyboard_struct *bbc_get_keyboard(struct bbc_struct *p) {
    (void)p;
    abort();
}
struct cmos_struct *bbc_get_cmos(struct bbc_struct *p) {
    (void)p;
    abort();
}
struct sound_struct *bbc_get_sound(struct bbc_struct *p) {
    (void)p;
    abort();
}
uint8_t bbc_get_IC32(struct bbc_struct *p) {
    (void)p;
    abort();
}
void bbc_set_IC32(struct bbc_struct *p, uint8_t v) {
    (void)p;
    (void)v;
    abort();
}
uint8_t cmos_get_bus_value(struct cmos_struct *p) {
    (void)p;
    abort();
}
void cmos_update_external_inputs(struct cmos_struct *p, uint8_t b, uint8_t a, uint8_t latch) {
    (void)p;
    (void)b;
    (void)a;
    (void)latch;
    abort();
}
int keyboard_bbc_is_key_pressed(struct keyboard_struct *p, uint8_t row, uint8_t col) {
    (void)p;
    (void)row;
    (void)col;
    abort();
}
int keyboard_bbc_is_key_column_pressed(struct keyboard_struct *p, uint8_t col) {
    (void)p;
    (void)col;
    abort();
}
int keyboard_bbc_is_any_key_pressed(struct keyboard_struct *p) {
    (void)p;
    abort();
}
void sound_sn_set_bus_value(struct sound_struct *p, uint8_t v) {
    (void)p;
    (void)v;
    abort();
}
