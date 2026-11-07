"""Node tests for AES-GCM IndexedDB crash journal and server-sequence replay.

No patient content, browser profile, live ASR or real API calls. Uses small
synthetic WAV-shaped byte arrays and a deterministic transactional store.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js required")
def test_encrypted_crash_journal_recovery_identity_limits_and_cleanup(tmp_path):
    (tmp_path / "journal.mjs").write_text(
        (ROOT / "app/static/js/transcribe/telehealthRecoveryJournal.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "test.mjs").write_text(r"""
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import {
  createTelehealthRecoveryJournal, createTelehealthUploadPacer,
  replayTelehealthRecoveryWindows,
} from './journal.mjs';

class FakeStore {
  constructor() {
    this.tables = { pending_windows: new Map(), capture_keys: new Map() };
    this.failWrite = false;
  }
  async get(table, id) { return this.tables[table].get(id); }
  async list(table) { return [...this.tables[table].values()]; }
  async put(table, row, { onlyIfMissing = false } = {}) {
    if (this.failWrite) throw new Error('Synthetic disk failure');
    if (onlyIfMissing && this.tables[table].has(row.id)) throw new Error('duplicate ID');
    this.tables[table].set(row.id, row);
  }
  async remove(table, id) { this.tables[table].delete(id); }
}
class FakeLocks {
  constructor() { this.tail = Promise.resolve(); }
  request(name, options, callback) {
    assert.equal(name, 'openscribe-telehealth-recovery-v1');
    assert.equal(options.mode, 'exclusive');
    const prior = this.tail;
    let unlock;
    const gate = new Promise((resolve) => { unlock = resolve; });
    this.tail = prior.then(() => gate);
    return prior.then(async () => {
      try { return await callback({ name, mode: options.mode }); }
      finally { unlock(); }
    });
  }
}
const locks = new FakeLocks();
const C = '11111111-1111-4111-8111-111111111111';
const T = '22222222-2222-4222-8222-222222222222';
const K = '33333333-3333-4333-8333-333333333333';
const otherK = '44444444-4444-4444-8444-444444444444';
const store = new FakeStore();
let now = 1720000000000;
const journal = createTelehealthRecoveryJournal({
  store, cryptography: webcrypto, now: () => now, locks,
});
await journal.check();
assert.equal(store.tables.capture_keys.size, 0, 'CryptoKey test leaves no key behind');
const part = (channel, sequence, key, offset = 0) => ({
  captureId: C, transcriptId: T, channel, sequence, key,
  spans: [{file_start_sample: 0, file_end_sample: 4,
    timeline_start_frame: offset, timeline_end_frame: offset + 12}],
  bytes: Uint8Array.from([82, 73, 70, 70, ...new Array(48).fill(73)]),
});
const clinician = part('clinician', 0, K);
const patient = part('patient', 0, otherK);
await journal.persist(clinician);
await journal.persist(patient);
const key = store.tables.capture_keys.get(C).key;
assert.equal(key.extractable, false);
const encrypted = [...store.tables.pending_windows.values()];
assert.equal(encrypted.length, 2);
assert.equal('bytes' in encrypted[0], false, 'plain audio must never be written');
assert.equal(encrypted[0].ciphertext.byteLength > clinician.bytes.length, true);
assert.equal((await journal.list(C, T)).length, 2);
assert.deepEqual(await journal.list(C, '99999999-9999-4999-8999-999999999999'), []);
await assert.rejects(
  journal.persist({ ...clinician, key: otherK }), /Conflicting/,
);
const alteredBytes = clinician.bytes.slice();
alteredBytes[alteredBytes.length-1] ^= 1;
await assert.rejects(
  journal.persist({...clinician, bytes: alteredBytes}),
  /Conflicting Telehealth recovery window audio/,
);
const persisted = createTelehealthRecoveryJournal({store, cryptography:webcrypto, now:()=>now, locks});
assert.deepEqual((await persisted.list(C,T)).map((v)=>v.channel), ['clinician', 'patient']);

// Separate tabs share the origin lock across CryptoKey creation/encryption and
// last-window key deletion. Pause the new write after key lookup; removing the
// old final window must wait until the new ciphertext commits.
const raceC = '55555555-5555-4555-8555-555555555555';
const racePart = (channel, sequence, offset) => ({
  ...part(channel, sequence, K, offset), captureId: raceC,
});
await journal.persist(racePart('clinician', 0, 0));
let enteredEncrypt;
const encryptEntered = new Promise((resolve) => { enteredEncrypt = resolve; });
let releaseEncrypt;
const encryptionGate = new Promise((resolve) => { releaseEncrypt = resolve; });
let delayed = true;
const slowCrypto = {
  subtle: {
    generateKey: (...args) => webcrypto.subtle.generateKey(...args),
    decrypt: (...args) => webcrypto.subtle.decrypt(...args),
    encrypt: async (...args) => {
      if (delayed) {
        delayed = false;
        enteredEncrypt();
        await encryptionGate;
      }
      return webcrypto.subtle.encrypt(...args);
    },
  },
  getRandomValues: (bytes) => webcrypto.getRandomValues(bytes),
  randomUUID: () => webcrypto.randomUUID(),
};
const writer = createTelehealthRecoveryJournal({store,cryptography:slowCrypto,now:()=>now,locks});
const remover = createTelehealthRecoveryJournal({store,cryptography:webcrypto,now:()=>now,locks});
const writing = writer.persist(racePart('patient', 0, 12));
await encryptEntered;
const removing = remover.remove(racePart('clinician', 0, 0));
await Promise.resolve();
assert.equal(store.tables.capture_keys.has(raceC), true,
  'last-window deletion must wait while another tab encrypts');
releaseEncrypt();
await Promise.all([writing, removing]);
assert.deepEqual((await remover.list(raceC,T)).map((v)=>v.channel), ['patient'],
  'the window committed during the race remains decryptable');

// A concurrent clear/prune-style read-modify-delete and another persist must
// leave either no key/window or a decryptable surviving window.
await Promise.all([
  remover.clear(raceC,T),
  writer.persist(racePart('clinician', 1, 24)),
]);
const afterClearRace = await remover.list(raceC,T);
assert.equal(store.tables.capture_keys.has(raceC), afterClearRace.length > 0);

// Concurrent first writes for one capture share the key created by the winner.
const creationC = '66666666-6666-4666-8666-666666666666';
const creationPart = (channel) => ({ ...part(channel,0,K), captureId:creationC });
const creatorA = createTelehealthRecoveryJournal({store,cryptography:webcrypto,now:()=>now,locks});
const creatorB = createTelehealthRecoveryJournal({store,cryptography:webcrypto,now:()=>now,locks});
await Promise.all([
  creatorA.persist(creationPart('clinician')),
  creatorB.persist(creationPart('patient')),
]);
assert.deepEqual((await creatorA.list(creationC,T)).map((v)=>v.channel), ['clinician','patient']);
await remover.clear(raceC,T);
await creatorA.clear(creationC,T);

const receipt = {
  capture_session_id: C, transcript_id: T, status: 'stopping',
  parts: [{
    channel:'clinician', sequence_no:0, status:'staged',
    timeline_start_frame:0, timeline_end_frame:12,
  }],
};
const attempted = [];
let fail = true;
const fetcher = async (url, opts) => {
  const submitted = {
    url, channel: opts.body.get('channel'),
    sequence: Number(opts.body.get('sequence_no')),
    identity: opts.headers['Idempotency-Key'],
  };
  attempted.push(submitted);
  if (fail) {
    fail = false;
    return {ok:false, json:async()=>({error:{message:'Synthetic network disconnect'}})};
  }
  return {ok:true, json:async()=>({
    capture_session_id:C,
    part:{channel:submitted.channel, sequence_no:submitted.sequence},
  })};
};
await assert.rejects(replayTelehealthRecoveryWindows({
  journal:persisted, capture:receipt, csrfFetch:fetcher, pace:async()=>{},
}), /Synthetic network disconnect/);
// Already accepted clinician locally is cleared. Patient remains encrypted with
// its exact stable identity, ready for another page load.
assert.equal(store.tables.pending_windows.size, 1);
assert.equal((await persisted.list(C,T))[0].key, otherK);
const onReload = createTelehealthRecoveryJournal({store,cryptography:webcrypto,now:()=>now,locks});
assert.deepEqual(await replayTelehealthRecoveryWindows({
  journal:onReload, capture:receipt, csrfFetch:fetcher, pace:async()=>{},
}), {clinician:0,patient:1});
assert.equal(attempted.length, 2);
assert.equal(attempted[0].identity, attempted[1].identity);
assert.equal(attempted[0].identity, otherK);
assert.equal(store.tables.pending_windows.size, 0);
assert.equal(store.tables.capture_keys.size, 0, 'remove the encryption key on successful ack');

const conflict = part('patient', 0, otherK);
await journal.persist(conflict);
await assert.rejects(replayTelehealthRecoveryWindows({
  journal, capture:{
    ...receipt, parts:[{
      channel:'patient', sequence_no:0, status:'staged',
      timeline_start_frame:1000, timeline_end_frame:1012,
    }],
  }, csrfFetch:fetcher, pace:async()=>{},
}), /conflicts with the server receipt/);
assert.equal((await journal.list(C,T)).length, 1);
await journal.clear(C,T);
assert.equal(store.tables.pending_windows.size, 0);

await journal.persist(patient);
const raw = store.tables.pending_windows.get(C+':patient:0');
new Uint8Array(raw.ciphertext)[0] ^= 8;
await assert.rejects(journal.list(C,T), /failed authentication/);
await journal.clear(C,T);

// Persisting a ninth unaccepted 10-second window must fail and NOT replace
// any of the eight encrypted source windows already stored.
for (let i=0; i<8; i++) {
  await journal.persist(part('clinician',i,
    '55555555-5555-4555-8555-' + String(i).padStart(12,'0'),i*12));
}
await assert.rejects(journal.persist(part(
  'clinician',8,'66666666-6666-4666-8666-666666666666',96,
)), /buffer is full/);
assert.equal(store.tables.pending_windows.size,8);
now += 6*60*60*1000 + 1;
assert.deepEqual(await journal.list(C,T), []);
assert.equal(store.tables.pending_windows.size,0);
assert.equal(store.tables.capture_keys.size,0);

// A browser storage error MUST fail closed before a window can be uploaded.
store.failWrite = true;
await assert.rejects(journal.persist(clinician), /Could not persist the Telehealth recovery key/);
store.failWrite = false;
await persisted.persist(part('patient',0,otherK,123)); // Rejection released the shared lock.

const waits=[];
let timer=1000;
const pace=createTelehealthUploadPacer({
  clock:()=>timer, pause:async(ms)=>{ waits.push(ms); timer+=ms; },
});
await pace();
await pace();
assert.deepEqual(waits,[1100]);

const unavailable = createTelehealthRecoveryJournal({
  store, cryptography:{}, now:()=>now, locks,
});
await assert.rejects(unavailable.check(),/requires WebCrypto/);

const unsupportedLocks = createTelehealthRecoveryJournal({
  store, cryptography:webcrypto, now:()=>now, locks:null,
});
await assert.rejects(unsupportedLocks.check(), /cross-tab Web Locks support/);
console.log('Encrypted Telehealth crash journal: passed');
""", encoding="utf-8")
    result = subprocess.run(
        [NODE, str(tmp_path / "test.mjs")], cwd=tmp_path,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
