"""Guard source-prefix lowering and the initialized counter frame."""

import copy
import json
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_sync as f
from tools.validation.common import ROOT

SOURCE = """
const State=Object.freeze({syncingForId:1,syncingForData:2,checkIdMarker:3,checkDataMarker:4});
export class IntelFdc {
 _log(value){console.log(value);}
 _setState(state){this._state=state;this._stateCount=0;this._numShifts=0;}
 static extractBits(value){return value&255;}
 _shiftDataBit(bit){const dataBit=bit?1:0;switch(this._state){
  case State.syncingForId:{this._stateCount++;this._shiftRegister=(this._shiftRegister<<1)|dataBit;
   if(this._stateCount>=32 && (this._shiftRegister&3)===3)this._setState(State.checkIdMarker);break;}
  case State.checkIdMarker:{this._shiftRegister=(this._shiftRegister<<1)|dataBit;this._numShifts++;
   if(this._numShifts===16){const data=IntelFdc.extractBits(this._shiftRegister);this._byteCallbackReading(data,255);}break;}
 }}
}
"""


class SyncSourceTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1000 + f.WIDTH))
        names = {}
        offset = 0
        for name, width in f.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        return {
            "ports": {"proofState": {"bits": state}, "proofNext": {"bits": state}},
            "netnames": names,
        }

    def test_prefix_keeps_the_complete_actual_state(self):
        f.indices(self.body())
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["syncCount"]["bits"][0] = body["netnames"]["syncAge"]["bits"][0]
            elif fault == "foreign":
                body["netnames"]["syncCount"]["bits"][0] = 99999
            else:
                body["netnames"]["syncAge"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.indices(body)

    def test_lowering_rejects_unmodelled_effects_and_fallthrough(self):
        sources = [
            SOURCE,
            SOURCE.replace("this._stateCount++;", "this._stateCount+=2;"),
            SOURCE.replace("this._stateCount++;", "this._unknown();"),
            SOURCE.replace("this._stateCount++;", "this._other=1;"),
            SOURCE.replace("checkDataMarker:4", "checkDataMarker:3"),
            SOURCE.replace("console.log(value);", "this._unknown();"),
            SOURCE.replace("this._stateCount++;", "this._log(this._unknown());"),
            SOURCE.replace("break;}", "}"),
            SOURCE.replace("const dataBit=bit?1:0;", "const dataBit=bit?1:0;this._other=1;"),
            SOURCE.replace("this._numShifts++;", "this._numShifts*=2;"),
        ]
        script = """
import {lowerSync,syncFrames} from './tools/bbc/fdc_sync_source.mjs';
const sources=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
process.stdout.write(JSON.stringify({accepted:sources.map(source=>{try{lowerSync(source);return true;}catch{return false;}}),frames:syncFrames()}));
"""
        trial = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            input=json.dumps(sources),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(trial.stdout)
        self.assertEqual(result["accepted"], [True] + [False] * (len(sources) - 1))
        frames = result["frames"]
        self.assertEqual(len(frames), 53)
        ages = {c["age"] for c in frames}
        self.assertTrue(
            {
                2**31 - 1,
                2**31,
                2**32 - 1,
                2**32,
                2**32 + 1,
                2**53 - 3,
                2**53 - 2,
                2**53 - 1,
                2**53,
            }.issubset(ages)
        )
        self.assertEqual({c["count"] for c in frames if c["checking"]}, {0, 1, 2, 3, 7, 14, 15})
        self.assertTrue(any(c["shift"] == 0xFFFFFFFF for c in frames))

    def test_actual_candidate_fault_requires_one_real_output(self):
        source = "  assign syncCandidateData = received;"
        self.assertIn("^ 8'h01", f.fault(source))
        for changed in ("", source + "\n" + source):
            with self.assertRaises(ValueError):
                f.fault(changed)

    def test_counter_frame_requires_actual_zero_reset(self):
        bits = [10, 11, 12]
        names = {
            name: {"bits": [bit]}
            for name, bit in zip(("syncAge", "syncCount", "syncChecking"), bits)
        }
        raw = {
            "modules": {
                "Intel8271": {
                    "netnames": names,
                    "ports": {"reset": {"bits": [1]}},
                    "cells": {
                        "reset": {
                            "type": "$adff",
                            "connections": {"Q": bits, "ARST": [1]},
                            "parameters": {"ARST_POLARITY": "1", "ARST_VALUE": "000"},
                        }
                    },
                }
            }
        }
        f.reset_frame(raw)
        for fault in ("nonzero", "other-reset", "sync-reset", "missing"):
            changed = copy.deepcopy(raw)
            cell = changed["modules"]["Intel8271"]["cells"]["reset"]
            if fault == "nonzero":
                cell["parameters"]["ARST_VALUE"] = "001"
            elif fault == "other-reset":
                cell["connections"]["ARST"] = [2]
            elif fault == "sync-reset":
                cell["type"] = "$dff"
            else:
                cell["connections"]["Q"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                f.reset_frame(changed)


if __name__ == "__main__":
    unittest.main()
