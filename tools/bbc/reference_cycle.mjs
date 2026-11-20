// Cycle-stepped independent CPU; original BBC memory/peripherals remain intact.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
export function cycleCpu(cpu, library) {
  const engine = require(library);
  let pins = engine.init(),
    remaining = 0,
    slots = 0,
    sampledNmi = false;
  const copyRegisters = () => {
    const r = engine.regs();
    cpu.a = r.a;
    cpu.x = r.x;
    cpu.y = r.y;
    cpu.s = r.s;
    cpu.pc = r.pc;
    cpu.p.setFromByte(r.p);
  };
  cpu.execute = (count) => {
    if (!Number.isSafeInteger(count) || count < 0) throw Error("Invalid CPU cycle budget");
    cpu.halted = false;
    cpu.targetCycles += count;
    while (!cpu.halted && cpu.currentCycles < cpu.targetCycles) {
      if (remaining === 0) {
        const address = pins & 65535;
        // Original Model B polltimeAddr adds 1 + ((cycles ^ currentCycles) & 1)
        // to each slow access. With one bus cycle this finishes on an odd
        // absolute 2 MHz clock, after two or three slots, preserving bus phase.
        remaining = cpu.is1MHzAccess(address, !(pins & 0x01000000))
          ? 2 + ((1 ^ cpu.currentCycles) & 1)
          : 1;
        if (pins & 0x02000000 && slots > 0) {
          copyRegisters();
          if (cpu._debugInstruction && cpu._debugInstruction(address, cpu.peekmem(address))) {
            cpu.halted = true;
            break;
          }
        }
      }
      // One original peripheral/scheduler clock for every physical 2 MHz slot.
      // Slow accesses complete on 1 MHz edges, after at least two slots.
      cpu.polltime(1);
      slots++;
      // NMI is asynchronous: retain its edge during a slow bus transaction,
      // including an edge withdrawn by the following FDC data/result access.
      // The native sampler leaves instruction state and pipeline age intact.
      const nmi = !!cpu.nmi;
      if (nmi !== sampledNmi) {
        engine.sampleNmi(nmi);
        sampledNmi = nmi;
      }
      if (--remaining === 0) {
        const address = pins & 65535;
        if (pins & 0x01000000) pins = ((pins & ~0x00ff0000) | (cpu.readmem(address) << 16)) >>> 0;
        else cpu.writemem(address, (pins >>> 16) & 255);
        // init() supplies RES and the core clears it when reset starts.
        // Reasserting it for eight slots runs a second reset sequence while
        // the original BBC devices keep ticking, delaying the ROM startup.
        pins =
          ((pins & ~0x0c000000) | (cpu.interrupt ? 0x04000000 : 0) | (cpu.nmi ? 0x08000000 : 0)) >>>
          0;
        pins = engine.tick(pins);
        sampledNmi = !!(pins & 0x08000000);
      }
    }
    copyRegisters();
    return !cpu.halted;
  };
  return {
    // Digital RES requests finish the in-flight bus transaction/instruction.
    // Original peripherals keep ticking; the BBC resets the FDC on restart.
    // Do not call cpu.reset(false): it replaces the JS register view while the
    // independently clocked pin engine still owns the actual CPU state.
    restart: () => {
      pins = (pins | 0x40000000) >>> 0;
      cpu.fdc.reset();
    },
    snapshot: () => ({ schema: 2, pins, remaining, slots, engine: engine.save() }),
    restore: (s) => {
      if (
        s?.schema !== 2 ||
        !Number.isInteger(s.pins) ||
        s.pins < 0 ||
        s.pins > 0xffffffff ||
        !Number.isInteger(s.remaining) ||
        s.remaining < 0 ||
        s.remaining > 3 ||
        !Number.isSafeInteger(s.slots) ||
        s.slots < 0
      )
        throw Error("Invalid own cycle-CPU snapshot");
      engine.restore(s.engine);
      pins = s.pins;
      remaining = s.remaining;
      slots = s.slots;
      // The native snapshot owns the previous level. Resample on the first
      // restored slot so this call-suppression cache needs no snapshot field.
      sampledNmi = null;
      copyRegisters();
    },
  };
}
