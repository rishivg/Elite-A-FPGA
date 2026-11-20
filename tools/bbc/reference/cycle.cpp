#define CHIPS_IMPL
#include "m6502.h"
#include "nmi.h"
#include "node_api.h"
static m6502_t cpu;
static napi_value init(napi_env env, napi_callback_info info) {
    m6502_desc_t desc{};
    uint32_t pins = m6502_init(&cpu, &desc);
    napi_value value;
    napi_create_uint32(env, pins, &value);
    return value;
}
static napi_value tick(napi_env env, napi_callback_info info) {
    size_t n = 1;
    napi_value arg;
    napi_get_cb_info(env, info, &n, &arg, nullptr, nullptr);
    uint32_t pins;
    if (n != 1 || napi_get_value_uint32(env, arg, &pins) != napi_ok) {
        napi_throw_type_error(env, nullptr, "Expected 6502 pins");
        return nullptr;
    }
    napi_value value;
    napi_create_uint32(env, uint32_t(m6502_tick(&cpu, pins)), &value);
    return value;
}
static napi_value sample_nmi(napi_env env, napi_callback_info info) {
    size_t n = 1;
    napi_value arg;
    napi_get_cb_info(env, info, &n, &arg, nullptr, nullptr);
    bool level;
    if (n != 1 || napi_get_value_bool(env, arg, &level) != napi_ok) {
        napi_throw_type_error(env, nullptr, "Expected NMI level");
        return nullptr;
    }
    bbc_sample_nmi(&cpu, level);
    napi_value result;
    napi_get_undefined(env, &result);
    return result;
}
static napi_value regs(napi_env env, napi_callback_info info) {
    napi_value result;
    napi_create_object(env, &result);
    const char *names[] = {"a", "x", "y", "s", "p", "pc"};
    unsigned values[] = {cpu.A, cpu.X, cpu.Y, cpu.S, cpu.P, cpu.PC};
    for (int i = 0; i < 6; i++) {
        napi_value value;
        napi_create_uint32(env, values[i], &value);
        napi_set_named_property(env, result, names[i], value);
    }
    return result;
}
static napi_value save(napi_env env, napi_callback_info info) {
    m6502_t snapshot = cpu;
    m6502_snapshot_onsave(&snapshot);
    napi_value result;
    napi_create_buffer_copy(env, sizeof(snapshot), &snapshot, nullptr, &result);
    return result;
}
static napi_value restore(napi_env env, napi_callback_info info) {
    size_t n = 1;
    napi_value arg;
    napi_get_cb_info(env, info, &n, &arg, nullptr, nullptr);
    bool buffer = false;
    if (n == 1)
        napi_is_buffer(env, arg, &buffer);
    void *data = nullptr;
    size_t size = 0;
    if (!buffer || napi_get_buffer_info(env, arg, &data, &size) != napi_ok || size != sizeof(cpu)) {
        napi_throw_type_error(env, nullptr, "Invalid own CPU state buffer");
        return nullptr;
    }
    m6502_t snapshot;
    memcpy(&snapshot, data, sizeof(snapshot));
    m6502_snapshot_onload(&snapshot, &cpu);
    cpu = snapshot;
    napi_value result;
    napi_get_undefined(env, &result);
    return result;
}
static napi_value module(napi_env env, napi_value exports) {
    napi_property_descriptor methods[] = {
        {"init", nullptr, init, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"tick", nullptr, tick, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"sampleNmi", nullptr, sample_nmi, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"regs", nullptr, regs, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"save", nullptr, save, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"restore", nullptr, restore, nullptr, nullptr, nullptr, napi_default, nullptr}};
    napi_define_properties(env, exports, sizeof(methods) / sizeof(methods[0]), methods);
    return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, module)
