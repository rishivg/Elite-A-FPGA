// Physical analogue and FIRE-pin scheduling; no guest memory writes.
export function scheduleAnalogue(scheduler, cpu, events, durationUs) {
  let previous = -1;
  for (const event of events) {
    if (
      !Number.isSafeInteger(event.at_us) ||
      !Number.isSafeInteger(event.at_us * 2) ||
      event.at_us <= previous ||
      event.at_us < 0 ||
      event.at_us >= durationUs ||
      !Array.isArray(event.channels) ||
      event.channels.length !== 4 ||
      event.channels.some((v) => !Number.isInteger(v) || v < 0 || v > 65535) ||
      !Number.isInteger(event.buttons) ||
      event.buttons < 0 ||
      event.buttons > 3
    )
      throw Error("Invalid physical analogue schedule");
    previous = event.at_us;
  }
  let levels = [0x8000, 0x8000, 0x8000, 0x8000];
  for (let channel = 0; channel < 4; channel++)
    cpu.adconverter.setChannelSource(channel, {
      getValue: () => levels[channel],
      dispose: () => {},
    });
  cpu.sysvia.setJoystickButton(0, false);
  cpu.sysvia.setJoystickButton(1, false);
  const epoch = scheduler.epoch,
    captures = [];
  for (const event of events) {
    scheduler
      .newTask(() => {
        const cycle = scheduler.epoch - epoch;
        if (cycle !== event.at_us * 2) throw Error("Analogue input missed its physical clock");
        levels = [...event.channels];
        cpu.sysvia.setJoystickButton(0, !!(event.buttons & 1));
        cpu.sysvia.setJoystickButton(1, !!(event.buttons & 2));
        captures.push({
          requested_cycle: event.at_us * 2,
          cycle,
          channels: [...levels],
          buttons: event.buttons,
        });
      })
      .schedule(event.at_us * 2);
  }
  return captures;
}
