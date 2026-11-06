"""Rendered Telehealth retry actions use synthetic DOM and client responses."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js required")
def test_failed_sealed_submission_retries_from_rendered_control_and_discard_unlocks(tmp_path):
    source = (ROOT / "app/static/js/transcribe/telehealthUI.js").read_text(encoding="utf-8")
    source = source.replace(
        "./telehealthRecordingClient.js?v=20261106-stop-feedback", "./fake.mjs"
    ).replace(
        "./telehealthRecoveryJournal.js?v=20261105-cross-tab-journal", "./fake.mjs"
    ).replace(
        "./telehealthDiagnostics.js?v=20261106-server-events", "./diagnostics.mjs"
    )
    (tmp_path / "diagnostics.mjs").write_text(
        (ROOT / "app/static/js/transcribe/telehealthDiagnostics.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "ui.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "fake.mjs").write_text(r"""
export function createTelehealthRecoveryJournal() {
  return { check:async()=>{}, list:async()=>[], clear:async()=>{} };
}
export function createTelehealthUploadPacer() { return async()=>{}; }
export async function replayTelehealthRecoveryWindows() { return {}; }
export function createTelehealthRecordingClient(options) {
  const f=globalThis.__uiFixture;
  f.clientOptions=options;
  let state='idle', attempts=0;
  return {
    async start(){
      options.onDiagnostic({event:'picker_selected',surface:'window',windowTitle:'PATIENT SECRET'});
      state='recording'; options.onStateChange(state); return {status:'active'};
    },
    async stop(){
      f.stopAttempts=(f.stopAttempts||0)+1;
      state='stopping'; options.onStateChange(state);
      options.onStopProgress('flushing');
      f.startedStop();
      await f.stopCaptureGate;
      options.onStopProgress('uploading');
      await f.stopUploadGate;
      options.onStopProgress('sealing');
      await f.stopSealGate;
      state='sealed'; options.onStateChange(state); return {status:'ready'};
    },
    async transcribe(){
      attempts += 1; f.transcribeAttempts=attempts;
      if (attempts === 1) {
        f.startedTranscribe();
        await f.firstTranscribeGate;
        throw new Error('Synthetic lost /transcribe response');
      }
      state='transcribing'; options.onStateChange(state); return {status:'processing'};
    },
    async retryPendingUploads(){ f.retryUploadAttempts=(f.retryUploadAttempts||0)+1; return {status:'ready'}; },
    async acknowledgeIncomplete(){ f.partialAttempts=(f.partialAttempts||0)+1; },
    async discard(){
      f.startedDiscard(); await f.discardGate;
      state='discarded'; options.onStateChange(state); return {status:'discarded'};
    },
    get state(){return state;},
  };
}
""", encoding="utf-8")
    (tmp_path / "test.mjs").write_text(r"""
import assert from 'node:assert/strict';
import { attachTelehealthControls } from './ui.mjs';

class Element {
  constructor(name){this.name=name;this.hidden=false;this.disabled=false;this.textContent='';this.dataset={};this.style={};this.attrs={};this.listeners={};this.classes=new Set();
    this.classList={add:(...xs)=>xs.forEach(x=>this.classes.add(x)),remove:(...xs)=>xs.forEach(x=>this.classes.delete(x)),contains:x=>this.classes.has(x)};}
  addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
  removeEventListener(type,fn){this.listeners[type]=(this.listeners[type]||[]).filter(x=>x!==fn);}
  async click({force=false}={}){if(this.disabled&&!force)return;const event={preventDefault(){},stopImmediatePropagation(){}};for(const fn of this.listeners.click||[])await fn(event);}
  setAttribute(k,v){this.attrs[k]=v;}
  querySelector(selector){return this.children?.get(selector)||new Element(this.name+selector);}
  showModal(){this.open=true;}
  close(){this.open=false;for(const fn of this.listeners.close||[])fn();}
  focus(){}
}
const tick=()=>new Promise(r=>setTimeout(r,0));
async function waitFor(predicate){for(let i=0;i<30;i++){if(predicate())return;await tick();}throw new Error('synthetic UI action did not reach expected state');}
async function scenario(action){
  let releaseFirst, startedTranscribe, releaseDiscard, startedDiscard;
  let startedStop, releaseStopCapture, releaseStopUpload, releaseStopSeal;
  const stopStarted=new Promise(r=>startedStop=r);
  const stopCaptureGate=new Promise(r=>releaseStopCapture=r);
  const stopUploadGate=new Promise(r=>releaseStopUpload=r);
  const stopSealGate=new Promise(r=>releaseStopSeal=r);
  const firstTranscribeGate=new Promise(r=>releaseFirst=r);
  const started=new Promise(r=>startedTranscribe=r);
  const discardGate=new Promise(r=>releaseDiscard=r);
  const discarding=new Promise(r=>startedDiscard=r);
  const selectors=[
    '[data-telehealth-toggle]','[data-record-toggle]','[data-record-toggle-label]',
    '[data-record-toggle-icon]','[data-telehealth-receipt]','[data-telehealth-channels]',
    '[data-telehealth-combined]','[data-telehealth-combined-level]',
    '[data-telehealth-receipt-message]','[data-telehealth-actions]',
    '[data-telehealth-retry]','[data-telehealth-transcribe]','[data-telehealth-submit-retry]',
    '[data-telehealth-discard]','[data-telehealth-debug-panel]','[data-telehealth-debug-reference]',
    '[data-telehealth-debug-events]','[data-telehealth-guidance]',
    '[data-telehealth-guidance-cancel]','[data-telehealth-guidance-continue]',
    '[data-telehealth-guidance-error]',
  ];
  const els=new Map(selectors.map(s=>[s,new Element(s)]));
  const receipt=els.get('[data-telehealth-receipt]'); receipt.children=new Map();
  for(const channel of ['clinician','patient']){
    for(const suffix of ['source=','level=','speech-status=']){
      const selector=`[data-telehealth-${suffix}${channel}"]`;
      receipt.children.set(selector,new Element(selector));
    }
  }
  globalThis.document={querySelector:s=>els.get(s)||new Element(s)};
  if (!globalThis.crypto?.randomUUID) {
    globalThis.crypto = { randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef' };
  }
  globalThis.window={
    confirm:()=>true, addEventListener(){}, refreshLucideIcons(){},
    setTimeout:(fn)=>setTimeout(fn,0),clearTimeout,
  };
  const notices=[];
  const fixture={
    transcribeAttempts:0,retryUploadAttempts:0,partialAttempts:0,stopAttempts:0,
    diagnosticRequests:[],firstTranscribeGate,startedTranscribe:()=>startedTranscribe(),
    stopCaptureGate,stopUploadGate,stopSealGate,startedStop:()=>startedStop(),
    discardGate,startedDiscard:()=>startedDiscard()
  };
  globalThis.__uiFixture=fixture;
  const ui=attachTelehealthControls({
    getTranscriptId:()=> 'transcript-uuid', getIngestionMode:()=> 'whole_file',
    canUseRecording:()=>true, isOtherRecordingActive:()=>false,
    getPreferences:()=>({show_telehealth_control:true,telehealth_guidance_acknowledged:true}),
    persistPreferences:async()=>{},csrfFetch:async(_url, options={})=>{
      if (options.body && _url.includes('/telehealth/diagnostics')) {
        const payload=JSON.parse(options.body);fixture.diagnosticRequests.push(payload);
        return {ok:true,json:async()=>({accepted:payload.events.length})};
      }
      return {ok:true,json:async()=>null};
    },
    refreshWorkspace:async()=>{},showNotice:(text,kind)=>notices.push([text,kind]),
  });
  await els.get('[data-telehealth-toggle]').click();
  await waitFor(()=>els.get('[data-telehealth-guidance]').open===true);
  await els.get('[data-telehealth-guidance-continue]').click();
  await waitFor(()=>ui.isActive());
  await els.get('[data-record-toggle]').click();
  await waitFor(()=>ui.isRecording());
  // Louder audio alone must not be treated as speech.
  for(let i=0;i<20;i++) fixture.clientOptions.onLevel({
    channel:'patient', rms:0.9, startFrame:i*48000, endFrame:(i+1)*48000, sampleRate:48000,
  });
  assert.equal(receipt.classList.contains('is-verified'),false);
  fixture.clientOptions.onSpeechFrame({channel:'patient',speechMs:5000});
  assert.equal(receipt.classList.contains('is-verified'),false);
  fixture.clientOptions.onSpeechFrame({channel:'clinician',speechMs:5000});
  assert.equal(receipt.classList.contains('is-verified'),true);
  fixture.clientOptions.onDiagnostic({event:'capture_failed',code:'missing_audio'});
  await waitFor(()=>fixture.diagnosticRequests.length>=1);
  const report=fixture.diagnosticRequests[0];
  assert.match(report.diagnostic_id,/^[0-9a-f]{32}$/);
  assert.ok(report.events.some(e=>e.event==='picker_selected'&&e.surface==='window'));
  assert.ok(!JSON.stringify(report).includes('PATIENT SECRET'));
  assert.ok(els.get('[data-telehealth-debug-events]').textContent.includes('capture_failed'));
  assert.ok(!document.querySelector('[data-telehealth-debug-events]').textContent.includes('PATIENT SECRET'));
  await els.get('[data-record-toggle]').click();
  await stopStarted;
  const button=els.get('[data-record-toggle]');
  const label=els.get('[data-record-toggle-label]');
  assert.equal(label.textContent,'Stopping…','Stop click immediately acknowledges the stop request');
  assert.equal(button.disabled,true,'a second recording cannot start before the audio is sealed');
  assert.equal(els.get('[data-record-toggle-icon]').dataset.lucide,'loader-circle');
  assert.equal(ui.isUnresolved(),true);
  await button.click({force:true});
  assert.equal(fixture.stopAttempts,1,'duplicate Stop clicks cannot start a second flush');
  assert.equal(fixture.transcribeAttempts,0);

  releaseStopCapture();
  await waitFor(()=>label.textContent==='Saving audio…');
  assert.equal(fixture.transcribeAttempts,0,'transcription cannot start before audio upload');
  assert.ok(els.get('[data-telehealth-receipt-message]').textContent.includes('Recording stopped.'));
  releaseStopUpload();
  await waitFor(()=>label.textContent==='Finalising audio…');
  assert.equal(button.disabled,true);
  releaseStopSeal();
  await started;
  assert.equal(label.textContent,'Submitting…','the button reports submission instead of recording');
  const retry=els.get('[data-telehealth-submit-retry]');
  assert.equal(retry.hidden,false,'sealed capture exposes a separate transcription retry action');
  assert.equal(retry.disabled,true,'the initial Stop submission remains in flight');
  assert.equal(els.get('[data-record-toggle]').disabled,true,'duplicate recording actions are blocked while submitting');
  await retry.click({force:true});
  assert.equal(fixture.transcribeAttempts,1,'a duplicate click cannot submit a second request');
  releaseFirst();
  await waitFor(()=>retry.disabled===false);
  assert.equal(label.textContent,'Retry submission','failed submit returns to a truthful recovery label');
  assert.ok(notices.some(([text])=>text.includes('Synthetic lost /transcribe response')));
  assert.equal(retry.hidden,false,'failed submission keeps its retry action visible');
  if(action==='retry'){
    await retry.click();
    await waitFor(()=>fixture.transcribeAttempts===2);
    await waitFor(()=>retry.hidden===true);
    assert.equal(els.get('[data-record-toggle]').disabled,true,'processing continues to block a new recording');
  }else{
    await els.get('[data-telehealth-discard]').click();
    await discarding;
    assert.equal(els.get('[data-telehealth-retry]').disabled,true,'upload retry is blocked during discard');
    assert.equal(retry.disabled,true,'transcription retry is blocked during discard');
    await els.get('[data-telehealth-retry]').click({force:true});
    await els.get('[data-telehealth-transcribe]').click({force:true});
    await retry.click({force:true});
    assert.equal(fixture.transcribeAttempts,1,'forced retry clicks cannot race discard');
    assert.equal(fixture.retryUploadAttempts,0,'forced upload-retry click cannot race discard');
    assert.equal(fixture.partialAttempts,0,'forced partial-recovery click cannot race discard');
    releaseDiscard();
    await waitFor(()=>els.get('[data-record-toggle]').disabled===false
      && els.get('[data-telehealth-discard]').disabled===false);
    assert.equal(els.get('[data-telehealth-discard]').disabled,false);
  }
}
await scenario('retry');
await scenario('discard');
console.log('Telehealth rendered submission retry and discard controls: passed');
""", encoding="utf-8")
    subprocess.run(
        [NODE, str(tmp_path / "test.mjs")], cwd=tmp_path,
        check=True, capture_output=True, text=True, timeout=30,
    )
