"""Rendered Telehealth capture and retry actions use synthetic DOM and responses."""
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
        "./telehealthRecordingClient.js?v=20261107-telehealth-recovery-retry", "./fake.mjs"
    ).replace(
        "./telehealthCapture.js?v=20261108-system-audio", "./capture.mjs"
    ).replace(
        "./telehealthRecoveryJournal.js?v=20261105-cross-tab-journal", "./fake.mjs"
    ).replace(
        "./telehealthDiagnostics.js?v=20261106-server-events", "./diagnostics.mjs"
    )
    (tmp_path / "diagnostics.mjs").write_text(
        (ROOT / "app/static/js/transcribe/telehealthDiagnostics.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "capture.mjs").write_text(r"""
export function prepareTelehealthDisplaySource(options = {}) {
  const f = globalThis.__uiFixture;
  f.requestedAudioScopes.push(options.audioScope);
  f.pickerCalls += 1;
  f.events.push('picker');
  const source = {
    surface: options.audioScope === 'system' ? 'window' : 'browser',
    audioScope: options.audioScope,
    live: true,
    releases: 0,
    isLive() { return this.live; },
    release() { this.live = false; this.releases += 1; f.sourceReleases += 1; },
  };
  f.preparedSources.push(source);
  return f.selectionGate ? f.selectionGate.then(() => source) : Promise.resolve(source);
}
""", encoding="utf-8")
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
      f.startedWithSource = options.preparedSource;
      f.events.push('client-start');
      f.clientStarts += 1;
      options.onDiagnostic({event:'picker_selected',surface:'window',windowTitle:'PATIENT SECRET'});
      if (f.pendingStart) {
        state='starting'; options.onStateChange(state);
        f.startedStart();
        await f.startGate;
        state='failed'; options.onStateChange(state);
        throw new Error('Synthetic start failure after consultation switch');
      }
      if (f.lostCreateResponse) {
        state='failed';
        throw new Error('Synthetic lost capture-create response');
      }
      state='recording'; options.onStateChange(state); return {status:'active'};
    },
    get mayHaveCreatedSession(){return Boolean(f.lostCreateResponse || f.pendingStartMayHaveCreated);},
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
      f.startedTranscribe();
      if (f.action==='own-failed') { state='transcribing'; options.onStateChange(state); return {status:'processing'}; }
      if (attempts === 1) {
        await f.firstTranscribeGate;
        throw new Error('Synthetic lost /transcribe response');
      }
      state='transcribing'; options.onStateChange(state); return {status:'processing'};
    },
    async getStatus(){ return f.ownStatus; },
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
  constructor(name){this.name=name;this.hidden=false;this.disabled=false;this.textContent='';this.dataset={};this.style={};this.attrs={};this.listeners={};this.classes=new Set();this.childNodes=[];
    this.classList={add:(...xs)=>xs.forEach(x=>this.classes.add(x)),remove:(...xs)=>xs.forEach(x=>this.classes.delete(x)),contains:x=>this.classes.has(x)};}
  addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
  removeEventListener(type,fn){this.listeners[type]=(this.listeners[type]||[]).filter(x=>x!==fn);}
  async click({force=false}={}){if(this.disabled&&!force)return;const event={prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};for(const fn of this.listeners.click||[]){await fn(event);if(event.stopped)break;}return event;}
  setAttribute(k,v){this.attrs[k]=v;}
  querySelector(selector){return this.children instanceof Map?(this.children.get(selector)||null):new Element(this.name+selector);}
  append(...nodes){this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=[...nodes];}
  showModal(){this.open=true;}
  close(){this.open=false;for(const fn of this.listeners.close||[])fn();}
  focus(){}
}
const tick=()=>new Promise(r=>setTimeout(r,0));
async function waitFor(predicate){for(let i=0;i<30;i++){if(predicate())return;await tick();}throw new Error('synthetic UI action did not reach expected state');}
async function scenario(action){
  let resolveSelection, resolveAcknowledgement, resolvePartialAction;
  const selectionGate=new Promise(r=>resolveSelection=r);
  const acknowledgementGate=new Promise(r=>resolveAcknowledgement=r);
  const partialActionGate=new Promise(r=>resolvePartialAction=r);
  let rejectStart, startedStart;
  const startGate=new Promise((_,reject)=>rejectStart=reject);
  const startAttempted=new Promise(r=>startedStart=r);
  let resolveInitialUnresolved;
  const initialUnresolvedGate=new Promise(r=>resolveInitialUnresolved=r);
  let transcriptId='transcript-uuid';
  const windowListeners={};
  let releaseModeSwitch, modeSwitchStarted;
  const modeSwitchGate=new Promise(r=>releaseModeSwitch=r);
  const modeSwitchRequested=new Promise(r=>modeSwitchStarted=r);
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
  const confirmations=[];
  let confirmationResult=true;
  const selectors=[
    '[data-telehealth-toggle]','[data-record-toggle]','[data-record-toggle-label]',
    '[data-record-toggle-icon]','[data-telehealth-receipt]','[data-telehealth-channels]',
    '[data-telehealth-receipt-message]','[data-telehealth-actions]',
    '[data-telehealth-retry]','[data-telehealth-transcribe]','[data-telehealth-submit-retry]',
    '[data-telehealth-partial-summary]','[data-telehealth-gap-summary]','[data-telehealth-gap-list]','[data-telehealth-use-available]','[data-telehealth-load-more-partials]',
    '[data-telehealth-discard]','[data-telehealth-debug-panel]','[data-telehealth-debug-reference]',
    '[data-telehealth-debug-events]','[data-telehealth-guidance]',
    '[data-telehealth-guidance-cancel]','[data-telehealth-guidance-continue]',
    '[data-telehealth-guidance-error]',
    '[data-telehealth-audio-scope-call]','[data-telehealth-audio-scope-system]',
    '[data-telehealth-system-audio-warning]',
    '[data-recording-mode-select]','[data-record-mode-dropdown]',
    '[data-record-mode-menu]','[data-mode=whole_file]','[data-mode=live_chunked]',
    '[data-audio-file-input]','[data-audio-action-trigger]','[data-upload-form]',
  ];
  const els=new Map(selectors.map(s=>[s,new Element(s)]));
  els.get('[data-recording-mode-select]').dataset.modeChangeInFlight='false';
  els.get('[data-record-mode-menu]').hidden=false;
  const modeOptions=[els.get('[data-mode=whole_file]'),els.get('[data-mode=live_chunked]')];
  const receipt=els.get('[data-telehealth-receipt]'); receipt.children=new Map();
  for(const suffix of ['source=','level=','speech-status=']){
    const selector=`[data-telehealth-${suffix}"patient"]`;
    receipt.children.set(selector,new Element(selector));
  }
  assert.equal(receipt.querySelector('[data-telehealth-source="clinician"]'),null,
    'the Telehealth receipt contains no invented clinician row');
  globalThis.document={
    querySelector:s=>els.get(s)||new Element(s),
    querySelectorAll:s=>s==='[data-record-mode-menu] [data-mode]'?modeOptions:[],
    createElement:tag=>new Element(tag),
    createTextNode:text=>({textContent:String(text)}),
  };
  if (!globalThis.crypto?.randomUUID) {
    globalThis.crypto = { randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef' };
  }
  globalThis.window={
    confirm:(text)=>{confirmations.push(text);return confirmationResult;}, addEventListener(type,fn){(windowListeners[type] ||= []).push(fn);}, refreshLucideIcons(){},
    setTimeout:(fn)=>setTimeout(fn,0),clearTimeout,
  };
  const notices=[];
  const fixture={
    transcribeAttempts:0,retryUploadAttempts:0,partialAttempts:0,stopAttempts:0,
    pickerCalls:0,clientStarts:0,sourceReleases:0,preparedSources:[],events:[],
    requestedAudioScopes:[],
    diagnosticRequests:[],firstTranscribeGate,startedTranscribe:()=>startedTranscribe(),
    stopCaptureGate,stopUploadGate,stopSealGate,startedStop:()=>startedStop(),
    discardGate,startedDiscard:()=>startedDiscard()
    ,modeSwitchGate,modeSwitchRequested:()=>modeSwitchStarted(),micLevelCalls:[],micResetCalls:0
  };
  fixture.action=action;
  fixture.lostCreateResponse=action==='lost-create';
  fixture.pendingStart=action.startsWith('late-start-root-switch');
  fixture.pendingStartMayHaveCreated=action==='late-start-root-switch';
  fixture.useAvailableRequests=[];
  fixture.partialRetryRequests=[];
  fixture.partialListRequests=[];
  fixture.refreshCalls=0;
  fixture.scheduledRefreshes=0;
  fixture.confirmations=confirmations;
  if(action==='own-failed') fixture.ownStatus={
    transcript_id:'transcript-uuid',capture_session_id:'live-failed-session',status:'failed',
    can_use_available_transcript:true,timeline_sample_rate:48000,failure_code:'transcription_failed',
    parts:[{channel:'patient',sequence_no:0,status:'failed',timeline_start_frame:0,timeline_end_frame:480000,
      ingestion_job_id:'live-failed-job',retry_available:true}],
  };
  if(action.startsWith('partial')) fixture.currentCapture={
    transcript_id:'transcript-uuid',capture_session_id:'partial-session',
    status:['partial-retry','partial-late-response','partial-pages','partial-deferred'].includes(action)?'partial':'failed',
    timeline_sample_rate:48000,failure_code:action==='partial-silent'?'no_transcribed_speech':'transcription_failed',
    can_use_available_transcript:!['partial-retry','partial-late-response','partial-pages','partial-deferred'].includes(action),
    parts:action==='partial-silent'
      ? [{channel:'patient',sequence_no:0,status:'transcribed',timeline_start_frame:0,
        timeline_end_frame:480000,ingestion_job_id:'silent-job',retry_available:false,included_in_transcript:true}]
      : [{channel:'patient',sequence_no:1,status:'failed',timeline_start_frame:480000,
        timeline_end_frame:720000,ingestion_job_id:'failed-job-1',retry_available:true}],
  };
  if(action==='partial-deferred') fixture.currentCapture.parts=[{
    channel:'patient',sequence_no:0,status:'transcribed',timeline_start_frame:0,timeline_end_frame:480000,
    ingestion_job_id:'deferred-job',retry_available:false,included_in_transcript:false,
  }];
  fixture.startGate=startGate;
  fixture.startedStart=()=>startedStart();
  fixture.unresolvedChecks=0;
  fixture.unresolvedRequests=[];
  fixture.ingestionMode='live_chunked';
  if(action==='late-picker') fixture.selectionGate=selectionGate;
  globalThis.__uiFixture=fixture;
  let ui;
  ui=attachTelehealthControls({
    getTranscriptId:()=> transcriptId, getIngestionMode:()=> fixture.ingestionMode,
    canUseRecording:()=>true, isOtherRecordingActive:()=>false,
    getPreferences:()=>({show_telehealth_control:true,telehealth_guidance_acknowledged:action!=='late-ack'}),
    persistPreferences:async(patch)=>{if(patch.telehealth_guidance_acknowledged) await acknowledgementGate;},csrfFetch:async(_url, options={})=>{
      if (_url.endsWith('/telehealth/unresolved')) {
        fixture.unresolvedChecks+=1;
        fixture.unresolvedRequests.push(_url);
        if (fixture.lostCreateResponse && fixture.unresolvedChecks===1) await initialUnresolvedGate;
        if (action==='partial-late-response' && fixture.unresolvedChecks===1) await initialUnresolvedGate;
        const switchedRoot = fixture.pendingStart && _url.includes('another-synthetic-transcript');
        return {ok:true,json:async()=>switchedRoot || (fixture.lostCreateResponse && fixture.unresolvedChecks>1) ? {
          transcript_id:switchedRoot ? 'another-synthetic-transcript' : 'transcript-uuid',
          capture_session_id:'server-committed-session',status:'active',parts:[],
        } : fixture.currentCapture?.transcript_id === transcriptId ? fixture.currentCapture : null};
      }
      if (_url.includes('/telehealth/partial-captures?')) {
        const offset=Number(new URL('http://local'+_url).searchParams.get('offset')||0);
        fixture.partialListRequests.push(offset);
        if(action==='partial-pages' && offset===20) return {ok:true,json:async()=>({items:[{
          transcript_id:'transcript-uuid',capture_session_id:'older-partial',status:'partial',timeline_sample_rate:48000,
          parts:[{channel:'patient',sequence_no:0,status:'failed',timeline_start_frame:0,timeline_end_frame:480000,
            ingestion_job_id:'older-failed-job',retry_available:true}],
        }],has_more:false,next_offset:null})};
        const items=fixture.currentCapture?.status==='partial'?[fixture.currentCapture]:[];
        return {ok:true,json:async()=>({items,has_more:action==='partial-pages',next_offset:action==='partial-pages'?20:null})};
      }
      if (_url.includes('/telehealth/captures/') && _url.endsWith('/use-available')) {
        fixture.useAvailableRequests.push({url:_url,options});
        if(action==='partial-stale-action') await partialActionGate;
        fixture.currentCapture={...(fixture.currentCapture||fixture.ownStatus),status:'partial',can_use_available_transcript:false};
        return {ok:true,json:async()=>fixture.currentCapture};
      }
      if (_url.includes('/ingestion-jobs/') && _url.endsWith('/retry-audio')) {
        fixture.partialRetryRequests.push({url:_url,options});
        fixture.currentCapture={...fixture.currentCapture,parts:fixture.currentCapture.parts.map(part=>({...part,status:'processing',retry_available:false}))};
        return {ok:true,json:async()=>({})};
      }
      if (options.body && _url.includes('/telehealth/diagnostics')) {
        const payload=JSON.parse(options.body);fixture.diagnosticRequests.push(payload);
        return {ok:true,json:async()=>({accepted:payload.events.length})};
      }
      if (_url === '/api/v1/transcripts/transcript-uuid' && options.method === 'PATCH') {
        modeSwitchStarted();
        await modeSwitchGate;
        fixture.ingestionMode='whole_file';
      }
      return {ok:true,json:async()=>null};
    },
    refreshWorkspace:async()=>{fixture.refreshCalls+=1;if(action.startsWith('partial'))ui?.sync();},
    scheduleWorkspaceRefreshBurst:()=>{fixture.scheduledRefreshes+=1;},
    showNotice:(text,kind)=>notices.push([text,kind]),
    setClinicianMicLevel:(rms, speech)=>fixture.micLevelCalls.push([rms,speech]),
    resetClinicianMicLevel:()=>{fixture.micResetCalls+=1;},
  });
  ui.setOrdinaryControlsSync(()=>{
    if(ui.isActive()) return;
    els.get('[data-recording-mode-select]').disabled=false;
    els.get('[data-record-mode-dropdown]').disabled=false;
    for(const option of modeOptions) option.disabled=false;
    els.get('[data-audio-file-input]').disabled=false;
    els.get('[data-audio-action-trigger]').disabled=false;
  });
  fixture.ordinaryRecordStarts=0;
  els.get('[data-record-toggle]').addEventListener('click',()=>{fixture.ordinaryRecordStarts+=1;});
  if(action==='partial-late-response') {
    await waitFor(()=>fixture.unresolvedChecks===1);
    transcriptId='another-synthetic-transcript';
    ui.sync();
    resolveInitialUnresolved();
    await waitFor(()=>fixture.unresolvedRequests.some(url=>url.includes('another-synthetic-transcript')));
    await waitFor(()=>!ui.isUnresolved() && els.get('[data-telehealth-partial-summary]').hidden);
    assert.equal(els.get('[data-telehealth-receipt]').hidden,true,
      'a delayed old-root partial projection cannot appear in the newly selected consultation');
    return;
  }
  if(action.startsWith('partial')) {
    await waitFor(()=>!els.get('[data-telehealth-partial-summary]').hidden);
    if(action==='partial-silent') {
      assert.equal(els.get('[data-telehealth-gap-list]').childNodes.length,0,
        'a fully transcribed silent source is not falsely described as a missing interval');
      assert.match(els.get('[data-telehealth-gap-summary]').textContent,/No speech was found/);
    } else if(action==='partial-deferred') {
      const deferredText=els.get('[data-telehealth-gap-list]').childNodes[0].childNodes.map(node=>node.textContent||'').join('');
      assert.match(deferredText,/transcribed; waiting to be added to transcript/,
        'completed source stays visible while durable application is deferred');
      assert.equal(els.get('[data-telehealth-gap-list]').childNodes[0].childNodes.some(node=>node.name==='button'),false,
        'a completed deferred part does not expose audio retry');
      assert.equal(ui.isUnresolved(),false,'accepted partial remains nonblocking while auto-apply waits');
      fixture.currentCapture.parts[0].included_in_transcript=true;
      ui.sync();
      await waitFor(()=>els.get('[data-telehealth-gap-list]').childNodes.length===0);
      assert.equal(els.get('[data-telehealth-gap-summary]').textContent,
        'Available source results have been accepted. Check the transcript for completeness before using it clinically.');
      return;
    } else {
      const gapText=els.get('[data-telehealth-gap-list]').childNodes[0].childNodes
        .map(node=>node.textContent||'').join('');
      assert.match(gapText,/Patient audio · window 2 · 00:10–00:15 · failed/,
        'the receipt identifies the missing source and its consultation interval');
    }
    const actionButton=els.get('[data-telehealth-use-available]');
    if(action==='partial-retry'||action==='partial-late-response'||action==='partial-pages') {
      assert.equal(actionButton.hidden,true,'an already accepted partial capture has no second acceptance action');
      assert.equal(ui.isUnresolved(),false,'an accepted partial capture does not keep the root unresolved');
      assert.equal(ui.isSelectedOrUnresolved(),false,'partial recovery does not retain the Telehealth selection lock');
      assert.equal(els.get('[data-recording-mode-select]').disabled,false,'ordinary mode changes remain available');
      assert.equal(els.get('[data-audio-file-input]').disabled,false,'audio input remains available');
    } else {
      assert.equal(actionButton.hidden,false);
      assert.equal(ui.isUnresolved(),true,'a failed capture remains a barrier until the owner accepts available results');
      assert.equal(ui.isSelectedOrUnresolved(),true);
      assert.equal(els.get('[data-recording-mode-select]').disabled,true);
    }
    if(action==='partial-cancel') {
      confirmationResult=false;
      await actionButton.click();
      assert.equal(fixture.useAvailableRequests.length,0,'cancel leaves available transcript unpublished');
      assert.equal(els.get('[data-telehealth-partial-summary]').hidden,false);
      return;
    }
    if(action==='partial-silent') {
      assert.equal(actionButton.textContent,'Resolve without transcript',
        'all-silent captures are not presented as a transcript to use');
    }
    if(action==='partial-stale-action') {
      await actionButton.click();
      await waitFor(()=>fixture.useAvailableRequests.length===1);
      transcriptId='another-synthetic-transcript';
      ui.sync();
      resolvePartialAction();
      await waitFor(()=>fixture.unresolvedRequests.some(url=>url.includes('another-synthetic-transcript')));
      await waitFor(()=>els.get('[data-telehealth-partial-summary]').hidden);
      assert.equal(els.get('[data-telehealth-receipt]').hidden,true,
        'a late acceptance response cannot show stale intervals on another consultation');
      return;
    }
    if(action==='partial-retry') {
      const retryButton=els.get('[data-telehealth-gap-list]').childNodes[0].childNodes.find(node=>node.name==='button');
      assert.ok(retryButton,'a retained failed source has an interval-specific retry action');
      await retryButton.click();
      await waitFor(()=>fixture.partialRetryRequests.length===1);
      assert.match(fixture.partialRetryRequests[0].url,/transcripts\/transcript-uuid\/ingestion-jobs\/failed-job-1\/retry-audio$/);
      assert.equal(fixture.partialRetryRequests[0].options.method,'POST');
      assert.equal(fixture.partialRetryRequests[0].options.body,undefined,'retry sends no clinical content');
      await waitFor(()=>fixture.currentCapture.parts[0].status==='processing');
      assert.ok(fixture.scheduledRefreshes>0,'partial state is polled for later recovered speech');
      fixture.currentCapture=null;
      ui.sync();
      await waitFor(()=>els.get('[data-telehealth-partial-summary]').hidden);
      assert.equal(ui.isUnresolved(),false,'a later completed capture clears the old missing-interval notice');
      return;
    }
    if(action==='partial-pages') {
      const loadMore=els.get('[data-telehealth-load-more-partials]');
      await waitFor(()=>loadMore.hidden===false);
      await loadMore.click();
      await waitFor(()=>els.get('[data-telehealth-gap-list]').childNodes.some(node=>node.dataset.captureSessionId==='older-partial'));
      let olderGap=els.get('[data-telehealth-gap-list]').childNodes.find(node=>node.dataset.captureSessionId==='older-partial');
      assert.ok(olderGap,'older accepted partial capture remains listed after page fetch');
      const previousPageCalls=fixture.partialListRequests.filter(offset=>offset===20).length;
      ui.sync();
      await waitFor(()=>fixture.partialListRequests.filter(offset=>offset===20).length>previousPageCalls);
      await waitFor(()=>els.get('[data-telehealth-gap-list]').childNodes.some(node=>node.dataset.captureSessionId==='older-partial'));
      assert.equal(ui.isUnresolved(),false,'partial polling keeps accepted captures nonblocking');
      olderGap=els.get('[data-telehealth-gap-list]').childNodes.find(node=>node.dataset.captureSessionId==='older-partial');
      const olderRetry=olderGap.childNodes.find(node=>node.name==='button');
      await olderRetry.click();
      await waitFor(()=>fixture.partialRetryRequests.some(req=>req.url.includes('older-failed-job')));
      assert.ok(fixture.partialRetryRequests.at(-1).url.includes('/older-failed-job/retry-audio'),
        'retry remains reachable for older partial capture pages');
      return;
    }
    confirmationResult=true;
    await actionButton.click();
    await waitFor(()=>fixture.useAvailableRequests.length===1);
    assert.equal(fixture.useAvailableRequests[0].options.method,'POST');
    assert.equal(fixture.useAvailableRequests[0].options.body,undefined,'acceptance sends no clinical content');
    await waitFor(()=>fixture.refreshCalls>0);
    assert.equal(ui.isUnresolved(),false,'acceptance releases the editing and new-input barrier');
    assert.equal(ui.isSelectedOrUnresolved(),false);
    assert.equal(els.get('[data-record-toggle]').disabled,false);
    assert.equal(els.get('[data-recording-mode-select]').disabled,false);
    assert.equal(els.get('[data-audio-file-input]').disabled,false);
    if(action==='partial-silent') {
      assert.ok(confirmations.at(-1).includes('without adding transcript text'));
      assert.ok(confirmations.at(-1).includes('prior transcript content and notes are unchanged'));
    } else {
      assert.ok(confirmations.at(-1).includes('Patient window 2 (00:10–00:15)'),
        'acceptance confirmation names the exact interval gap');
      assert.ok(confirmations.at(-1).includes('later recovered speech is added as a new transcript version'));
    }
    fixture.currentCapture=null;
    ui.sync();
    await waitFor(()=>els.get('[data-telehealth-partial-summary]').hidden);
    return;
  }
  await els.get('[data-telehealth-toggle]').click();
  if(action==='lost-create') await waitFor(()=>fixture.unresolvedChecks===1);
  await modeSwitchRequested;
  assert.equal(els.get('[data-record-toggle]').disabled,true,
    'ordinary recording is disabled while Telehealth waits for recorded-upload mode');
  await els.get('[data-record-toggle]').click({force:true});
  assert.equal(fixture.ordinaryRecordStarts,0,
    'the capture-phase Telehealth guard also consumes a forced main-record click during activation');
  assert.equal(fixture.clientStarts,0,'activation cannot start either recorder while the mode change is pending');
  releaseModeSwitch();
  await waitFor(()=>els.get('[data-telehealth-guidance]').open===true);
  if(action==='cancel') {
    await els.get('[data-telehealth-guidance-cancel]').click();
    await waitFor(()=>!ui.isSelectedOrUnresolved());
    assert.equal(fixture.pickerCalls,0);
    assert.equal(fixture.clientStarts,0);
    assert.equal(els.get('[data-record-toggle]').disabled,false);
    return;
  }
  if (action === 'system-audio') {
    els.get('[data-telehealth-audio-scope-call]').checked = false;
    els.get('[data-telehealth-audio-scope-system]').checked = true;
    for (const fn of els.get('[data-telehealth-audio-scope-system]').listeners.change || []) fn();
    assert.equal(els.get('[data-telehealth-system-audio-warning]').hidden, false);
  }
  await els.get('[data-telehealth-guidance-continue]').click();
  assert.equal(fixture.requestedAudioScopes[0], action === 'system-audio' ? 'system' : 'call');
  if(['late-picker','late-ack','switch-root','pagehide'].includes(action)) {
    await waitFor(()=>fixture.pickerCalls===1);
    if(action==='switch-root') { transcriptId='another-synthetic-transcript'; ui.sync(); }
    else if(action==='pagehide') { for(const fn of windowListeners.pagehide||[]) fn(); }
    else await els.get('[data-telehealth-guidance-cancel]').click();
    resolveSelection();
    resolveAcknowledgement();
    await waitFor(()=>fixture.sourceReleases>0);
    await waitFor(()=>!ui.isSelectedOrUnresolved());
    assert.equal(ui.isActive(),false);
    assert.equal(fixture.clientStarts,0,'cancelled or stale preparation never starts recording');
    assert.equal(fixture.preparedSources[0].live,false,'late selected source is released');
    assert.equal(els.get('[data-record-toggle]').disabled,false);
    return;
  }
  await waitFor(()=>ui.isActive());
  if (action === 'system-audio') {
    assert.match(els.get('[data-telehealth-receipt-message]').textContent, /unrelated applications or speech/);
    assert.equal(receipt.children.get('[data-telehealth-speech-status="patient"]').textContent, 'System audio connected');
  } else {
    assert.equal(els.get('[data-telehealth-receipt-message]').hidden,true,'normal preparation needs no extra paragraph');
  }
  assert.deepEqual(fixture.events,['picker'],'selection preparation precedes recording start');
  assert.equal(fixture.clientStarts,0,'enabling only prepares a source; it does not start capture');
  assert.equal(els.get('[data-recording-mode-select]').disabled,true);
  assert.equal(els.get('[data-record-mode-dropdown]').disabled,true);
  assert.ok(modeOptions.every((option)=>option.disabled));
  assert.equal(els.get('[data-audio-file-input]').disabled,true);
  assert.equal(els.get('[data-audio-action-trigger]').disabled,true);
  const attemptedFileSelection={prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};
  els.get('[data-audio-file-input]').value='synthetic.wav';
  for(const fn of els.get('[data-audio-file-input]').listeners.change||[]) fn(attemptedFileSelection);
  assert.equal(attemptedFileSelection.prevented,true,'late file-picker changes are fenced by the UI capture handler');
  assert.equal(attemptedFileSelection.stopped,true);
  assert.equal(els.get('[data-audio-file-input]').value,'');
  await els.get('[data-record-toggle]').click();
  if(fixture.pendingStart) {
    await startAttempted;
    transcriptId='another-synthetic-transcript';
    ui.sync();
    assert.equal(fixture.unresolvedRequests.some(url=>url.includes('another-synthetic-transcript')),false,
      'root reconciliation waits until the old client start settles');
    rejectStart(new Error('Synthetic committed start response lost'));
    await waitFor(()=>fixture.unresolvedRequests.some(url=>url.includes('another-synthetic-transcript')));
    await waitFor(()=>ui.isUnresolved());
    assert.equal(els.get('[data-telehealth-transcribe]').hidden,false,
      'the current consultation renders its own owner-unresolved capture');
    assert.equal(els.get('[data-record-toggle]').disabled,true,
      'the current consultation stays blocked until its capture is resolved');
    assert.equal(fixture.preparedSources[0].releases,1,
      'the failed old start releases its own selected source exactly once');
    return;
  }
  if(action==='lost-create') {
    await waitFor(()=>notices.some(([text])=>text.includes('Synthetic lost capture-create response')));
    resolveInitialUnresolved();
    await waitFor(()=>ui.isUnresolved());
    assert.equal(fixture.unresolvedChecks,2,'ambiguous create failure reruns an earlier in-flight negative check');
    assert.equal(els.get('[data-telehealth-transcribe]').hidden,false,
      'the committed owner-unresolved capture is rendered with recovery actions');
    assert.equal(els.get('[data-record-toggle]').disabled,true,
      'the consultation stays blocked until the committed capture is resolved');
    return;
  }
  await waitFor(()=>ui.isRecording());
  assert.deepEqual(fixture.events,['picker','client-start']);
  assert.equal(fixture.startedWithSource,fixture.preparedSources[0], 'Start consumes the source prepared by Continue');
  assert.equal(fixture.clientStarts,1);
  fixture.clientOptions.onLevel({channel:'clinician',rms:0.31});
  assert.deepEqual(fixture.micLevelCalls.at(-1),[0.31,false],
    'clinician RMS reuses the consultation microphone visualizer while remaining speech-unconfirmed');
  // Louder audio alone must not be treated as speech.
  for(let i=0;i<20;i++) fixture.clientOptions.onLevel({
    channel:'patient', rms:0.9, startFrame:i*48000, endFrame:(i+1)*48000, sampleRate:48000,
  });
  assert.equal(receipt.classList.contains('is-verified'),false);
  fixture.clientOptions.onSpeechFrame({channel:'patient',speechMs:2999});
  fixture.clientOptions.onSpeechFrame({channel:'clinician',speechMs:2999});
  assert.equal(receipt.classList.contains('is-verified'),false);
  assert.notEqual(receipt.children.get('[data-telehealth-speech-status="patient"]').textContent,'Speech detected');
  fixture.clientOptions.onSpeechFrame({channel:'patient',speechMs:1});
  assert.equal(receipt.children.get('[data-telehealth-speech-status="patient"]').textContent,'Speech detected');
  assert.equal(receipt.classList.contains('is-verified'),true,'patient speech confirms the patient-only receipt independently');
  fixture.clientOptions.onSpeechFrame({channel:'clinician',speechMs:1});
  assert.deepEqual(fixture.micLevelCalls.at(-1),[0.31,true],
    'the shared clinician meter receives the independent VAD confirmation state');
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
  assert.ok(fixture.micResetCalls>0,'the shared clinician meter resets as soon as stopping begins');
  const meterUpdatesAtStop=fixture.micLevelCalls.length;
  fixture.clientOptions.onLevel({channel:'clinician',rms:0.8});
  fixture.clientOptions.onSpeechFrame({channel:'clinician',speechMs:3000});
  assert.equal(fixture.micLevelCalls.length,meterUpdatesAtStop,'late frames cannot reactivate the stopped clinician meter');
  const button=els.get('[data-record-toggle]');
  const label=els.get('[data-record-toggle-label]');
  assert.equal(label.textContent,'Stopping…','Stop click immediately acknowledges the stop request');
  assert.equal(button.disabled,true,'a second recording cannot start before the audio is sealed');
  assert.equal(button.attrs['aria-busy'],'true','assistive technology sees that Stop is still saving');
  assert.equal(els.get('[data-record-toggle-icon]').dataset.lucide,'loader-circle');
  assert.equal(ui.isUnresolved(),true);
  await button.click({force:true});
  assert.equal(fixture.stopAttempts,1,'duplicate Stop clicks cannot start a second flush');
  assert.equal(fixture.transcribeAttempts,0);

  releaseStopCapture();
  await waitFor(()=>label.textContent==='Saving audio…');
  assert.equal(fixture.transcribeAttempts,0,'transcription cannot start before audio upload');
  if (action === 'system-audio') {
    assert.match(els.get('[data-telehealth-receipt-message]').textContent, /unrelated applications or speech/,
      'system-audio privacy warning remains visible while stopping');
  } else {
    assert.equal(els.get('[data-telehealth-receipt-message]').hidden,true,
      'saving progress stays in the record button without a routine paragraph');
  }
  releaseStopUpload();
  await waitFor(()=>label.textContent==='Finalising audio…');
  assert.equal(button.disabled,true);
  releaseStopSeal();
  await started;
  if(action!=='own-failed') assert.equal(label.textContent,'Submitting…','the button reports submission instead of recording');
  if(action==='own-failed') {
    await waitFor(()=>fixture.transcribeAttempts===1);
    ui.sync();
    const useAvailable=els.get('[data-telehealth-use-available]');
    await waitFor(()=>useAvailable.hidden===false);
    assert.equal(ui.isUnresolved(),true,'live failed transcription remains a barrier before explicit acceptance');
    assert.equal(useAvailable.textContent,'Use available transcript');
    await useAvailable.click();
    await waitFor(()=>fixture.useAvailableRequests.length===1);
    assert.match(fixture.useAvailableRequests[0].url,/live-failed-session\/use-available$/,
      'the post-Stop failed capture is actionable without reloading');
    await waitFor(()=>ui.isUnresolved()===false);
    assert.equal(els.get('[data-recording-mode-select]').disabled,false);
    assert.equal(els.get('[data-audio-file-input]').disabled,false);
    return;
  }
  const retry=els.get('[data-telehealth-submit-retry]');
  assert.equal(retry.hidden,false,'sealed capture exposes a separate transcription retry action');
  assert.equal(retry.disabled,true,'the initial Stop submission remains in flight');
  assert.equal(els.get('[data-record-toggle]').disabled,true,'duplicate recording actions are blocked while submitting');
  await retry.click({force:true});
  assert.equal(fixture.transcribeAttempts,1,'a duplicate click cannot submit a second request');
  releaseFirst();
  await waitFor(()=>retry.disabled===false);
  assert.equal(label.textContent,'Retry submission','failed submit returns to a truthful recovery label');
  assert.equal(button.attrs['aria-busy'],'false','saving state ends after submission failure');
  assert.ok(notices.some(([text])=>text.includes('Synthetic lost /transcribe response')),JSON.stringify(notices));
  assert.equal(retry.hidden,false,'failed submission keeps its retry action visible');
  if(action==='retry'){
    await retry.click();
    await waitFor(()=>fixture.transcribeAttempts===2);
    await waitFor(()=>retry.hidden===true);
    assert.equal(els.get('[data-record-toggle]').disabled,true,'processing continues to block a new recording');
    assert.equal(ui.isSelectedOrUnresolved(),true,'processing locks input on the current consultation');
    assert.equal(ui.isUnresolved(),false,'processing permits opening another consultation after Stop');
    transcriptId='another-synthetic-transcript';
    ui.sync();
    assert.equal(ui.isActive(),false);
    assert.equal(els.get('[data-record-toggle]').disabled,false,'the next consultation gets its ordinary Record button');
    assert.equal(els.get('[data-recording-mode-select]').disabled,false);

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
    assert.equal(els.get('[data-recording-mode-select]').disabled,false);
    assert.equal(els.get('[data-audio-file-input]').disabled,false);
  }
}
for(const action of ['cancel','late-picker','late-ack','switch-root','pagehide']) await scenario(action);
await scenario('retry');
await scenario('system-audio');
await scenario('discard');
await scenario('lost-create');
await scenario('late-start-root-switch');
await scenario('late-start-root-switch-precreate');
await scenario('partial-cancel');
await scenario('partial-accept');
await scenario('partial-retry');
await scenario('partial-silent');
await scenario('partial-late-response');
await scenario('partial-stale-action');
await scenario('partial-pages');
await scenario('partial-deferred');
await scenario('own-failed');
console.log('Telehealth rendered submission retry and discard controls: passed');
""", encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(tmp_path / "test.mjs")], cwd=tmp_path,
        check=False, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
