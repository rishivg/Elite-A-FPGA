// Shared authentic released-game boot. No RAM state or game assets substituted.
import path from "node:path";
import { pathToFileURL } from "node:url";
export async function bootReleased(config) {
  const { MachineSession } = await import(
    pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
  );
  const session = new MachineSession("B-DFS1.2", { discImage: config.disc, tube: false });
  await session.initialise();
  await session.boot();
  await session.type("*EXEC !BOOT");
  await session.runFor(16000000);
  session.keyDown("KeyN");
  await session.runFor(200000);
  session.keyUp("KeyN");
  await session.runFor(2000000);
  session.keyDown("Space");
  await session.runFor(200000);
  session.keyUp("Space");
  await session.runFor(2000000);
  if (
    Buffer.from(session.readMemory(0x350, 7)).toString() !== "NEWCOME" ||
    Buffer.from(session.readMemory(0x361, 4)).readUInt32BE() !== 5000 ||
    session.readMemory(0x365, 1)[0] !== 60
  )
    throw Error("Released Elite-A did not reach expected initial commander");
  return session;
}
