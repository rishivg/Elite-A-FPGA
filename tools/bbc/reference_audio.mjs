// Observe original generators before the analogue DC restoration filter.
// Splitting each unchanged generator's loop into one-sample calls preserves
// its counter, LFSR, float additions and scheduling.
export function observeDigitalAudio(chip, emit) {
  let pins = null,
    levels = null;
  const generators = [...chip.generators];
  for (let channel = 0; channel < 4; channel++) {
    const original = generators[channel];
    chip.generators[channel] = (ch, out, offset, length) => {
      const amplitude = Math.round(chip.volume[channel] * 252);
      for (let i = 0; i < length; i++) {
        original(ch, out, offset + i, 1);
        const bit = channel === 3 ? chip.lfsr & 1 : +chip.outputBit[channel];
        pins[i] |= bit << channel;
        levels[i] += bit * amplitude;
      }
    };
  }
  const generate = chip.generate.bind(chip);
  chip.generate = (out, offset, length) => {
    pins = new Uint8Array(length);
    levels = new Uint8Array(length);
    generate(out, offset, length);
    emit({ pins, levels, sine: chip.sineOn });
    pins = null;
    levels = null;
  };
}
