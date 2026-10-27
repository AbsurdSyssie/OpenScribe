"""Browser-level dictation action regressions driven by the real app.js handlers.

The Node harness supplies only browser primitives and synthetic network responses; it
does not copy dictation logic or contact a microphone/provider.
"""

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
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const sourcePath = process.argv[1];
const { createAutosaveController } = await import(pathToFileURL(sourcePath.replace(/app\.js$/, 'autosaveController.js')).href);
const { createMicVisualizer } = await import(pathToFileURL(sourcePath.replace(/app\.js$/, 'micVisualizer.js')).href);
const { DeepgramLiveStream, trimPinnedVadSpeechBuffer } = await import(pathToFileURL(sourcePath.replace(/app\.js$/, 'deepgramLiveStream.js')).href);
const { createOwnedMicStream, createVadCaptureOptions } = await import(pathToFileURL(sourcePath.replace(/app\.js$/, 'vadCapture.js')).href);
const scenario = process.argv[2];
let source = fs.readFileSync(sourcePath, 'utf8');
source = source.slice(source.indexOf('const bootstrap ='));
const exampleHelperSource = fs.readFileSync(sourcePath.replace(/app\.js$/, 'exampleConsultation.js'), 'utf8')
  .replaceAll('export function ', 'function ');
const exampleHelperSandbox = { Boolean };
exampleHelperSandbox.globalThis = exampleHelperSandbox;
vm.createContext(exampleHelperSandbox);
vm.runInContext(`${exampleHelperSource}\nglobalThis.createExampleConsultationBoundary = createExampleConsultationBoundary; globalThis.createExampleConsultationInitializer = createExampleConsultationInitializer;`, exampleHelperSandbox);
class El {
  constructor() { this.dataset={}; this.style={setProperty(){},removeProperty(){}}; this.classList={add(){},remove(){},toggle(){return false},contains(){return false}}; this.listeners={}; this.value=''; this.textContent=''; this.innerHTML=''; this.hidden=true; this.disabled=false; this.options=[]; this.selectedOptions=[]; this.children=[]; this.files=[]; this.isConnected=true; }
  addEventListener(t,f) { (this.listeners[t]??=[]).push(f); }
  dispatchEvent(e) { for (const f of this.listeners[e.type]||[]) f({...e,target:this}); return true; }
  click() { if (!this.disabled) this.dispatchEvent({type:'click',preventDefault(){}}); }
  setAttribute(k,v) { this[k]=v; } removeAttribute(k) { delete this[k]; }
  getAttribute(k) { return this[k]??null; } appendChild(x) { this.children.push(x); }
  replaceChildren(...x) { this.children=x; } insertAdjacentHTML() {} remove() {}
  querySelector() { return null; } querySelectorAll() { return []; } focus() {}
  closest() { return null; } matches() { return false; } getClientRects() { return [1]; }
  scrollIntoView() {}
}
const elements = new Map();
const get = (selector) => { if (!elements.has(selector)) elements.set(selector,new El()); return elements.get(selector); };
const document = {body:new El(),activeElement:null,visibilityState:'visible',addEventListener(){},dispatchEvent(){return true},querySelector:get,querySelectorAll(){return []},createElement(){return new El()},createDocumentFragment(){return new El()},getElementById(){return null}};
const window = {document,location:{href:'http://testserver/transcribe',origin:'http://testserver',protocol:'http:',host:'testserver',search:''},history:{pushState(){},replaceState(){}},localStorage:{getItem(){return null},setItem(){}},setTimeout(){return 1},clearTimeout(){},setInterval(){return 1},clearInterval(){},requestAnimationFrame(){return 1},cancelAnimationFrame(){},matchMedia(){return {matches:false,addEventListener(){}}},addEventListener(){},confirm(){return true},URL,EventSource:class {addEventListener(){} close(){}},lucide:{createIcons(){}}};
globalThis.document=document; globalThis.window=window;
const bootstrap={activeTranscriptId:'tx-a',activeIngestionMode:'whole_file',nextLiveChunkSequenceNo:1,hasSttSelection:true,hasDictationSttSelection:true,dictationLiveStreamingAvailable:scenario.startsWith('live-'),hasLlmSelection:true,sttAvailable:true,dictationSttAvailable:true,dictationSttSelected:true,dictationSttStatusMessage:'',llmAvailable:true,dictation:{effective_text:'typed words',segment_count:0},userAppPreferences:{},activeTranscriptHasContent:false};
let recorder;
class Recorder {
  constructor(stream){this.stream=stream;this.state='inactive';this.mimeType='audio/webm';this.listeners={};this.stopCount=0;recorder=this;}
  addEventListener(t,f){(this.listeners[t]??=[]).push(f)} start(){this.state='recording'} pause(){this.state='paused'} resume(){this.state='recording'}
  stop(){this.stopCount++;this.state='inactive'} emit(t,data={}){for(const fn of this.listeners[t]||[]) fn({type:t,...data});}
}
window.MediaRecorder=Recorder;
class FakeWebSocket {
  static OPEN=1; static CLOSING=2; static CONNECTING=0;
  constructor(url){this.url=url;this.readyState=0;this.bufferedAmount=0;this.sent=[];this.listeners={};FakeWebSocket.last=this}
  addEventListener(t,f){(this.listeners[t]??=[]).push(f)}
  emit(t,event={}){for(const fn of this.listeners[t]||[]) fn(event)}
  send(value){this.sent.push(value)}
  close(){this.readyState=3}
  open(){this.readyState=FakeWebSocket.OPEN;this.emit('open')}
  json(value){this.emit('message',{data:JSON.stringify(value)})}
}
globalThis.WebSocket=FakeWebSocket;
const vadInstances=[];
let deferVadPreparation=false;
let resolveVadPreparation;
class FakeMicVAD {
  static async new(options){const instance=new FakeMicVAD(options);vadInstances.push(instance);if(deferVadPreparation)return new Promise(resolve=>{resolveVadPreparation=()=>resolve(instance)});return instance}
  constructor(options){this.options=options;this.frameProcessor={audioBuffer:[]};this.started=false;this.stream=null;this.destroyed=false}
  async start(){this.stream=await this.options.getStream();this.started=true}
  async pause(){if(this.started){this.started=false;await this.options.pauseStream?.(this.stream);this.options.onSpeechEnd?.(new Float32Array())}}
  destroy(){this.destroyed=true}
}
window.vad={MicVAD:FakeMicVAD};
let trackStops=0;
let mediaRequests=0;
let resolveMedia;
const mediaPromise = new Promise((resolve)=>{resolveMedia=resolve});
let useDeferredMedia=false;
let denyMedia=false;
const calls=[];
let deferPreview=true;
const previewResolvers=[];
let previewResult='recognized synthetic words';
let deferPatch=false;
let patchResolve;
let fetchWorkspaceCount=0;
const response=(payload={})=>({ok:true,json:async()=>payload});
const csrfFetch=async (url,options={})=>{
  const method=options.method||'GET';
  calls.push({url,method,body:options.body});
  if(url.includes('/preview-audio-file')) return deferPreview ? new Promise((resolve,reject)=>previewResolvers.push({resolve,reject})) : response({text:previewResult});
  if(url.endsWith('/post-consultation-dictation')) return deferPatch ? new Promise(resolve=>{patchResolve=resolve}) : response({effective_text:'typed words\n\n'+previewResult,segment_count:0});
  if(url.endsWith('/generate-output')) return response({id:'generated-draft'});
  return response({});
};
const no=()=>{}; const proxy=new Proxy({}, {get(_t,k){return (..._a)=> k==='isMarked' ? false : undefined}});
const structured=new Proxy({serializeCurrentNoteEditor:()=>({mode:'freeform',edited_output_text:''}),bootstrapFromDom:no},{get:(t,k)=>t[k]??no});
const deps={attachTranscribeActions:no,readTranscribeBootstrap:()=>bootstrap,clearDelayedGenerationStatus:no,createDocumentNavigator:()=>proxy,createInitialNoteRenderPreserver:()=>()=>false,formatWorkspaceCreatedAt:()=>'',generationFailureDetailsHtml:()=>'',generationFailureMessage:()=>'',generationLoadingHtml:()=>'',scheduleDelayedGenerationStatus:no,createTranscribeLayout:()=>({attach:no}),createAudioCaptureController:()=>({attachDomListeners:no,isLiveCaptureUiActive:()=>false,isCaptureUiActive:()=>false,shouldPreserveLiveMicStatus:()=>false}),createStructuredEditor:()=>structured,attachSmartPhraseExpander:no,attachNoteReordering:no,createGuidedTour:()=>({attach:no}),csrfFetch,csrfToken:()=> 'synthetic-csrf',DeepgramLiveStream,trimPinnedVadSpeechBuffer,createOwnedMicStream,createVadCaptureOptions,isWorkingNoteTargetId:()=>false,workingNoteTargetId:()=>'',captureNoteDirtyBaseline:()=>'',noteBaselineForSave:()=>'',createTemplateSuggestionController:()=>({onTranscriptChanged:no,attach:no}),createExampleConsultationBoundary:exampleHelperSandbox.createExampleConsultationBoundary,createExampleConsultationInitializer:exampleHelperSandbox.createExampleConsultationInitializer,createMultipleProblemsController:()=>proxy,createSplitAnalysisRestorationPoller:()=>proxy,createSplitGenerateController:()=>proxy,createSplitPartialActionsController:()=>proxy,createSplitReviewController:()=>proxy,createWorkspaceFetchCoordinator:()=>proxy,dispatchTemplateGeneration:async()=>false,isConsultationSplitCreateEnabled:()=>false,splitDiagnosticMessage:()=>'',formatSessionRailCreatedAt:()=>'',keepSessionRailItemVisible:no,reconcileSessionRailItems:()=>[],sessionRailGroup:()=>'',sortSessionRailItems:()=>[],syncSessionRailCreatedAt:no};
// Exercise the actual shared save controller; the unrelated transcript-turn
// editor stays inactive in these dictation-only scenarios.
deps.createAutosaveController = (options) => createAutosaveController({ ...options, timers: window });
deps.createMicVisualizer = createMicVisualizer;
deps.createTranscriptTurnsEditor = () => ({ dirty: false, flush: async () => null });
class Evt{constructor(type,init={}){this.type=type;Object.assign(this,init);this.defaultPrevented=false} preventDefault(){this.defaultPrevented=true}}
const sandbox={...deps,document,window,globalThis:null,navigator:{mediaDevices:{getUserMedia:()=>{mediaRequests++;return useDeferredMedia?mediaPromise:(denyMedia?Promise.reject(new Error('synthetic denial')):Promise.resolve({getTracks:()=>[{stop(){trackStops++}}]}))}}},MediaRecorder:Recorder,console,URL,Event:Evt,CustomEvent:Evt,HTMLElement:El,HTMLInputElement:El,HTMLTextAreaElement:El,Element:El,Blob,FormData,Date,Math,Number,String,Boolean,Array,Object,JSON,Map,Set,Promise,WeakMap,WeakSet,Intl,AbortController,fetch:async()=>{fetchWorkspaceCount++;return response({})},setTimeout:window.setTimeout,clearTimeout:window.clearTimeout}; sandbox.globalThis=sandbox; vm.createContext(sandbox); vm.runInContext(source,sandbox,{filename:sourcePath});
const modal=get('[data-dictation-modal]'); modal.hidden=false;
const input=get('[data-dictation-combined-input]'); input.value='typed words';
const selector=get('[data-template-select]'); selector.value='template-a'; selector.options=[{value:'template-a',dataset:{templateMode:'freeform',templateName:'Synthetic'},textContent:'Synthetic'}]; selector.selectedOptions=selector.options;
get('[data-dictation-template-select]').value='template-a';
const record=get('[data-dictation-record-toggle]'), pause=get('[data-dictation-pause-recording]');
const save=get('[data-dictation-save]'), generate=get('[data-dictation-save-generate]');
const turns=async(n=8)=>{for(let i=0;i<n;i++) await Promise.resolve()};
const waitFor=async(fn)=>{for(let i=0;i<100;i++){if(fn())return;await new Promise(r=>setTimeout(r,0))} throw Error(`timed out waiting; record disabled=${record.disabled}, state=${record.dataset.state}, status=${get('[data-dictation-mic-status]').textContent}, calls=${JSON.stringify(calls.map(c=>[c.method,c.url]))}`)};
if(scenario==='live-save' || scenario==='live-generate' || scenario==='live-paused-save' || scenario==='live-two-streams' || scenario==='live-failure' || scenario==='live-permission-close' || scenario==='live-delayed-vad' || scenario==='live-ready-failure-delayed-vad'){
  if(scenario==='live-permission-close') useDeferredMedia=true;
  if(scenario==='live-delayed-vad' || scenario==='live-ready-failure-delayed-vad') deferVadPreparation=true;
  const liveToggle=get('[data-dictation-live-toggle]');
  liveToggle.click();
  assert.equal(liveToggle.getAttribute('aria-pressed'),'true');
  record.click();
  await waitFor(()=>FakeWebSocket.last && vadInstances.length>0);
  const socket=FakeWebSocket.last;
  assert.match(socket.url,/post-consultation-dictation\/deepgram-stream$/);
  socket.open(); await turns(10);
  assert.deepEqual(JSON.parse(socket.sent[0]),{type:'start',csrf_token:'synthetic-csrf'});
  if(scenario==='live-delayed-vad' || scenario==='live-ready-failure-delayed-vad'){
    if (scenario==='live-ready-failure-delayed-vad') {
      socket.json({type:'ready',stream_id:'11111111-1111-4111-8111-111111111111',sample_rate:16000,encoding:'pcm_s16le'});
      await turns(5);
    }
    socket.emit('error'); await turns(20);
    assert.equal(record.dataset.state,'idle');
    resolveVadPreparation(); await turns(20);
    assert.equal(vadInstances[0].destroyed,true,'late VAD setup is destroyed after socket startup fails');
    assert.equal(mediaRequests,0,'socket failure while VAD initializes never starts microphone capture');
    assert.equal(record.dataset.state,'idle');
    assert.equal(get('[data-dictation-save]').disabled,false,'a failure before microphone capture does not block typed-only save');
  } else {
    socket.json({type:'ready',stream_id:'11111111-1111-4111-8111-111111111111',sample_rate:16000,encoding:'pcm_s16le'});
    if(scenario==='live-permission-close'){
      await waitFor(()=>mediaRequests>0);
      get('[data-dictation-cancel]').click(); await turns(5);
      resolveMedia({getTracks:()=>[{stop(){trackStops++}}]}); await turns(30);
      assert.equal(modal.hidden,true);
      assert.ok(trackStops>=1,'late microphone permission grant is stopped after modal cancellation');
      assert.equal(socket.readyState,3,'cancellation closes the pending live stream');
    } else {
      await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Recording dictation...');
      const vad=vadInstances.at(-1);
      if(scenario==='live-two-streams') {
        const firstSocket=socket;
        firstSocket.json({type:'dictation_preview',window_no:0,confirmed_text:'First window',interim_text:''});
        firstSocket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'First window'});
        firstSocket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'First window'});
        assert.equal(input.value,'typed words\n\nFirst window','duplicate checkpoint is ignored in the first stream');
        record.click();
        await waitFor(()=>firstSocket.sent.some(value=>typeof value==='string'&&JSON.parse(value).type==='stop'));
        firstSocket.json({type:'finalized'});
        await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Live dictation ready.');

        record.click();
        await waitFor(()=>FakeWebSocket.last && FakeWebSocket.last!==firstSocket && vadInstances.length>1);
        const secondSocket=FakeWebSocket.last;
        secondSocket.open(); await turns(10);
        secondSocket.json({type:'ready',stream_id:'22222222-2222-4222-8222-222222222222',sample_rate:16000,encoding:'pcm_s16le'});
        await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Recording dictation...');
        secondSocket.json({type:'dictation_preview',window_no:0,confirmed_text:'Second window',interim_text:''});
        secondSocket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'Second window'});
        secondSocket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'Second window'});
        assert.equal(input.value,'typed words\n\nFirst window\n\nSecond window','window zero from the second stream appends once and preserves the first window');
        save.click();
        await waitFor(()=>secondSocket.sent.some(value=>typeof value==='string'&&JSON.parse(value).type==='stop'));
        secondSocket.json({type:'finalized'});
        await waitFor(()=>calls.some(c=>c.method==='PATCH'));
        assert.equal(JSON.parse(calls.find(c=>c.method==='PATCH').body).combined_text,'typed words\n\nFirst window\n\nSecond window');
      } else if(scenario==='live-save' || scenario==='live-generate' || scenario==='live-paused-save'){
        if(scenario==='live-paused-save') {
          pause.click(); await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Dictation paused.');
          assert.equal(vad.started,false,'live pause pauses the shared VAD capture');
          pause.click(); await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Recording dictation...');
          assert.equal(vad.started,true,'live resume restarts the shared VAD capture');
          pause.click(); await waitFor(()=>get('[data-dictation-mic-status]').textContent==='Dictation paused.');
        }
        socket.json({type:'dictation_preview',window_no:0,confirmed_text:'Confirmed words',interim_text:' changing words'});
        assert.equal(get('[data-dictation-live-confirmed]').textContent,'Confirmed words');
        assert.equal(get('[data-dictation-live-interim]').textContent,'changing words');
        assert.equal(get('[data-dictation-word-count]').textContent,'4 words','count includes provider-confirmed words but excludes interim text');
        assert.equal(input.value,'typed words','provisional and confirmed live text stays outside the editable textarea before checkpoint');
        socket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'Confirmed words'});
        socket.json({type:'window_committed',window_no:0,next_sample_offset:0,text:'Confirmed words'});
        assert.equal(input.value,'typed words\n\nConfirmed words','each finalized window appends only once');
        assert.equal(get('[data-dictation-word-count]').textContent,'4 words','checkpointing does not double-count confirmed words');
        (scenario==='live-generate' ? generate : save).click();
        await waitFor(()=>socket.sent.some(value=>typeof value==='string'&&JSON.parse(value).type==='stop'));
        socket.json({type:'finalized'});
        await waitFor(()=>calls.some(c=>c.method==='PATCH'));
        assert.equal(JSON.parse(calls.find(c=>c.method==='PATCH').body).combined_text,'typed words\n\nConfirmed words');
        if(scenario==='live-generate') {
          await waitFor(()=>calls.some(c=>c.url.endsWith('/generate-output')));
          assert.equal(calls.filter(c=>c.method==='PATCH').length,1);
        }
      } else {
        socket.json({type:'dictation_preview',window_no:0,confirmed_text:'Keep this',interim_text:' discard this'});
        socket.json({type:'error',code:'stream_failed',unresolved:true});
        await waitFor(()=>get('[data-dictation-live-failure]').hidden===false);
        assert.equal(get('[data-dictation-live-confirmed]').textContent,'Keep this');
        assert.equal(get('[data-dictation-live-interim]').textContent,'');
        assert.equal(save.disabled,true,'failed live capture requires an explicit choice before save');
        assert.equal(calls.some(c=>c.method==='PATCH'),false);
        get('[data-dictation-live-use-confirmed]').click();
        assert.equal(input.value,'typed words\n\nKeep this');
        assert.equal(save.disabled,false);
        await waitFor(()=>vad.destroyed);
      }
    }
  }
} else if(scenario==='typed'){
  save.click(); await waitFor(()=>calls.some(c=>c.method==='PATCH'));
  assert.deepEqual(calls.filter(c=>c.method==='POST').map(c=>c.url),[]);
  assert.equal(calls.filter(c=>c.method==='PATCH').length,1);
} else if(scenario==='recording-save' || scenario==='recording-generate' || scenario==='paused-save' || scenario==='stop-save-race'){
  record.click(); await waitFor(()=>recorder);
  recorder.emit('dataavailable',{data:new Blob(['first'])});
  if(scenario==='paused-save'){pause.click();assert.equal(recorder.state,'paused')}
  if(scenario==='stop-save-race') {record.click(); save.click();}
  else if(scenario==='recording-generate') generate.click();
  else {
    save.click();
    if (scenario==='recording-save') save.click();
  }
  assert.equal(recorder.stopCount,1);
  assert.equal(calls.some(c=>c.url.includes('/preview-audio-file')),false,'preview must wait for final recorder events');
  recorder.emit('dataavailable',{data:new Blob(['final'])});
  recorder.emit('stop');
  await waitFor(()=>calls.some(c=>c.url.includes('/preview-audio-file')));
  if (scenario==='recording-save') save.click();
  assert.equal(input.value,'typed words','transcription must not append before preview resolves');
  previewResolvers[0].resolve(response({text:previewResult}));
  const endpoint=scenario==='recording-generate'?'/generate-output':'/post-consultation-dictation';
  await waitFor(()=>calls.some(c=>c.url.endsWith(endpoint)));
  const previewIndex=calls.findIndex(c=>c.url.includes('/preview-audio-file'));
  const actionIndex=calls.findIndex(c=>c.url.endsWith(endpoint));
  assert.ok(previewIndex>=0 && actionIndex>previewIndex);
  const previewBody=calls[previewIndex].body;
  assert.equal((await previewBody.get('audio').text()),'firstfinal');
  assert.match(input.value,/recognized synthetic words/);
  assert.equal(calls.filter(c=>c.url.includes('/preview-audio-file')).length,1);
  assert.equal(recorder.stopCount,1);
  if(scenario==='recording-generate') {
    const patchIndex=calls.findIndex(c=>c.method==='PATCH');
    assert.ok(patchIndex>previewIndex && actionIndex>patchIndex);
    assert.equal(JSON.parse(calls[patchIndex].body).combined_text,'typed words\n\nrecognized synthetic words');
    assert.equal(calls.filter(c=>c.method==='PATCH').length,1);
  } else {
    assert.equal(JSON.parse(calls[actionIndex].body).combined_text,'typed words\n\nrecognized synthetic words');
  }
} else if(scenario==='record-button-stops' || scenario==='paused-record-button-stops'){
  record.click(); await waitFor(()=>recorder); await turns(12);
  assert.equal(record.disabled,false,'Record must become selectable as Stop after microphone startup settles');
  if(scenario==='paused-record-button-stops') pause.click();
  record.click();
  assert.equal(recorder.stopCount,1,'second Record click must stop the active capture');
  recorder.emit('dataavailable',{data:new Blob(['audio'])}); recorder.emit('stop');
  await waitFor(()=>calls.some(c=>c.url.includes('/preview-audio-file')));
  previewResolvers[0].resolve(response({text:previewResult})); await turns(20);
  assert.match(input.value,/recognized synthetic words/);
} else if(scenario==='permission-denied'){
  denyMedia=true; record.click(); await turns(12);
  assert.equal(record.disabled,false,'Record must be selectable again after microphone permission is denied');
  assert.equal(record.dataset.state,'idle');
} else if(scenario==='failed-retry'){
  record.click(); await waitFor(()=>recorder); recorder.emit('dataavailable',{data:new Blob(['audio'])}); save.click();
  recorder.emit('dataavailable',{data:new Blob(['tail'])}); recorder.emit('stop');
  await waitFor(()=>calls.some(c=>c.url.includes('/preview-audio-file')));
  previewResolvers[0].resolve({ok:false,json:async()=>({detail:'synthetic failure'})}); await turns(20);
  assert.equal(calls.some(c=>c.method==='PATCH'),false);
  assert.equal(input.value,'typed words');
  assert.equal(get('[data-dictation-retry-transcription]').hidden,false);
  deferPreview=false; previewResult='retry synthetic text'; get('[data-dictation-retry-transcription]').click();
  await waitFor(()=>calls.filter(c=>c.url.includes('/preview-audio-file')).length===2);
  await waitFor(()=>/retry synthetic text/.test(input.value));
  save.click();
  await waitFor(()=>calls.some(c=>c.method==='PATCH'));
  assert.equal(calls.filter(c=>c.method==='PATCH').length,1);
  assert.match(input.value,/retry synthetic text/);
} else if(scenario==='empty-preview'){
  record.click(); await waitFor(()=>recorder); recorder.emit('dataavailable',{data:new Blob(['audio'])}); save.click();
  recorder.emit('dataavailable',{data:new Blob(['tail'])}); recorder.emit('stop');
  await waitFor(()=>calls.some(c=>c.url.includes('/preview-audio-file')));
  previewResolvers[0].resolve(response({text:''})); await turns(20);
  assert.equal(calls.some(c=>c.method==='PATCH'),false);
  assert.equal(input.value,'typed words');
  assert.equal(get('[data-dictation-retry-transcription]').hidden,false);
  deferPreview=false; previewResult='recovered synthetic text'; get('[data-dictation-retry-transcription]').click();
  await waitFor(()=>/recovered synthetic text/.test(input.value)); save.click();
  await waitFor(()=>calls.some(c=>c.method==='PATCH'));
  assert.equal(calls.filter(c=>c.method==='PATCH').length,1);
} else if(scenario==='late-close'){
  record.click(); await waitFor(()=>recorder); recorder.emit('dataavailable',{data:new Blob(['audio'])}); save.click();
  recorder.emit('dataavailable',{data:new Blob(['tail'])}); recorder.emit('stop');
  await waitFor(()=>calls.some(c=>c.url.includes('/preview-audio-file')));
  get('[data-dictation-cancel]').click(); await turns();
  previewResolvers[0].resolve(response({text:previewResult})); await turns(30);
  assert.doesNotMatch(input.value,/recognized synthetic words/);
  assert.equal(modal.hidden,true);
  assert.equal(calls.some(c=>c.method==='PATCH'||c.url.endsWith('/generate-output')),false);
} else if(scenario==='permission-close'){
  useDeferredMedia=true; record.click(); await turns(5);
  get('[data-dictation-cancel]').click(); resolveMedia({getTracks:()=>[{stop(){trackStops++}}]}); await turns(30);
  assert.equal(recorder,undefined,'late microphone permission must not start a recorder');
  assert.equal(trackStops,1);
} else if(scenario==='generate-cancel-after-save'){
  deferPatch=true; input.value='typed words with unsaved edit'; input.dispatchEvent(new Evt('input',{bubbles:true})); generate.disabled=false; generate.click();
  await waitFor(()=>calls.some(c=>c.method==='PATCH'));
  get('[data-dictation-cancel]').click(); await turns();
  patchResolve(response({effective_text:'typed words',segment_count:0})); await turns(30);
  assert.equal(calls.some(c=>c.url.endsWith('/generate-output')),false,'generation must not start after the modal context was canceled during source save');
} else throw Error(`unknown scenario ${scenario}`);
'''


def run_scenario(name: str) -> None:
    app_js = Path(__file__).resolve().parents[1] / "app/static/js/transcribe/app.js"
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", HARNESS, str(app_js), name],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("scenario", ["recording-save", "recording-generate", "paused-save", "stop-save-race"])
def test_save_actions_stop_capture_and_wait_for_final_preview(scenario: str) -> None:
    run_scenario(scenario)


def test_record_button_stops_capture_after_startup_settles() -> None:
    run_scenario("record-button-stops")


def test_record_button_stops_paused_capture() -> None:
    run_scenario("paused-record-button-stops")


def test_record_button_recovers_after_permission_denial() -> None:
    run_scenario("permission-denied")


def test_typed_only_save_keeps_existing_flow() -> None:
    run_scenario("typed")


def test_failed_preview_keeps_audio_and_explicit_retry_can_save() -> None:
    run_scenario("failed-retry")


def test_empty_preview_aborts_save_and_requires_retry_then_save() -> None:
    run_scenario("empty-preview")


def test_late_preview_after_close_does_not_save_or_append() -> None:
    run_scenario("late-close")


def test_closing_during_microphone_permission_prevents_capture_start() -> None:
    run_scenario("permission-close")


def test_generate_does_not_start_if_modal_closes_during_source_save() -> None:
    run_scenario("generate-cancel-after-save")


def test_live_dictation_preview_checkpoints_append_once_and_save_after_final_drain() -> None:
    run_scenario("live-save")


def test_live_dictation_save_and_generate_waits_for_final_drain() -> None:
    run_scenario("live-generate")


def test_live_dictation_paused_capture_resumes_then_saves() -> None:
    run_scenario("live-paused-save")


def test_live_dictation_resets_window_deduplication_for_each_stream() -> None:
    run_scenario("live-two-streams")


def test_live_dictation_failure_requires_explicit_confirmed_text_choice() -> None:
    run_scenario("live-failure")


def test_live_dictation_rejected_socket_destroys_late_vad_setup() -> None:
    run_scenario("live-delayed-vad")


def test_live_dictation_ready_then_socket_failure_does_not_start_late_vad() -> None:
    run_scenario("live-ready-failure-delayed-vad")


def test_live_dictation_cancel_stops_late_microphone_permission_grant() -> None:
    run_scenario("live-permission-close")
