"""Synthetic Node orchestration test: interrupted upload retains exact replay identity."""
import shutil
import subprocess
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_capture_stop_drain_seal_and_retry_reuse_source_identity(tmp_path):
    source = (ROOT / "app/static/js/transcribe/telehealthRecordingClient.js").read_text(
        encoding="utf-8"
    )
    source = source.replace(
        "./telehealthCapture.js?v=20261106-window-vad", "./fake.mjs"
    ).replace(
        "./telehealthWindows.js?v=20261104-telehealth-windows", "./fake.mjs"
    ).replace(
        "./telehealthWav.js?v=20261105-telehealth-wav", "./fake.mjs"
    ).replace(
        "./telehealthRecoveryJournal.js?v=20261105-cross-tab-journal", "./journal.mjs"
    )
    (tmp_path / "journal.mjs").write_text(
        (ROOT / "app/static/js/transcribe/telehealthRecoveryJournal.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "client.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "fake.mjs").write_text(
        r"""
const fixture = () => globalThis.__telehealthFake;
export function createTelehealthCapture(options) {
  fixture().events.push('display-picker-first');
  return {
    start: async () => ({ sampleRate: 48000 }),
    stop: async () => { fixture().events.push('capture-stop'); },
  };
}
export function createTelehealthWindows({ onWindow }) {
  let finished = false;
  let pending = [];
  const chunks = [
    { channel: 'clinician', startFrame: 0, endFrame: 480,
      samples: new Float32Array(480), sampleRate: 48000 },
    { channel: 'patient', startFrame: 0, endFrame: 480,
      samples: new Float32Array(480), sampleRate: 48000 },
  ];
  return {
    ingest: () => true,
    async finish() {
      finished = true;
      pending = [...chunks];
      while (pending.length) {
        await onWindow(pending[0]); // Keep failed chunk for exact retry.
        pending.shift();
      }
    },
    async retryPending() {
      while (pending.length) {
        await onWindow(pending[0]);
        pending.shift();
      }
    },
    status() { return { finished }; },
  };
}
export function encodeTelehealthWavWindow(window) {
  return { bytes: new Uint8Array([82, 73, 70, 70]),
    spans: [{ file_start_sample: 0, file_end_sample: 480,
      timeline_start_frame: window.startFrame, timeline_end_frame: window.endFrame }] };
}
""",
        encoding="utf-8",
    )
    (tmp_path / "test.mjs").write_text(
        r"""
import assert from 'node:assert/strict';
import { createTelehealthRecordingClient } from './client.mjs';
Object.defineProperty(globalThis, 'crypto', {
  configurable: true,
  value: { randomUUID: (() => {
    let i = 0;
    return () => '00000000-0000-4000-8000-' + String(++i).padStart(12, '0');
  })() },
});
globalThis.__telehealthFake = { events: [] };
const history = globalThis.__telehealthFake.events;
const attempts = [];
let failFirst = true;
const fetcher = async (path, opts = {}) => {
  history.push(opts.method || 'GET');
  if (path.endsWith('/telehealth/captures')) {
    assert.equal(history[0], 'display-picker-first');
    return { ok: true, json: async () => ({ capture_session_id: 'session-uuid', status: 'active' }) };
  }
  if (path.endsWith('/stop')) {
    history.push('stopped-on-server');
    return { ok: true, json: async () => ({ status: 'stopping' }) };
  }
  if (path.endsWith('/seal')) {
    history.push('sealed-on-server');
    return { ok: true, json: async () => ({ status: 'ready' }) };
  }
  if (path.endsWith('/transcribe')) {
    history.push('transcribed-on-server');
    return { ok: true, json: async () => ({
      capture_session_id: 'session-uuid', status: 'processing', ingestion_job_ids: ['clinician-job','patient-job'],
    }) };
  }
  if (path.endsWith('/audio-parts')) {
    history.push('audio-window-upload');
    const identity = opts.headers?.['Idempotency-Key'];
    const channel = opts.body.get('channel');
    attempts.push([channel, identity]);
    if (failFirst) {
      failFirst = false;
      return { ok: false, json: async () => ({ error: { message: 'Synthetic lost upload response' } }) };
    }
    return { ok: true, json: async () => ({
      capture_session_id: 'session-uuid',
      part: { channel, sequence_no: Number(opts.body.get('sequence_no')) },
    }) };
  }
  throw new Error('Unexpected synthetic route: ' + path);
};
const durable = new Map();
const journal = {
  async check() { history.push('journal-ready'); },
  async persist(part) { history.push('journal-committed'); durable.set(part.key, part); },
  async remove(part) { durable.delete(part.key); },
  async clear() { durable.clear(); },
};
const stopPhases = [];
const client = createTelehealthRecordingClient({
  transcriptId: 'transcript-uuid',
  csrfFetch: fetcher, recoveryJournal: journal,
  pacedUpload: async () => {},
  onStopProgress: phase => stopPhases.push(phase),
});
const started = await client.start();
assert.equal(started.status, 'active');
assert.equal(client.state, 'recording');
await assert.rejects(client.stop(), /Synthetic lost upload response/);
assert.deepEqual(stopPhases, ['flushing', 'uploading'],
  'source upload failure is never misrepresented as finalised audio');
assert.equal(client.state, 'recovery_required');
assert.equal(attempts.length, 1);
await client.retryPendingUploads();
assert.equal(client.state, 'sealed');
assert.deepEqual(client.uploadedParts, { clinician: 1, patient: 1 });
assert.equal(durable.size, 0);
assert.deepEqual(attempts.map(([channel]) => channel), ['clinician','clinician','patient']);
assert.equal(attempts[0][1], attempts[1][1]);
assert.equal(history.indexOf('journal-committed') < history.indexOf('audio-window-upload'), true);
assert.notEqual(attempts[1][1], attempts[2][1]);
assert.equal(history.indexOf('capture-stop') < history.indexOf('stopped-on-server'), true);
assert.equal(history.indexOf('sealed-on-server') > history.indexOf('stopped-on-server'), true);
const queued = await client.transcribe();
assert.equal(client.state, 'transcribing');
assert.deepEqual(queued.ingestion_job_ids, ['clinician-job','patient-job']);
""",
        encoding="utf-8",
    )
    subprocess.run(
        ["node", str(tmp_path / "test.mjs")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_stop_and_audio_retry_serialize_schema_valid_interruption_metadata(tmp_path):
    source = (ROOT / "app/static/js/transcribe/telehealthRecordingClient.js").read_text(
        encoding="utf-8"
    )
    source = source.replace(
        "./telehealthCapture.js?v=20261106-window-vad", "./fake.mjs"
    ).replace(
        "./telehealthWindows.js?v=20261104-telehealth-windows", "./fake.mjs"
    ).replace(
        "./telehealthWav.js?v=20261105-telehealth-wav", "./fake.mjs"
    ).replace(
        "./telehealthRecoveryJournal.js?v=20261105-cross-tab-journal", "./journal.mjs"
    )
    (tmp_path / "journal.mjs").write_text(
        (ROOT / "app/static/js/transcribe/telehealthRecoveryJournal.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "client.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "fake.mjs").write_text(r"""
const f = () => globalThis.__fixture;
export function createTelehealthCapture(options) {
  const capture = {
    start: async () => ({ sampleRate: 48000 }),
    stop: async () => {},
  };
  f().capture = { interrupt: (channel, reason) => options.onInterrupted({ channel, reason }) };
  return capture;
}
export function createTelehealthWindows({ onWindow }) {
  let pending = [];
  const chunk = { channel:'clinician', startFrame:0, endFrame:480,
    samples:new Float32Array(480), sampleRate:48000 };
  return {
    async finish() {
      pending = [chunk];
      await onWindow(pending[0]);
      pending.shift();
    },
    async retryPending() {
      while (pending.length) { await onWindow(pending[0]); pending.shift(); }
    },
    status() { return { finished:true }; },
  };
}
export function encodeTelehealthWavWindow(window) {
  return { bytes:new Uint8Array([82,73,70,70]), spans:[{
    file_start_sample:0,file_end_sample:480,
    timeline_start_frame:window.startFrame,timeline_end_frame:window.endFrame,
  }] };
}
""", encoding="utf-8")
    (tmp_path / "test.mjs").write_text(r"""
import assert from 'node:assert/strict';
import { createTelehealthRecordingClient } from './client.mjs';
Object.defineProperty(globalThis, 'crypto', { configurable:true, value:{
  randomUUID:(()=>{let n=0;return()=>`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`;})(),
} });
const schemaFields = ['failure_code','interrupted_channel'];
const allStopBodies = [];
async function scenario(channel, reason, expected) {
  globalThis.__fixture = { stopBodies:[], audioAttempts:0 };
  const fixture = globalThis.__fixture;
  const fetcher = async (url, options={}) => {
    if (url.endsWith('/telehealth/captures')) return {ok:true,json:async()=>({capture_session_id:'session-uuid',status:'active'})};
    if (url.endsWith('/stop')) {
      fixture.stopBodies.push(JSON.parse(options.body));
      allStopBodies.push(JSON.parse(options.body));
      return {ok:true,json:async()=>({status:'stopping'})};
    }
    if (url.endsWith('/audio-parts')) {
      fixture.audioAttempts += 1;
      if (fixture.audioAttempts === 1) return {ok:false,json:async()=>({error:{message:'Synthetic upload failure'}})};
      return {ok:true,json:async()=>({capture_session_id:'session-uuid',part:{channel:'clinician',sequence_no:0}})};
    }
    if (url.endsWith('/seal')) return {ok:true,json:async()=>({status:'interrupted'})};
    throw new Error('Unexpected route '+url);
  };
  const journal = {
    async check(){}, async persist(){}, async remove(){}, async clear(){},
  };
  const client = createTelehealthRecordingClient({
    transcriptId:'transcript-uuid',csrfFetch:fetcher,recoveryJournal:journal,
    pacedUpload:async()=>{},
  });
  await client.start();
  fixture.capture.interrupt(channel,reason);
  await assert.rejects(client.stop(),/Synthetic upload failure/);
  await client.retryPendingUploads();
  assert.equal(client.state,'incomplete');
  assert.deepEqual(fixture.stopBodies,[expected,expected]);
  for (const body of fixture.stopBodies) {
    assert.deepEqual(Object.keys(body).sort(),schemaFields);
    if (body.interrupted_channel !== null) assert.ok(['clinician','patient'].includes(body.interrupted_channel));
    assert.ok(['source_ended','audio_clock_interrupted'].includes(body.failure_code));
  }
}
await scenario('patient','ended',{interrupted_channel:'patient',failure_code:'source_ended'});
await scenario(null,'audio_clock_interrupted',{interrupted_channel:null,failure_code:'audio_clock_interrupted'});
console.log(JSON.stringify(allStopBodies));
""", encoding="utf-8")
    result = subprocess.run(
        ["node", str(tmp_path / "test.mjs")], cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    from app.schemas.telehealth import TelehealthCaptureStop
    for payload in json.loads(result.stdout.splitlines()[-1]):
        TelehealthCaptureStop.model_validate(payload)
