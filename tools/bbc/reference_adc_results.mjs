// NEC-qualified register semantics and completed-result bits; nominal original scheduling remains.
import path from "node:path";
import { pathToFileURL } from "node:url";

export const RESULT_PROFILE = "bbc-result-bits";
export const ADC_PROFILES = ["original", RESULT_PROFILE];

export function resultAdcClass(Adc, centreValue = 0x8000) {
  return class ResultAdc extends Adc {
    constructor(...args) {
      super(...args);
      this.command = 0;
    }
    write(address, value) {
      if ((address & 3) !== 0) return;
      const retained = this.status & 0x3f;
      this.command = value & 0x0f;
      super.write(address, value);
      // NEC status latch retains the previous completed conversion fields.
      this.status = retained | 0x80;
    }
    read(address) {
      switch (address & 3) {
        case 0:
          return this.status;
        case 1:
          // The completed data remains readable; only EOC is acknowledged.
          this.status |= 0x80;
          this.sysvia.setcb1(true);
          return this.high;
        default:
          return this.low;
      }
    }
    snapshotState() {
      return { ...super.snapshotState(), command: this.command };
    }
    restoreState(state) {
      if (state.taskOffset !== null && !Number.isInteger(state.command))
        throw Error("ADC snapshot lacks its pending conversion command");
      super.restoreState(state);
      this.command = state.command ?? 0;
    }
    reset() {
      super.reset();
      this.command = 0;
    }
    onComplete() {
      const control = this.command;
      const channel = control & 3;
      const source = this.channelSources[channel];
      const value = this.fixedValue ?? (source ? source.getValue(channel) : centreValue);
      if (!Number.isInteger(value) || value < 0 || value > 0xffff)
        throw Error("ADC source must provide an unsigned 16-bit value");
      // BBC result MSBs occupy status 5:4; data registers expose 12 aligned bits.
      // These fields are checked over the full domain against independent C ADC.
      this.status = control | 0x40 | ((value >>> 10) & 0x30);
      this.high = (value >>> 8) & 0xff;
      this.low = value & 0xf0;
      this.sysvia.setcb1(false);
    }
  };
}

export async function configureAdcResults(cpu, root, profile) {
  if (!ADC_PROFILES.includes(profile)) throw Error("Unknown ADC result profile");
  if (profile === "original") return;
  const { Adc, AdcCentreValue } = await import(pathToFileURL(path.join(root, "src/adc.js")));
  const previous = cpu.adconverter;
  if (previous.task.scheduled()) throw Error("Configure ADC before conversions begin");
  const QualifiedAdc = resultAdcClass(Adc, AdcCentreValue);
  const adc = new QualifiedAdc(cpu.sysvia, cpu.scheduler);
  adc.restoreState(previous.snapshotState());
  adc.setFixedValue(previous.fixedValue);
  for (let channel = 0; channel < 4; channel++)
    adc.setChannelSource(channel, previous.getChannelSource(channel));
  previous.task.cancel();
  cpu.adconverter = adc;
}
