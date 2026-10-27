import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the synthetic chart-control test")
def test_admin_usage_chart_controls_keep_native_and_chart_state_in_sync():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {}; this.checked = false; this.value = ''; }
  set id(value) { this._id = value; elements.set(value, this); }
  get id() { return this._id; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  closest() { return this.wrapper; }
  querySelector() { return null; }
}
const wrapper = new Element('figure');
const chartNode = new Element('div');
chartNode.dataset.usageChart = 'input';
chartNode.dataset.usageChartScope = 'team';
chartNode.wrapper = wrapper;
chartNode.parentElement = wrapper;
const data = new Element('script');
data.textContent = JSON.stringify([
  { label: '01 Jan 2026', short_label: '01 Jan', current_input: 20, previous_input: 10 },
  { label: '02 Jan 2026', short_label: '02 Jan', current_input: 30, previous_input: 15 },
]);
data.dataset = { rangeLabel: 'Last 30 days', hasComparison: 'true' };
const calls = [];
const handlers = {};
const chart = {
  setOption(option) { this.option = option; },
  dispatchAction(action) { calls.push(action); },
  on(name, callback) { handlers[name] = callback; },
  getOption() { return { dataZoom: [{}, { start: 0, end: 100 }] }; },
  resize() {},
};
const document = {
  getElementById(id) { return id === 'usage-chart-data' ? data : elements.get(id) || null; },
  querySelectorAll(selector) { return selector === '[data-usage-chart]' ? [chartNode] : []; },
  createElement(tag) { return new Element(tag); },
  activeElement: null,
};
const context = {
  document,
  window: { echarts: { init() { return chart; } }, ResizeObserver: class { observe() {} } },
  ResizeObserver: class { observe() {} },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const start = elements.get('usage-chart-start-team-input');
const end = elements.get('usage-chart-end-team-input');
const current = elements.get('usage-chart-series-team-input-current');
const previous = elements.get('usage-chart-series-team-input-previous');
assert.ok(start && end && current && previous);
assert.match(chart.option.aria.label.description, /Full values for every period are available in the data table/);
assert.equal(start.attributes['aria-valuetext'], 'First period shown: 01 Jan 2026');
start.value = '1';
document.activeElement = start;
start.listeners.input();
assert.ok(calls.some((action) => action.type === 'dataZoom' && action.startValue === 1));

handlers.datazoom({ startValue: 0, endValue: 0 });
assert.equal(start.value, '0');
assert.equal(end.value, '0');
handlers.datazoom({ start: 0, end: 0 });
assert.equal(end.value, '0');
previous.checked = false;
previous.listeners.change();
assert.ok(calls.some((action) => action.type === 'legendUnSelect' && action.name === 'Previous equal period'));
handlers.legendselectchanged({ selected: { 'Previous equal period': true } });
assert.equal(previous.checked, true);
assert.match(chartNode.attributes['aria-label'], /Full values for every period are available in the data table/);
"""
    chart_path = Path("app/static/js/admin-usage-charts.js").resolve()
    result = subprocess.run(
        [shutil.which("node"), "-e", script, str(chart_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
