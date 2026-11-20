"""Guard original bit-method lowering and full-state CRC proof boundaries."""

import copy
import json
import subprocess
import unittest

from tools.bbc.proofs.cpu_reference import fdc_bits
from tools.validation.common import ROOT


class FdcBitTests(unittest.TestCase):
    def body(self):
        state = list(range(1000, 1934))
        names, offset = {}, 0
        for name, width in fdc_bits.FIELDS.items():
            names[name] = {"bits": state[offset : offset + width]}
            offset += width
        for name in (
            "headerPresent",
            "anyHeaderClock",
            "index",
            "arrivingByte",
            "isWrite",
            "isId",
            "isScan",
            "validDataClocks",
            "abortMedia",
        ):
            names[name] = {"bits": ["0"]}
        return {
            "ports": {
                "proofState": {"bits": state, "direction": "input"},
                "proofNext": {"bits": state, "direction": "output"},
            },
            "netnames": names,
        }

    def test_actual_crc_requires_complete_unique_controller_state(self):
        fdc_bits.contract(self.body(), "1'b1")
        for fault in ("missing", "duplicate", "alias", "foreign", "narrow"):
            body = copy.deepcopy(self.body())
            if fault == "missing":
                body["ports"]["proofState"]["bits"].pop()
            elif fault == "duplicate":
                body["ports"]["proofState"]["bits"][-1] = 1000
            elif fault == "alias":
                body["netnames"]["readCrc"] = body["netnames"]["headerCrc"]
            elif fault == "foreign":
                body["netnames"]["headerCrc"]["bits"][0] = 99999
            else:
                body["netnames"]["headerCrc"]["bits"].pop()
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                fdc_bits.contract(body, "1'b1")

    def test_crc_fault_preserves_reset_and_requires_all_five_update_sites(self):
        source = "headerCrc <= 16'hffff;\n" + "headerCrc <= nextValue;\n" * 5
        changed = fdc_bits.crc_fault(source)
        self.assertIn("headerCrc <= 16'hffff;", changed)
        self.assertEqual(changed.count("^ 16'h0001"), 5)
        for count in (0, 4, 6):
            with self.subTest(count=count), self.assertRaises(ValueError):
                fdc_bits.crc_fault("headerCrc <= nextValue;\n" * count)

    def lower(self, cases):
        script = """
import {lowerPure} from './tools/bbc/fdc_bit_source.mjs';
const cases=JSON.parse(await new Promise(resolve=>{let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>resolve(s));}));
const result=cases.map(([body,masks])=>{try{lowerPure(`class Original {static bits(x){${body}}}`,'Original','bits','Kernel',[32],masks);return true;}catch{return false;}});
process.stdout.write(JSON.stringify(result));
"""
        trial = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(trial.stdout)

    def test_lowering_rejects_unmodelled_number_effects_and_control_flow(self):
        valid = "let y=0;for(let i=0;i<8;++i){y<<=1;if(x&128)y|=1;x<<=1;}return y>>>0;"
        invalid = [
            "return x+1;",
            "return x*2;",
            "return x>>1;",
            "this.state=x;return x;",
            "return this.state;",
            "return unknown(x);",
            "while(x){x<<=1;}return x;",
            "for(let i=0;i<x;++i){}return x;",
            "for(let i=0;i<33;++i){}return x;",
            "if(x)return 1;return 0;",
            "var y=x;return y;",
            "return 4294967296;",
        ]
        self.assertEqual(
            self.lower([(valid, [])] + [(s, []) for s in invalid]), [True] + [False] * len(invalid)
        )

    def test_extraction_frame_rejects_unmasked_high_input_reads(self):
        cases = [
            ("return x&65535;", [65535]),
            ("return 32768&x;", [65535]),
            ("return x;", [65535]),
            ("return x&65536;", [65535]),
            ("let y=x;return y&65535;", [65535]),
            ("return x&65535;", [-1]),
            ("return x&65535;", [4294967296]),
        ]
        self.assertEqual(self.lower(cases), [True, True, False, False, False, False, False])


if __name__ == "__main__":
    unittest.main()
