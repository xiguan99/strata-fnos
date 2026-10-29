#!/usr/bin/env python3
"""Offline check of the card-role mapping that ships in host/server/native-control.mjs.

The mapping decides which physical cards the engine is given (`CUDA_VISIBLE_DEVICES`), so it must hold for layouts other
than this NAS: Main on card 0 of one card, Main + Helper, Main on the third card. The test lifts `roleList` and
`rolesToDevices` verbatim out of the shipped file, so a change there that breaks a layout fails the build (no GPU, no
model, no network involved).
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "..", "fnpack", "app", "host", "server", "native-control.mjs")
NODE = os.environ.get("NODE_BIN") or "/var/apps/nodejs_v24/target/bin/node"

ROLES = """test("two cards, Main is 0 - the engine gets one card, numbered 0", () => {
  const r = rolesToDevices({"0": "main", "1": "off"});
  assert.deepEqual(r.devices, [0]);
  assert.equal(r.roles["0"], "main");
});
test("two cards, Main is 1, the other Off", () => {
  assert.deepEqual(rolesToDevices({"0": "off", "1": "main"}).devices, [1]);
});
test("Main + Helper: Helper first, Main last, both visible", () => {
  assert.deepEqual(rolesToDevices({"1": "main", "0": "helper"}).devices, [0, 1]);
  assert.deepEqual(rolesToDevices({"3": "main", "2": "helper"}).devices, [2, 3]);
});
test("Off cards never appear", () => {
  assert.deepEqual(rolesToDevices({"0": "off", "1": "off", "2": "main"}).devices, [2]);
});
test("no Main, or two Mains, describes no start", () => {
  assert.equal(rolesToDevices({"0": "helper", "1": "off"}), null);
  assert.equal(rolesToDevices({"0": "main", "1": "main"}), null);
  assert.equal(rolesToDevices({}), null);
  assert.equal(rolesToDevices(undefined), null);
});
test("three-card machine: the Main card at any index", () => {
  for (const main of [0, 1, 2]) {
    const roles = {"0": "off", "1": "off", "2": "off"};
    roles[String(main)] = "main";
    assert.deepEqual(rolesToDevices(roles).devices, [main]);
  }
});
"""


def block(lines, startswith):
    at = next(i for i, line in enumerate(lines) if line.startswith(startswith))
    out = [lines[at]]
    if "function" in startswith:
        k = at + 1
        while True:
            out.append(lines[k])
            if lines[k].rstrip() == " }":
                break
            k += 1
    return "\n".join(out)


def main():
    with open(SOURCE, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    cases = "\n".join([
        'import {test} from "node:test";',
        'import assert from "node:assert/strict";',
        "/* Lifted verbatim from fnpack/app/host/server/native-control.mjs. */",
        block(lines, " const roleList="),
        block(lines, " function rolesToDevices("),
        ROLES,
    ])
    with tempfile.TemporaryDirectory() as tmp:
        case = os.path.join(tmp, "gpu-roles.test.mjs")
        with open(case, "w", encoding="utf-8") as handle:
            handle.write(cases)
        done = subprocess.run([NODE, "--test", case], capture_output=True, text=True)
    tail = [line for line in done.stdout.splitlines() if line.startswith(("✔", "✖", "ℹ pass", "ℹ fail"))]
    for line in tail:
        print("  " + line)
    if done.returncode != 0:
        print(done.stdout[-2000:] + done.stderr[-2000:], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
