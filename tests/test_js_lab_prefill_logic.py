"""化验预填纯逻辑的行为测试（通过 Node 运行，未安装 Node 时跳过）。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def test_lab_prefill_core_in_node():
    if not NODE:
        pytest.skip("node 不可用，跳过 JS 纯逻辑测试")
    script = r"""
const assert = require('assert');
const core = require('./web/lab-prefill-core.js');

const items = [
  { item: '血小板平均体积', value: 10.5, unit: 'fL' },
  { item: '血小板压积', value: 0.21, unit: '%' },
  { item: 'AST/ALT', value: 1.2, unit: '' },
  { item: '谷草转氨酶', value: 35, unit: 'U/L' },
  { item: '血小板计数', value: 156, unit: '×10⁹/L' },
  { item: '前白蛋白(PA)', value: 250, unit: 'mg/L' },
  { item: '白蛋白', value: 32, unit: 'g/L' },
  { item: '直接胆红素', value: 12, unit: 'μmol/L' },
  { item: '总胆红素', value: 45, unit: 'μmol/L' },
  { item: '尿素', value: 8.2, unit: 'mmol/L' },
  { item: '尿酸', value: 420, unit: 'μmol/L' },
  { item: '糖化血红蛋白', value: 7.1, unit: '%' },
  { item: 'INR', value: 2.1, unit: '' },
];

assert.strictEqual(core.labNumber(null), null);
assert.strictEqual(core.labNumber({ value: 0 }), 0);
assert.strictEqual(core.labNumber({ value: '' }), null);
assert.strictEqual(core.findLabItem(items, 'albumin').item, '白蛋白');
assert.strictEqual(core.findLabItem(items, 'bilirubin').item, '总胆红素');
assert.strictEqual(core.findLabItem(items, 'urea').item, '尿素');
assert.strictEqual(core.findLabItem(items, 'ast').item, '谷草转氨酶');
assert.strictEqual(
  core.findLabItem(items, 'platelets').item, '血小板计数'
);
assert.strictEqual(
  core.findLabItem(items, 'hba1c').item, '糖化血红蛋白'
);
assert.strictEqual(core.labAlbuminGL({ value: 3.2, unit: 'g/dL' }).value, 32);
assert.strictEqual(
  core.labAlbuminGL({ value: 320, unit: 'mg/dL' }).value, 3.2
);
assert.strictEqual(
  core.labUreaMmolL({ item: '尿素氮', value: 30, unit: 'mg/dL' })
    .value.toFixed(1),
  '10.7'
);
assert.strictEqual(
  core.labUreaMmolL({ item: '尿素', value: 30, unit: 'mg/dL' })
    .value.toFixed(1),
  '5.0'
);
assert.strictEqual(
  core.labBilirubinUmolL({ value: 2.0, unit: 'mg/dL' })
    .value.toFixed(1),
  '34.2'
);
assert.deepStrictEqual(
  [
    core.childPughBilirubinScore(33.9),
    core.childPughBilirubinScore(34),
    core.childPughBilirubinScore(51),
    core.childPughBilirubinScore(51.1),
  ],
  [1, 2, 2, 3]
);
assert.deepStrictEqual(
  [
    core.childPughAlbuminScore(36),
    core.childPughAlbuminScore(35),
    core.childPughAlbuminScore(28),
    core.childPughAlbuminScore(27.9),
  ],
  [1, 2, 2, 3]
);
assert.deepStrictEqual(
  [
    core.childPughInrScore(1.6),
    core.childPughInrScore(1.7),
    core.childPughInrScore(2.3),
    core.childPughInrScore(2.4),
  ],
  [1, 2, 2, 3]
);
assert.strictEqual(core.childPughBilirubinScore(null), null);
console.log('ok');
"""
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
