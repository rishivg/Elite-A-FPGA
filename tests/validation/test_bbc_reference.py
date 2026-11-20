"""Passive sound observation and independent reference CPU serialization."""

import subprocess
import unittest

from tools.bbc import cycle_reference
from tools.validation.common import ROOT, source_root


class OriginalReferenceTests(unittest.TestCase):
    def test_field_digest_matches_native_aperture_including_odd_pixels(self):
        import hashlib
        import tempfile
        from pathlib import Path

        import numpy as np

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rgba = np.fromfunction(
                lambda y, x, c: (y * 19 + x * 13 + c * 37) % 256, (625, 1024, 4), dtype=int
            ).astype("u1")
            rgb = np.zeros((512, 1024, 3), dtype="u1")
            rgb[:248, :512] = rgba[80:576:2, 264:776, :3]
            expected = hashlib.sha256(rgb[:248, :512].tobytes()).hexdigest()
            (directory / "original.rgba").write_bytes(rgba.tobytes())
            (directory / "native.rgb").write_bytes(rgb.tobytes())
            source = directory / "hash.cpp"
            source.write_text("""
#include \"bbc_frame_hash.h\"
#include <fstream>
#include <iostream>
int main(int argc,char**argv){std::ifstream f(argv[1],std::ios::binary);std::vector<uint8_t> rgb{std::istreambuf_iterator<char>(f),{}};BbcFrameHash hash;std::cout<<hash.digest(rgb)<<\"\\n\";rgb[3]^=1;std::cout<<hash.digest(rgb)<<\"\\n\";}
""")
            subprocess.run(
                [
                    "c++",
                    "-O2",
                    "-std=c++17",
                    "-I" + str(ROOT / "sim/native"),
                    str(source),
                    "-lcrypto",
                    "-o",
                    str(directory / "hash"),
                ],
                check=True,
                timeout=30,
            )
            native = subprocess.check_output(
                [str(directory / "hash"), str(directory / "native.rgb")], text=True
            ).splitlines()
            self.assertEqual(native[0], expected)
            self.assertNotEqual(native[1], expected)
            script = """
import fs from 'node:fs';import {pathToFileURL} from 'node:url';
const {bitmapFieldHash}=await import(pathToFileURL(process.argv[2]));
console.log(bitmapFieldHash(fs.readFileSync(process.argv[3])));
"""
            original = subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-",
                    str(ROOT / "tools/bbc/reference_frame.mjs"),
                    str(directory / "original.rgba"),
                ],
                input=script,
                text=True,
            ).strip()
            self.assertEqual(original, expected)

    def test_cycle_bus_stretch_matches_original_model_b_clock_phase(self):
        self.assertTrue(cycle_reference.current())
        script = r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [root,adapter,library]=process.argv.slice(2);
const {Cpu6502}=await import(pathToFileURL(root+'/src/6502.js'));
const {cycleCpu}=await import(pathToFileURL(adapter));
for(const start of [0,1,2,3]){
 const memory=new Uint8Array(65536);memory.fill(0xea);
 memory[0xfffc]=0;memory[0xfffd]=4;
 memory.set([0xa9,0x55,0x8d,0x40,0xfe,0xad,0x40,0xfe,0xea,0x4c,0x00,0x04],0x400);
 let pending=null,accesses=0;
 const cpu={model:{isMaster:false},currentCycles:start,targetCycles:start,interrupt:0,nmi:false,
            p:{setFromByte:()=>{}},polltime:n=>cpu.currentCycles+=n,peekmem:a=>memory[a]};
 const isSlow=Cpu6502.prototype.buildIs1MHzAccess.call(cpu);
 cpu.is1MHzAccess=(a,w)=>{if(isSlow(a,w))pending={currentCycles:cpu.currentCycles,address:a};return isSlow(a,w);};
 function observed(a){
  if(!isSlow(a,false))return;
  assert.equal(pending.address,a);
  const original={currentCycles:pending.currentCycles,is1MHzAccess:()=>true,polltime(n){this.currentCycles+=n;}};
  Cpu6502.prototype.polltimeAddr.call(original,1,a,false);
  assert.equal(cpu.currentCycles,original.currentCycles,'Slow bus must preserve the original clock phase');
  assert.equal(cpu.currentCycles&1,1);pending=null;accesses++;
 }
 cpu.readmem=a=>{observed(a);return memory[a];};
 cpu.writemem=(a,d)=>{observed(a);memory[a]=d;};
 cycleCpu(cpu,library);cpu.execute(200);
 assert.ok(accesses>=10);assert.equal(memory[0xfe40],0x55);
}
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(source_root("jsbeeb")),
                str(ROOT / "tools/bbc/reference_cycle.mjs"),
                str(cycle_reference.BINARY),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_cycle_adapter_uses_one_builtin_reset_sequence(self):
        self.assertTrue(cycle_reference.current())
        script = r"""
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const [adapter,library]=process.argv.slice(2);
const require=createRequire(import.meta.url),engine=require(library);
const {cycleCpu}=await import(pathToFileURL(adapter));
const memory=new Uint8Array(65536);memory.fill(0xea);
memory.set([0xea,0x4c,0,4],0x400);memory[0xfffc]=0;memory[0xfffd]=4;
let pins=engine.init();const expected=[];
for(let cycle=1;cycle<=40;cycle++){
 const address=pins&65535,write=!(pins&0x01000000);
 expected.push([cycle,address,write]);
 if(write)memory[address]=(pins>>>16)&255;
 else pins=((pins&~0x00ff0000)|(memory[address]<<16))>>>0;
 pins=engine.tick(pins);
}
assert.equal(expected.filter(r=>r[1]===0xfffc).length,1);
assert.equal(expected.filter(r=>r[1]===0xfffd).length,1);
assert.equal(expected.filter(r=>r[2]).length,0,'RESET stack cycles must read');
const actual=[];
const cpu={model:{isMaster:false},currentCycles:0,targetCycles:0,interrupt:0,nmi:false,
 p:{setFromByte:()=>{}},polltime:n=>cpu.currentCycles+=n,is1MHzAccess:()=>false,peekmem:a=>memory[a],
 readmem:a=>{actual.push([cpu.currentCycles,a,false]);return memory[a];},
 writemem:(a,d)=>{actual.push([cpu.currentCycles,a,true]);memory[a]=d;}};
cycleCpu(cpu,library);cpu.execute(40);assert.deepEqual(actual,expected);
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(ROOT / "tools/bbc/reference_cycle.mjs"),
                str(cycle_reference.BINARY),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_cycle_adapter_retains_nmi_pulses_during_slow_accesses(self):
        self.assertTrue(cycle_reference.current())
        script = r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [adapter,library]=process.argv.slice(2);
const {cycleCpu}=await import(pathToFileURL(adapter));
for(const start of [0,1,2,3]){
 const memory=new Uint8Array(65536);memory.fill(0xea);
 memory.set([0xa9,0x55,0x8d,0x40,0xfe,0x4c,0,4],0x400);
 memory.set([0xee,0,0x20,0x40],0x500);
 memory[0x2000]=0;memory[0xfffc]=0;memory[0xfffd]=4;
 memory[0xfffa]=0;memory[0xfffb]=5;
 let slowStart=null,pulses=0;
 const cpu={currentCycles:start,targetCycles:start,interrupt:0,nmi:false,
 p:{setFromByte:()=>{}},peekmem:a=>memory[a],
 is1MHzAccess(a){
  if(a===0xfe40){slowStart=cpu.currentCycles;return true;}
  return false;
 },
 polltime(n){
  cpu.currentCycles+=n;
  cpu.nmi=slowStart!==null && cpu.currentCycles===slowStart+1;
  if(cpu.nmi)pulses++;
 },
 readmem:a=>memory[a],writemem:(a,d)=>{memory[a]=d;}};
 const control=cycleCpu(cpu,library);cpu.execute(250);
 assert.ok(pulses>0);assert.ok(memory[0x2000]>0,'A pulse entirely inside the wait must reach the NMI handler');
 // Sampling state lives in the native snapshot; no instruction state is
 // reconstructed from the JS register view during restore.
 const saved=control.snapshot(),image=memory.slice(),cycles=cpu.currentCycles;
 cpu.execute(60);const expected=memory.slice(),final=control.snapshot();
 memory.set(image);cpu.currentCycles=cycles;cpu.targetCycles=cycles;
 cpu.nmi=false;slowStart=null;control.restore(saved);cpu.execute(60);
 assert.deepEqual(memory,expected);assert.deepEqual(control.snapshot(),final);
}
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(ROOT / "tools/bbc/reference_cycle.mjs"),
                str(cycle_reference.BINARY),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_digital_audio_observation_preserves_original_device_and_samples(self):
        script = r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [root,observer]=process.argv.slice(2);
const {SoundChip}=await import(pathToFileURL(root+'/src/soundchip.js'));
const {Scheduler}=await import(pathToFileURL(root+'/src/scheduler.js'));
const {observeDigitalAudio}=await import(pathToFileURL(observer));
function replay(observe){
 const chunks=[],pins=[],levels=[];
 const chip=new SoundChip(buffer=>chunks.push(Buffer.from(buffer.buffer)));
 const scheduler=new Scheduler();chip.setScheduler(scheduler);
 if(observe)observeDigitalAudio(chip,chunk=>{pins.push(Buffer.from(chunk.pins));levels.push(Buffer.from(chunk.levels));});
 chip.reset(true);
 const writes=[0x83,0x04,0x90,0xa6,0x03,0xb3,0xc4,0x02,0xd8,0xe4,0xf2,0xe7,0xe3,0xcf,0x3f,0xe0,0xff];
 for(let i=0;i<writes.length;i++){scheduler.polltime(1250+i*13);chip.poke(writes[i]);}
 scheduler.polltime(50000);chip.catchUp();chunks.push(Buffer.from(chip.buffer.slice(0,chip.position).buffer));
 return {state:chip.snapshotState(),audio:Buffer.concat(chunks),pins:Buffer.concat(pins),levels:Buffer.concat(levels)};
}
const plain=replay(false),observed=replay(true);
assert.deepEqual(observed.state,plain.state);assert.deepEqual(observed.audio,plain.audio);
assert.equal(observed.pins.length,observed.audio.length/4);assert.ok(observed.levels.some(n=>n>0));
"""
        subprocess.run(
            [
                "node",
                "--input-type=module",
                "-",
                str(source_root("jsbeeb")),
                str(ROOT / "tools/bbc/reference_audio.mjs"),
            ],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_qualified_cycle_cpu_own_state_roundtrip(self):
        self.assertTrue(
            cycle_reference.current(), "Run make bbc-reference-test to qualify the cycle oracle"
        )
        script = r"""
const assert=require('node:assert/strict'),c=require(process.argv[2]);
let pins=c.init();for(let i=0;i<8;i++)pins=c.tick(pins);
const saved=c.save(),before=c.regs();let p=pins;
for(let i=0;i<50;i++)p=c.tick(p);
const after=c.regs();c.restore(saved);assert.deepEqual(c.regs(),before);p=pins;
for(let i=0;i<50;i++)p=c.tick(p);
assert.deepEqual(c.regs(),after);assert.throws(()=>c.restore(Buffer.alloc(0)));assert.throws(()=>c.restore('invalid'));
"""
        subprocess.run(
            ["node", "-", str(cycle_reference.BINARY)],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
