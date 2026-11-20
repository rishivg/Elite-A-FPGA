"""Reject changes outside the guarded write-run source effects."""

import ast
import json
import subprocess
import unittest

from tools.validation.common import ROOT

namespace = {"__name__": "gap_fixture"}
exec(
    compile(
        ast.parse((ROOT / "tests/validation/test_c_fdc_gap_source.py").read_text()),
        "gap_fixture",
        "exec",
    ),
    namespace,
)
SOURCE = (
    namespace["SOURCE"]
    .replace("internalGap2Skip:21", "internalGap2Skip:21,internalWriteRunData:21")
    .replace("select_1:128", "select_1:128,writeEnable:1")
    .replace("syncingForData:7", "syncingForData:7,writeRun:12,writeDataMark:13")
    .rstrip()[:-1]
    + """
 _startIrqCallbacks(){this._regs[Registers.internalStatus]|=0x30;}
 _doWriteRun(callContext,byte){this._mmioData=byte&0xff;this._mmioClocks=0xff;this._crc=IbmDiscFormat.crcAddByte(this._crc,this._mmioData);this._regs[Registers.internalWriteRunData]=this._mmioData;this._driveOutRaise(DriveOut.writeEnable);this._callContext=callContext;this._setState(State.writeRun);}
 _callbackWriteRun(){if(!this._decrementCounter()){this._mmioData=this._regs[Registers.internalWriteRunData];this._crc=IbmDiscFormat.crcAddByte(this._crc,this._mmioData);return;}switch(this._callContext){case Call.write:this._mmioData=0;this._startIrqCallbacks();this._setState(State.writeDataMark);break;}}
}
"""
)


class WriteGapSourceTests(unittest.TestCase):
    def lower(self, sources):
        script = """import {lowerWriteGap} from './tools/bbc/fdc_write_gap_source.mjs';const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));process.stdout.write(JSON.stringify(sources.map(source=>{try{lowerWriteGap(source);return true;}catch{return false;}})));"""
        r = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(sources),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(r.stdout)

    def test_aliases_and_initialization_are_guarded(self):
        cases = [
            SOURCE,
            SOURCE.replace("internalWriteRunData:21", "internalWriteRunData:20"),
            SOURCE.replace("writeEnable:1", "writeEnable:2"),
            SOURCE.replace("writeRun:12", "writeRun:11"),
            SOURCE.replace("writeDataMark:13", "writeDataMark:14"),
            SOURCE.replace(
                "this._regs[Registers.internalWriteRunData]=this._mmioData;",
                "this._regs[Registers.internalWriteRunData]=0;",
            ),
            SOURCE.replace(
                "_doWriteRun(callContext,byte){", "_doWriteRun(callContext,byte){this._unknown();"
            ),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_counter_data_and_irq_effects_are_guarded(self):
        cases = [
            SOURCE,
            SOURCE.replace("if(!this._decrementCounter())", "if(this._decrementCounter())"),
            SOURCE.replace(
                "this._mmioData=this._regs[Registers.internalWriteRunData]",
                "this._mmioData=this._regs[0]",
            ),
            SOURCE.replace("|=0x30", "|=0x20"),
            SOURCE.replace("this._setState(State.writeDataMark)", "this._setState(State.writeRun)"),
            SOURCE.replace("_callbackWriteRun(){", "_callbackWriteRun(){this._unknown();"),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))

    def test_effectful_and_duplicate_case_labels_are_rejected(self):
        cases = [
            SOURCE,
            SOURCE.replace("case Call.write:", "case this._unknown(): case Call.write:"),
            SOURCE.replace("case Call.write:", "case 5: case Call.write:"),
            SOURCE.replace("case Call.write:", "case Call.read:"),
            SOURCE.replace("case Call.write:", "case Call.write: case Call.write:"),
        ]
        self.assertEqual(self.lower(cases), [True] + [False] * (len(cases) - 1))


if __name__ == "__main__":
    unittest.main()
