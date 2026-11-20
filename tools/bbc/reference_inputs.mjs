// Physical keys are external stimuli, scheduled on original peripheral clocks.
// Instruction boundaries determine observation time, not input arrival time.
export function scheduleKeys(scheduler, session, keymap, events) {
  let previousTime = -1;
  for (const event of events) {
    if (
      !Number.isSafeInteger(event.at_us) ||
      !Number.isSafeInteger(event.at_us * 2) ||
      event.at_us <= previousTime ||
      !Array.isArray(event.keys) ||
      event.keys.some(
        (key) =>
          !Array.isArray(keymap[key]) ||
          keymap[key].length !== 2 ||
          keymap[key].some(
            (value, i) => !Number.isInteger(value) || value < 0 || value >= (i ? 8 : 16),
          ),
      ) ||
      new Set(event.keys).size !== event.keys.length
    ) {
      throw Error("Invalid physical keyboard schedule");
    }
    previousTime = event.at_us;
  }
  const epoch = scheduler.epoch;
  const captures = [];
  let previousKeys = new Set();
  for (const event of events) {
    scheduler
      .newTask(() => {
        const cycle = scheduler.epoch - epoch;
        if (cycle !== event.at_us * 2) throw Error("Keyboard event missed its physical clock");
        const current = new Set(event.keys);
        for (const key of [...previousKeys].sort())
          if (!current.has(key)) session.keyUpRaw(keymap[key]);
        for (const key of [...current].sort())
          if (!previousKeys.has(key)) session.keyDownRaw(keymap[key]);
        previousKeys = current;
        captures.push({ requested_cycle: event.at_us * 2, cycle, keys: event.keys });
      })
      .schedule(event.at_us * 2);
  }
  return captures;
}
