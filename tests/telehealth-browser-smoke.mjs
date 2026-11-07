// Synthetic browser-source smoke test. No network, patient data, mic or provider calls.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Make isolated .mjs copies; this runs on CI Node versions that treat plain
// .js files as CommonJS, without changing production module configuration.
const sourceDir = join(dirname(fileURLToPath(import.meta.url)), '../app/static/js/transcribe');
const directory = await mkdtemp(join(tmpdir(), 'openscribe-telehealth-smoke-'));
for (const name of ['telehealthTimeline', 'telehealthVad', 'telehealthDiagnostics', 'telehealthCapture']) {
  let source = await readFile(join(sourceDir, name + '.js'), 'utf-8');
  if (name === 'telehealthCapture') {
    source = source.replace(
      './telehealthTimeline.js?v=20261104-telehealth-timing', './telehealthTimeline.mjs',
    ).replace(
      './telehealthVad.js?v=20261106-speech-vad', './telehealthVad.mjs',
    );
  }
  await writeFile(join(directory, name + '.mjs'), source, 'utf-8');
}
const load = async name => import(pathToFileURL(join(directory, name + '.mjs')).href);
const { createTelehealthCapture } = await load('telehealthCapture');
const { createTelehealthVadMonitor } = await load('telehealthVad');
const { createTelehealthDiagnostics } = await load('telehealthDiagnostics');

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

class Track {
  constructor(kind, surface) {
    this.kind = kind;
    this.surface = surface;
    this.readyState = 'live';
    this.muted = false;
    this.stopped = false;
    this.listeners = {};
  }
  getSettings() { return this.kind === 'video' ? { displaySurface: this.surface } : {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener(name, fn) {
    this.listeners[name] = (this.listeners[name] || []).filter(x => x !== fn);
  }
  stop() { this.stopped = true; }
  clone() { return new Track(this.kind, this.surface); }
}

class Node {
  connect(target) { return target; }
  disconnect() {}
}
class Context {
  constructor() {
    this.sampleRate = 48000;
    this.currentTime = 0;
    this.state = 'running';
    this.destination = {};
    this.audioWorklet = { addModule: async () => {} };
  }
  createMediaStreamSource() { return new Node(); }
  createGain() { const gain = new Node(); gain.gain = { value: 1 }; return gain; }
  async suspend() { this.state = 'suspended'; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
}
class Tap extends Node {
  constructor() {
    super();
    this.port = {
      onmessage: null,
      postMessage: message => {
        if (message.type === 'flush') {
          queueMicrotask(() => this.port.onmessage({
            data: { type: 'flushed', requestId: message.requestId },
          }));
        }
      },
    };
  }
}
globalThis.window = { AudioContext: Context, AudioWorkletNode: Tap, setTimeout, clearTimeout };

function sources(surface, audioIncluded) {
  const video = new Track('video', surface);
  const patient = new Track('audio');
  const clinician = new Track('audio');
  const display = {
    getTracks: () => audioIncluded ? [video, patient] : [video],
    getAudioTracks: () => audioIncluded ? [patient] : [],
    getVideoTracks: () => [video],
  };
  const observed = { options: null, microphoneRequests: 0 };
  return {
    observed, video, clinician,
    mediaDevices: {
      async getDisplayMedia(options) { observed.options = options; return display; },
      async getUserMedia() {
        observed.microphoneRequests++;
        return { getTracks: () => [clinician], getAudioTracks: () => [clinician] };
      },
    },
  };
}

function captureFixture(env, allowWindow = false) {
  const events = [];
  const recorder = createTelehealthCapture({
    mediaDevices: env.mediaDevices,
    AudioContextCtor: Context,
    AudioWorkletNodeCtor: Tap,
    onFrames: () => true,
    confirmWindowAudio: () => allowWindow,
    onDiagnostic: event => events.push(event),
  });
  return { recorder, events };
}

const tab = sources('browser', true);
const tabFixture = captureFixture(tab);
await tabFixture.recorder.start();
assert.equal(tab.observed.microphoneRequests, 1);
assert.equal(tab.observed.options.video, true);
assert.equal(tab.observed.options.windowAudio, 'window');
assert.equal(tab.observed.options.systemAudio, 'exclude');
assert.equal(tab.observed.options.surfaceSwitching, 'exclude');
await tabFixture.recorder.stop();

const windowSource = sources('window', true);
const windowFixture = captureFixture(windowSource, true);
await windowFixture.recorder.start();
assert.ok(windowFixture.events.some(e => e.event === 'window_acknowledged'));
await windowFixture.recorder.stop();

const noAudio = sources('window', false);
const noAudioFixture = captureFixture(noAudio, true);
await assert.rejects(noAudioFixture.recorder.start(), /No audio was shared/);
assert.equal(noAudio.observed.microphoneRequests, 0);
assert.ok(noAudioFixture.events.some(e => e.event === 'picker_rejected' && e.code === 'missing_audio'));
assert.equal(noAudio.video.stopped, true);

const declined = sources('window', true);
await assert.rejects(captureFixture(declined, false).recorder.start(), /cancelled/);
assert.equal(declined.observed.microphoneRequests, 0);

const screen = sources('monitor', true);
await assert.rejects(captureFixture(screen, true).recorder.start(), /Full-screen/);
assert.equal(screen.observed.microphoneRequests, 0);

const log = createTelehealthDiagnostics({ now: () => '2026-11-06T00:00:00Z' });
log.emit('picker_selected', {
  surface: 'window', title: 'SENSITIVE WINDOW TITLE',
  error: 'SENSITIVE ERROR TEXT', url: 'https://sensitive.example/',
});
for (let i = 0; i < 250; i++) log.emit('upload_accepted', { channel: 'patient', part: i });
assert.equal(log.length, 200);
assert.ok(!JSON.stringify(log.snapshot()).includes('SENSITIVE'));
assert.equal(log.snapshot().events[0].part, 50);

// Automatic reporting is content-free and does not depend on a download action.
const transmitted = [];
const reporter = createTelehealthDiagnostics({
  generateId: () => 'a'.repeat(32),
  sendBatch: async (target, id, events) => {
    transmitted.push({ target, id, events });
    return { accepted: events.length };
  },
});
assert.equal(reporter.begin('synthetic-owner-root'), 'a'.repeat(32));
reporter.emit('picker_selected', {
  surface: 'window',
  title: 'SENSITIVE WINDOW TITLE',
  url: 'https://sensitive.example',
});
reporter.emit('capture_failed', { code: 'missing_audio' });
await reporter.flush();
assert.equal(transmitted.length, 1);
assert.equal(transmitted[0].events.length, 2);
assert.equal(transmitted[0].id, 'a'.repeat(32));
assert.ok(!JSON.stringify(transmitted).includes('SENSITIVE'));
assert.ok(transmitted[0].events.every(event => !Object.hasOwn(event, 'at')));
reporter.clear();

class Stream {
  constructor(tracks) { this.tracks = tracks; }
}
class FakeVad {
  static instances = [];
  static async new(options) {
    const instance = {
      options,
      async start() {},
      async pause() {},
      async destroy() {},
    };
    FakeVad.instances.push(instance);
    return instance;
  }
}
const heard = [], vadStatus = [];
const originalPatient = new Track('audio'), originalClinician = new Track('audio');
const vad = createTelehealthVadMonitor({
  VadCtor: FakeVad,
  MediaStreamCtor: Stream,
  onSpeechFrame: event => heard.push(event),
  onState: event => vadStatus.push(event),
});
vad.start({ patient: originalPatient, clinician: originalClinician });
await tick();
assert.equal(FakeVad.instances.length, 2);
assert.equal(vadStatus.filter(v => v.status === 'ready').length, 2);
FakeVad.instances[0].options.onFrameProcessed({ isSpeech: 0.1 }, new Float32Array(512));
FakeVad.instances[0].options.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512));
assert.equal(heard.filter(v => v.speechMs === 0).length, 1);
assert.equal(heard.filter(v => v.speechMs > 0).length, 1);
vad.stop();
assert.equal(originalPatient.stopped, false);
assert.equal(originalClinician.stopped, false);
const count = heard.length;
FakeVad.instances[0].options.onFrameProcessed({ isSpeech: 0.9 }, new Float32Array(512));
assert.equal(heard.length, count);

await rm(directory, { recursive: true, force: true });
console.log('Synthetic Telehealth tab/window capture, VAD isolation and diagnostics passed');
