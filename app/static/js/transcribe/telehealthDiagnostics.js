// Ephemeral, content-free diagnostics forwarded only to a private server file.
// Closed schemas prevent error messages, window titles, URLs or audio reaching reports.
const EVENTS = new Set([
  'picker_requested', 'picker_selected', 'picker_rejected', 'window_acknowledged',
  'microphone_requested', 'microphone_ready', 'capture_started', 'capture_stopping',
  'capture_stopped', 'capture_failed', 'capture_state', 'capture_interrupted',
  'track_ended', 'track_muted', 'source_changed', 'worklet_ready',
  'vad_loading', 'vad_ready', 'vad_unavailable', 'vad_speech_confirmed',
  'journal_check', 'journal_failed', 'window_prepared', 'upload_started',
  'upload_accepted', 'upload_failed', 'seal_started', 'seal_completed',
  'seal_failed', 'transcribe_started', 'transcribe_accepted', 'transcribe_failed',
]);
const FIELDS = {
  channel: new Set(['patient', 'clinician', 'none']),
  surface: new Set(['browser', 'window', 'monitor', 'unknown']),
  audio_scope: new Set(['call', 'system']),
  state: new Set([
    'idle', 'starting', 'recording', 'running', 'stopping', 'stopped',
    'failed', 'recovery_required', 'incomplete', 'sealed', 'transcribing',
    'discarded',
  ]),
  code: new Set([
    'none', 'user_cancelled', 'permission_denied', 'window_declined',
    'unsupported_surface', 'missing_audio', 'missing_video', 'source_ended',
    'source_muted', 'audio_clock_interrupted', 'audio_backpressure',
    'audio_processor_failed', 'audio_flush_failed', 'browser_capture_failed',
    'vad_unavailable', 'vad_initialization_failed', 'journal_unavailable',
    'upload_failed', 'seal_failed', 'transcribe_failed',
  ]),
};
const MAX_EVENTS = 200;
const MAX_PART = 1000000;

// The browser keeps a bounded local view for opted-in developers and
// asynchronously forwards ONLY allowlisted metadata to the owner-scoped API.
// The server assigns timestamps and writes private rotating files. No console,
// localStorage, IndexedDB or user-triggered download is involved.
export function createTelehealthDiagnostics({
  now = () => new Date().toISOString(),
  sendBatch = null,
  generateId = () => globalThis.crypto?.randomUUID?.().replaceAll('-', '') || null,
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (id) => clearTimeout(id),
} = {}) {
  let records = [], pending = [], timer = null, inFlight = false;
  let target = null, diagnosticId = null, generation = 0;
  const MAX_PENDING = 200;
  const flush = async () => {
    if (!pending.length || inFlight || !target || !diagnosticId || !sendBatch) return;
    if (timer !== null) { cancel(timer); timer = null; }
    const current = generation;
    const destination = target;
    const id = diagnosticId;
    const batch = pending.slice(0, 20);
    inFlight = true;
    let retryDelay = 5000;
    try {
      const response = await sendBatch(destination, id, batch);
      if (current !== generation) return;
      if (response?.accepted !== batch.length) throw new Error('Diagnostic storage unavailable');
      pending.splice(0, batch.length);
    } catch (_) {
      // No raw exception messages in diagnostics, console or other storage.
      retryDelay = 15000;
    } finally {
      if (current === generation) {
        inFlight = false;
        if (pending.length) arm(retryDelay);
      }
    }
  };
  const arm = (ms = 5000) => {
    if (timer !== null || inFlight || !pending.length || !target) return;
    timer = schedule(() => { timer = null; void flush(); }, ms);
  };
  const reset = () => {
    ++generation;
    if (timer !== null) { cancel(timer); timer = null; }
    inFlight = false;
    records = [];
    pending = [];
    target = null;
    diagnosticId = null;
  };
  const begin = (transcriptId) => {
    reset();
    const id = typeof generateId === 'function' ? generateId() : null;
    if (typeof id === 'string' && /^[0-9a-f]{32}$/.test(id)
      && typeof transcriptId === 'string' && transcriptId) {
      target = transcriptId;
      diagnosticId = id;
    }
    return diagnosticId;
  };
  const emit = (event, fields = {}) => {
    if (!EVENTS.has(event)) return;
    const record = { at: now(), event };
    for (const [name, allowed] of Object.entries(FIELDS)) {
      if (allowed.has(fields?.[name])) record[name] = fields[name];
    }
    if (Number.isSafeInteger(fields?.part) && fields.part >= 0 && fields.part <= MAX_PART) {
      record.part = fields.part;
    }
    records.push(record);
    if (records.length > MAX_EVENTS) records.shift();
    if (target && sendBatch) {
      const { at, ...safeFields } = record;
      pending.push(safeFields);
      if (pending.length > MAX_PENDING) pending.shift();
      arm();
    }
  };
  return {
    emit, begin, flush, clear: reset,
    snapshot: () => ({
      schema: 'openscribe-telehealth-diagnostics-v1',
      privacy: 'Fixed event metadata only; no clinical content or audio.',
      events: records.map((item) => ({ ...item })),
    }),
    get length() { return records.length; },
    get diagnosticId() { return diagnosticId; },
  };
}
