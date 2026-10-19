"""Node behavior checks for the real Scribe sidebar width controls."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for the frontend behavior harness")


HARNESS = r'''
import fs from 'node:fs';
import assert from 'node:assert/strict';
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf("  var primarySidebar = document.querySelector('[data-primary-sidebar]');");
const end = source.indexOf('  function syncSessionPanelHeaderHeight()', start);
assert.ok(start >= 0 && end > start, 'sidebar resize controller boundaries must exist');
const listeners = (target) => ({
  addEventListener(type, callback) { (target.events[type] ??= []).push(callback); },
  dispatch(type, event = {}) { for (const fn of target.events[type] || []) fn({type, target, preventDefault(){}, ...event}); },
});
class Element {
  constructor(width=0) {
    this.events={}; this.attrs={}; this.style={width: width ? `${width}px` : '', setProperty(){}, removeProperty(){}};
    this.classList={set:new Set(), add:(x)=>this.classList.set.add(x), remove:(x)=>this.classList.set.delete(x), toggle:(x,v)=>v?this.classList.set.add(x):this.classList.set.delete(x)};
    this.listeners=listeners(this);
  }
  addEventListener(...args) { this.listeners.addEventListener(...args); }
  dispatchEvent(type,event) { this.listeners.dispatch(type,event); }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  getAttribute(key) { return this.attrs[key] ?? null; }
  getBoundingClientRect() { return {width: Number.parseFloat(this.style.width)||0}; }
  setPointerCapture() {}
  releasePointerCapture() {}
}
const aside = new Element(288);
const divider = new Element();
const collapse = [new Element()];
const narrower = new Element(); narrower.attrs['data-sidebar-step']='-16';
const wider = new Element(); wider.attrs['data-sidebar-step']='16';
const storage = new Map();
const media = {matches:true, addEventListener(){}};
const window = {matchMedia(){return media},localStorage:{getItem(k){return storage.get(k)||null},setItem(k,v){storage.set(k,v)}}};
const document = {body:{style:{}},querySelector(s){return s==='[data-primary-sidebar]'?aside:s==='[data-sidebar-resize]'?divider:null},querySelectorAll(s){return s==='[data-sidebar-collapse-toggle]'?collapse:s==='[data-sidebar-step]'?[narrower,wider]:[]}};
new Function('document','window',source.slice(start,end))(document,window);
assert.equal(divider.getAttribute('aria-valuenow'),'288');
divider.dispatchEvent('keydown',{key:'ArrowRight'});
assert.equal(divider.getAttribute('aria-valuenow'),'304');
divider.dispatchEvent('keydown',{key:'Home'});
assert.equal(divider.getAttribute('aria-valuenow'),'64');
divider.dispatchEvent('keydown',{key:'ArrowRight'});
assert.equal(divider.getAttribute('aria-valuenow'),'192');
narrower.dispatchEvent('click');
assert.equal(divider.getAttribute('aria-valuenow'),'192','width must stay above the expanded minimum');
collapse[0].dispatchEvent('click');
assert.equal(divider.getAttribute('aria-valuenow'),'64','collapse toggle must collapse the sidebar');
collapse[0].dispatchEvent('click');
assert.equal(divider.getAttribute('aria-valuenow'),'288','collapse toggle must restore the expanded width');
divider.dispatchEvent('pointerdown',{pointerId:3,clientX:100});
divider.dispatchEvent('pointermove',{pointerId:3,clientX:120});
divider.dispatchEvent('pointerup',{pointerId:3,clientX:120});
assert.equal(divider.getAttribute('aria-valuenow'),'308','single-pointer drag must resize the sidebar');
assert.equal(storage.get('openscribe:transcribe:sidebar-width'),'308');
divider.dispatchEvent('keydown',{key:'End'});
wider.dispatchEvent('click');
assert.equal(divider.getAttribute('aria-valuenow'),'384','click controls must stay within the maximum');
divider.dispatchEvent('pointerdown',{pointerId:4,clientX:100});
divider.dispatchEvent('pointermove',{pointerId:4,clientX:120});
divider.dispatchEvent('pointerup',{pointerId:4,clientX:120});
assert.equal(divider.getAttribute('aria-valuenow'),'384','pointer drag must stay within the maximum');
assert.equal(storage.get('openscribe:transcribe:sidebar-width'),'384');
'''


def test_scribe_sidebar_width_controls_obey_keyboard_pointer_bounds_and_persistence() -> None:
    extras = ROOT / "app/templates/transcribe/_shell_extras.html"
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", HARNESS, str(extras)],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
