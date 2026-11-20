// Declared standard FM transport fixtures; the original FDC/flux code is unchanged.
export function configureDrive(fdc, profile) {
  if (!["original-spindle", "constant-fm", "clocked-fm"].includes(profile))
    throw Error("Unknown drive profile");
  if (profile === "original-spindle") return;
  for (const drive of fdc._drives) {
    Object.defineProperty(drive, "speed", { get: () => 1 });
    if (profile !== "clocked-fm") continue;
    const fraction = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(drive),
      "positionFraction",
    );
    if (!fraction?.get || !fraction?.set) throw Error("Original drive position API is unavailable");
    const standard = () => {
      if (drive.trackLength !== 3125) throw Error("Clocked FM requires a standard 3125-cell track");
    };
    Object.defineProperty(drive, "positionTime", {
      get: () => {
        standard();
        return drive.headPosition * 128;
      },
    });
    Object.defineProperty(drive, "positionFraction", {
      get: () => {
        standard();
        let value = fraction.get.call(drive);
        // Directed rounding preserves an integer cell through the original
        // fraction setter during equal-length track/side changes. The correction
        // is at floating-point precision, rather than a physical clock offset.
        if (Math.trunc(value * drive.trackLength) < drive.headPosition)
          value += Math.abs(value) * Number.EPSILON;
        return value;
      },
      set: (value) => fraction.set.call(drive, value),
    });
  }
}
