// Crash recovery for unacknowledged Telehealth WAV windows.
// The browser profile holds an AES-GCM non-extractable CryptoKey in IndexedDB;
// no plaintext audio, patient identity, API token or clinical text is stored.
// Same-origin code and an unlocked browser profile can still decrypt this data.
// The journal is a bounded LAST-RESORT recovery buffer, not a clinical archive.
const DB_NAME = 'openscribe-telehealth-recovery-v1';
const DB_VERSION = 1;
const WINDOWS = 'pending_windows';
const KEYS = 'capture_keys';
const STORAGE_LOCK = 'openscribe-telehealth-recovery-v1';
const CHANNELS = ['clinician', 'patient'];
const MAX_WINDOWS = 8;
const MAX_AUDIO_BYTES = 1024 * 1024;
const MAX_RECOVERY_AGE_MS = 6 * 60 * 60 * 1000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const requestDone = (request) => new Promise((resolve, reject) => {
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error || new Error('Local audio store request failed.'));
});
const transactionDone = (transaction) => new Promise((resolve, reject) => {
  transaction.oncomplete = () => resolve();
  transaction.onabort = () => reject(transaction.error || new Error('Local audio store transaction aborted.'));
  transaction.onerror = () => reject(transaction.error || new Error('Local audio store transaction failed.'));
});

export function createIndexedDbTelehealthStore({ indexedDB = globalThis.indexedDB } = {}) {
  let connection = null;
  const open = async () => {
    if (!indexedDB) throw new Error('Encrypted Telehealth crash recovery requires IndexedDB.');
    if (!connection) {
      connection = new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(WINDOWS)) db.createObjectStore(WINDOWS, { keyPath: 'id' });
          if (!db.objectStoreNames.contains(KEYS)) db.createObjectStore(KEYS, { keyPath: 'id' });
        };
        request.onsuccess = () => {
          const db = request.result;
          db.onversionchange = () => db.close();
          resolve(db);
        };
        request.onerror = () => reject(request.error || new Error('Cannot open encrypted recovery store.'));
        request.onblocked = () => reject(new Error('Another tab is blocking the recovery store.'));
      }).catch((error) => { connection = null; throw error; });
    }
    return connection;
  };
  const get = async (store, id) => {
    const db = await open();
    return requestDone(db.transaction(store, 'readonly').objectStore(store).get(id));
  };
  const list = async (store) => {
    const db = await open();
    return requestDone(db.transaction(store, 'readonly').objectStore(store).getAll());
  };
  const put = async (store, row, { onlyIfMissing = false } = {}) => {
    const db = await open();
    // Request the strictest available disk-commit semantics. Browsers may
    // still lose the final transaction on whole-device failure, hence the
    // mandatory incomplete-recording warning after a crash.
    const tx = db.transaction(store, 'readwrite', { durability: 'strict' });
    const finished = transactionDone(tx);
    tx.objectStore(store)[onlyIfMissing ? 'add' : 'put'](row);
    await finished; // Never claim persistence until the IndexedDB COMMIT.
  };
  const remove = async (store, id) => {
    const db = await open();
    // Request the strictest available disk-commit semantics. Browsers may
    // still lose the final transaction on whole-device failure, hence the
    // mandatory incomplete-recording warning after a crash.
    const tx = db.transaction(store, 'readwrite', { durability: 'strict' });
    const finished = transactionDone(tx);
    tx.objectStore(store).delete(id);
    await finished;
  };
  return { get, list, put, remove };
}

export function createTelehealthRecoveryJournal({
  store = createIndexedDbTelehealthStore(),
  cryptography = globalThis.crypto,
  now = () => Date.now(),
  locks = globalThis.navigator?.locks,
} = {}) {
  // The status-only recovery interface should still mount on unsupported
  // browsers; the recording START itself will fail closed in check().
  const subtle = cryptography?.subtle;
  let action = Promise.resolve();
  // Serialise per-tab mutations so a key cannot be deleted while its window
  // is being written. IndexedDB commits still form the durable boundary.
  const serial = (fn) => {
    const result = action.catch(() => {}).then(fn);
    action = result.catch(() => {});
    return result;
  };
  const coordinated = (fn) => {
    if (!locks || typeof locks.request !== 'function') {
      throw new Error('Encrypted Telehealth recovery requires cross-tab Web Locks support.');
    }
    return locks.request(STORAGE_LOCK, { mode: 'exclusive' }, fn);
  };
  const run = (fn) => serial(() => coordinated(fn));
  const partId = (captureId, channel, sequence) => captureId + ':' + channel + ':' + sequence;
  const keyFor = async (captureId, create = false) => {
    const existing = await store.get(KEYS, captureId);
    if (existing?.key) return existing.key;
    if (!create) throw new Error('The encrypted Telehealth recovery key is no longer available.');
    const key = await subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    try {
      await store.put(KEYS, { id: captureId, key, createdAt: now() }, { onlyIfMissing: true });
      return key;
    } catch (_) {
      // Two tabs creating the same session key must use the winner's key.
      const concurrent = await store.get(KEYS, captureId);
      if (concurrent?.key) return concurrent.key;
      throw new Error('Could not persist the Telehealth recovery key.');
    }
  };
  const prune = async () => {
    const entries = await store.list(WINDOWS);
    const current = now();
    const expired = entries.filter((entry) => !Number.isFinite(entry.createdAt)
      || entry.createdAt > current + 60000
      || current - entry.createdAt > MAX_RECOVERY_AGE_MS);
    for (const entry of expired) await store.remove(WINDOWS, entry.id);
    // Key cleanup can follow window cleanup without deleting any live window.
    const remaining = entries.filter((entry) => !expired.includes(entry));
    const keys = await store.list(KEYS);
    const liveCaptures = new Set(remaining.map((entry) => entry.captureId));
    for (const item of keys) {
      // A different open tab may have created a key but not yet committed
      // its first encrypted window. Do not race and delete that live key.
      if (!liveCaptures.has(item.id) && (
        !Number.isFinite(item.createdAt)
        || current - item.createdAt > MAX_RECOVERY_AGE_MS
      )) await store.remove(KEYS, item.id);
    }
  };
  const persist = (part) => run(async () => {
    const { captureId, transcriptId, channel, sequence, key, bytes, spans } = part || {};
    if (!UUID.test(captureId) || !UUID.test(transcriptId) || !UUID.test(key)
      || !CHANNELS.includes(channel) || !Number.isSafeInteger(sequence)
      || sequence < 0 || sequence > 100000
      || !(bytes instanceof Uint8Array) || bytes.length < 46
      || bytes.length > MAX_AUDIO_BYTES || !Array.isArray(spans)
      || spans.length !== 1) {
      throw new Error('Invalid Telehealth local recovery window.');
    }
    if (!subtle || typeof cryptography?.getRandomValues !== 'function') {
      throw new Error('Encrypted browser recovery is unavailable.');
    }
    await prune();
    const id = partId(captureId, channel, sequence);
    const prior = await store.get(WINDOWS, id);
    if (prior) {
      if (prior.key !== key || prior.transcriptId !== transcriptId
        || JSON.stringify(prior.spans) !== JSON.stringify(spans)) {
        throw new Error('Conflicting Telehealth recovery window identity.');
      }
      // The same idempotency key may NEVER be reused with different bytes,
      // even if the original HTTP acceptance response was lost.
      const priorKey = await keyFor(captureId);
      let original;
      try {
        original = new Uint8Array(await subtle.decrypt(
          { name: 'AES-GCM', iv: prior.iv }, priorKey, prior.ciphertext,
        ));
      } catch (_) {
        throw new Error('Encrypted Telehealth recovery data failed authentication.');
      }
      let equal = original.length === bytes.length;
      if (equal) {
        for (let i = 0; i < original.length; i++) {
          if (original[i] !== bytes[i]) { equal = false; break; }
        }
      }
      original.fill(0);
      if (!equal) throw new Error('Conflicting Telehealth recovery window audio.');
      return;
    }
    const total = (await store.list(WINDOWS)).length;
    if (total >= MAX_WINDOWS) {
      throw new Error('Encrypted Telehealth recovery buffer is full. Stop and resolve this recording.');
    }
    const aesKey = await keyFor(captureId, true);
    const iv = cryptography.getRandomValues(new Uint8Array(12));
    const plain = bytes.slice();
    let ciphertext;
    try {
      ciphertext = await subtle.encrypt({ name: 'AES-GCM', iv }, aesKey, plain);
    } finally {
      plain.fill(0);
    }
    await store.put(WINDOWS, {
      id, captureId, transcriptId, channel, sequence, key,
      spans: structuredClone(spans), iv, ciphertext, createdAt: now(),
    }, { onlyIfMissing: true });
  });
  const list = (captureId, transcriptId) => run(async () => {
    if (!UUID.test(captureId) || !UUID.test(transcriptId)) {
      throw new Error('Invalid Telehealth recovery identity.');
    }
    await prune();
    const entries = (await store.list(WINDOWS)).filter(
      (entry) => entry.captureId === captureId && entry.transcriptId === transcriptId,
    ).sort((a, b) => a.channel.localeCompare(b.channel) || a.sequence - b.sequence);
    if (!entries.length) return [];
    if (!subtle) throw new Error('Browser encryption is unavailable for saved audio.');
    const aesKey = await keyFor(captureId);
    const recovered = [];
    try {
      for (const entry of entries) {
        let plaintext;
        try {
          plaintext = await subtle.decrypt(
            { name: 'AES-GCM', iv: entry.iv }, aesKey, entry.ciphertext,
          );
        } catch (_) {
          throw new Error('Encrypted Telehealth recovery data failed authentication.');
        }
        recovered.push({
          captureId, transcriptId, channel: entry.channel, sequence: entry.sequence,
          key: entry.key, spans: entry.spans, bytes: new Uint8Array(plaintext),
        });
      }
      return recovered;
    } catch (error) {
      for (const entry of recovered) entry.bytes.fill(0);
      throw error;
    }
  });
  const remove = (part) => run(async () => {
    const id = partId(part.captureId, part.channel, part.sequence);
    const existing = await store.get(WINDOWS, id);
    if (existing && (existing.transcriptId !== part.transcriptId || existing.key !== part.key)) {
      throw new Error('Cannot clear a different Telehealth recovery window.');
    }
    await store.remove(WINDOWS, id);
    const remaining = (await store.list(WINDOWS)).some((entry) => entry.captureId === part.captureId);
    if (!remaining) await store.remove(KEYS, part.captureId);
  });
  const clear = (captureId, transcriptId) => run(async () => {
    if (!UUID.test(captureId) || !UUID.test(transcriptId)) throw new Error('Invalid Telehealth identity.');
    const entries = (await store.list(WINDOWS)).filter((entry) =>
      entry.captureId === captureId && entry.transcriptId === transcriptId);
    for (const entry of entries) await store.remove(WINDOWS, entry.id);
    const remaining = (await store.list(WINDOWS)).some((entry) => entry.captureId === captureId);
    if (!remaining) await store.remove(KEYS, captureId);
  });
  const check = () => run(async () => {
    if (!subtle || typeof cryptography?.getRandomValues !== 'function') {
      throw new Error('Encrypted Telehealth recovery requires WebCrypto on HTTPS or localhost.');
    }
    // Opening the recovery store is also its expiry boundary. In particular,
    // the startup health check must not leave six-hour-old audio on disk until
    // a later upload or explicit recovery happens to list/persist a window.
    await prune();
    await store.list(WINDOWS); // Fail BEFORE server capture if storage is blocked.
    const key = await subtle.generateKey(
      { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
    );
    // Some private browsing modes allow a database to open but cannot clone
    // non-extractable CryptoKeys. Test that exact storage contract up front.
    const probeId = 'healthcheck-' + cryptography.randomUUID();
    try {
      await store.put(KEYS, { id: probeId, key }, { onlyIfMissing: true });
      const retrieved = await store.get(KEYS, probeId);
      if (!retrieved?.key || retrieved.key.extractable !== false) {
        throw new Error('Browser cannot persist encrypted Telehealth recovery data.');
      }
    } finally {
      await store.remove(KEYS, probeId);
    }
  });
  return { check, persist, list, remove, clear };
}

// Shared paced upload for live capture and crash replay. Existing route has a
// 1-request/second limiter; two simultaneous source windows must not race it.
export function createTelehealthUploadPacer({ pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  clock = () => Date.now(), intervalMs = 1100 } = {}) {
  let last = null;
  return async () => {
    const current = clock();
    if (last !== null && current - last < intervalMs) await pause(intervalMs - (current - last));
    last = clock();
  };
}

export async function submitTelehealthRecoveryWindow({
  part, csrfFetch, pace = async () => {},
} = {}) {
  if (typeof csrfFetch !== 'function' || !part?.captureId || !part?.transcriptId
    || !CHANNELS.includes(part.channel)) throw new Error('Invalid recovery submission.');
  await pace();
  const data = new FormData();
  data.append('channel', part.channel);
  data.append('sequence_no', String(part.sequence));
  data.append('spans_json', JSON.stringify(part.spans));
  data.append('audio', new Blob([part.bytes], { type: 'audio/wav' }),
    'telehealth-' + part.channel + '-' + part.sequence + '.wav');
  const endpoint = '/api/v1/transcripts/' + encodeURIComponent(part.transcriptId)
    + '/telehealth/captures/' + encodeURIComponent(part.captureId) + '/audio-parts';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  let response;
  try {
    response = await csrfFetch(endpoint, {
      method: 'POST', credentials: 'include',
      headers: { 'Idempotency-Key': part.key }, body: data,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('Telehealth upload timed out. The encrypted audio is retained for exact replay.');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    let reason = 'Could not securely store a Telehealth audio part.';
    try { reason = (await response.json())?.error?.message || reason; } catch (_) {}
    throw new Error(reason);
  }
  const accepted = await response.json();
  if (accepted?.capture_session_id !== part.captureId
    || accepted?.part?.channel !== part.channel
    || accepted?.part?.sequence_no !== part.sequence) {
    throw new Error('Telehealth audio acceptance identity did not match the submitted window.');
  }
  return accepted;
}

// Recovery is always incomplete after a crash. This function guarantees
// replay identity and order, NOT that the browser recorded the last frames.
export async function replayTelehealthRecoveryWindows({
  journal, capture, csrfFetch,
  pace = createTelehealthUploadPacer(),
} = {}) {
  if (!capture?.capture_session_id || !capture?.transcript_id || !journal) {
    throw new Error('Authenticated server capture metadata is required for recovery.');
  }
  const captureId = capture.capture_session_id;
  const transcriptId = capture.transcript_id;
  const entries = await journal.list(captureId, transcriptId);
  try {
    const remote = { clinician: new Map(), patient: new Map() };
    for (const part of capture.parts || []) {
      if (!CHANNELS.includes(part.channel) || !Number.isSafeInteger(part.sequence_no)
        || remote[part.channel].has(part.sequence_no)) {
        throw new Error('Saved Telehealth source sequence is invalid.');
      }
      remote[part.channel].set(part.sequence_no, part);
    }
    const next = {};
    for (const channel of CHANNELS) {
      let sequence = 0;
      for (const value of [...remote[channel].keys()].sort((a, b) => a - b)) {
        if (value !== sequence++) throw new Error('Server source sequence has a gap.');
      }
      next[channel] = sequence;
    }
    const replayed = { clinician: 0, patient: 0 };
    for (const entry of entries) {
      const serverPart = remote[entry.channel].get(entry.sequence);
      if (serverPart) {
        const span = entry.spans[0];
        if (serverPart.timeline_start_frame !== span.timeline_start_frame
          || serverPart.timeline_end_frame !== span.timeline_end_frame
          || serverPart.status === 'expired') {
          throw new Error('Saved Telehealth audio conflicts with the server receipt.');
        }
        // Bounds do not prove that the server accepted these exact bytes or
        // this idempotency key. Ask the existing staging endpoint to verify
        // the original retry identity before deleting local recovery audio.
        await submitTelehealthRecoveryWindow({ part: entry, csrfFetch, pace });
        await journal.remove(entry);
      } else {
        if (!['active', 'stopping'].includes(capture.status)) {
          throw new Error('This Telehealth capture is already sealed; unaccepted local audio cannot be attached.');
        }
        if (entry.sequence !== next[entry.channel]) {
          throw new Error('A locally retained audio window is missing its preceding part.');
        }
        await submitTelehealthRecoveryWindow({ part: entry, csrfFetch, pace });
        await journal.remove(entry);
        next[entry.channel] += 1;
        replayed[entry.channel] += 1;
      }
    }
    return replayed;
  } finally {
    // Zero every recovered plaintext buffer, including any not sent after
    // an HTTP, sequence-validation, storage or decryption error.
    for (const entry of entries) entry.bytes.fill(0);
  }
}
