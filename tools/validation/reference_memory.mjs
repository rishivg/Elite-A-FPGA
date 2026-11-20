// A debugger capture must not acknowledge interrupts or consume FDC data.
// MachineSession.readMemory() performs guest bus reads, including MMIO effects.
// The pinned CPU's peekmem() reads RAM/ROM and represents device locations as FF.
export function memorySnapshot(cpu, address = 0, length = 65536) {
  if (
    !Number.isInteger(address) ||
    !Number.isInteger(length) ||
    address < 0 ||
    length < 0 ||
    address + length > 65536
  )
    throw Error("Invalid debugger memory range");
  return Buffer.from(Array.from({ length }, (_, i) => cpu.peekmem(address + i)));
}
