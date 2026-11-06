// Ephemeral, content-free browser capture diagnostics. No console or server logging.
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

export function createTelehealthDiagnostics({ now = () => new Date().toISOString() } = {}) {
  let records = [];
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
  };
  return {
    emit,
    clear: () => { records = []; },
    snapshot: () => ({
      schema: 'openscribe-telehealth-diagnostics-v1',
      privacy: 'Event metadata only; no clinical content or audio.',
      events: records.map((item) => ({ ...item })),
    }),
    get length() { return records.length; },
  };
}
