import os
from pathlib import Path
import re
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_split_review_workspace_accessibility_hooks_and_cachebusters():
    workspace = (ROOT / "app/templates/transcribe/_workspace.html").read_text()
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text()
    shell = (ROOT / "app/templates/transcribe/_shell_extras.html").read_text()
    head = (ROOT / "app/templates/transcribe/_head_assets.html").read_text()
    controller = (ROOT / "app/static/js/transcribe/splitReview.js").read_text()

    assert 'data-split-review-trigger hidden aria-haspopup="dialog"' in workspace
    assert 'data-split-review-modal\nhidden' in workspace
    assert 'role="dialog" aria-modal="true"' in workspace
    assert 'data-split-review-status' in workspace
    assert 'role="status"' in workspace
    assert 'aria-live="polite"' in workspace
    assert 'Review problems' in workspace
    assert 'data-split-review-problem-count' in workspace
    assert 'data-split-review-add' in controller
    assert 'data-split-review-create' in workspace
    split_module_version = re.search(r"splitReview\.js\?v=([^']+)", app_js)
    assert split_module_version and split_module_version.group(1).strip()
    assert 'splitReviewController?.applyWorkspaceState' in app_js
    assert 'refreshWorkspace: (nextTranscriptId) => fetchWorkspace(nextTranscriptId, {' in app_js
    assert 'guardTranscriptId: nextTranscriptId' in app_js
    shell_version = re.search(r"app\.js\?v=([^\"]+)", shell)
    css_version = re.search(r"transcribe\.css\?v=([^\"]+)", head)
    assert shell_version and shell_version.group(1).strip()
    assert css_version and css_version.group(1).strip()
    assert 'data-split-continue-one-note hidden' in workspace
    assert 'data-split-review-suggest' in workspace
    assert 'data-split-review-loading' in workspace
    assert 'data-split-review-ignored' in workspace
    assert 'data-multiple-problems-toggle' in workspace
    assert 'data-split-review-title' in controller
    assert 'data-split-review-primary' not in controller
    assert 'data-split-review-disposition' not in controller
    assert 'data-split-retry-missing hidden' in workspace
    assert 'data-split-keep-available hidden' in workspace
    assert 'id="split-partial-status"' in workspace
    assert 'data-split-operation-status role="status" aria-live="polite"' in workspace
    assert 'renderRestoredSplitOperationStatus(workspace)' in app_js
    assert "intent.analysis_id === analysis?.analysis_id" in app_js
    assert "splitOperationStatus.dataset.statusSource === 'operation'" in app_js
    assert "Preparing your problem review. This accepted request is saved" in app_js
    assert "Choose a writing assistant before suggesting problems." in app_js


def test_split_placeholder_reconciliation_uses_materialized_ids_and_preserves_other_notes(tmp_path):
    source = (ROOT / "app/static/js/transcribe/app.js").read_text()
    build_start = source.index("      const buildSplitDisplayDocuments")
    start_start = source.index("      const startSplitPlaceholderBatch", build_start)
    reconcile_start = source.index("      const reconcileSplitPlaceholderState", start_start)
    functions_end = source.index("      const splitGenerationBatchActive", reconcile_start)
    functions = textwrap.dedent(
        source[build_start:start_start]
        + source[start_start:reconcile_start]
        + source[reconcile_start:functions_end]
    )
    runner = tmp_path / "split-placeholder-deletion-runner.js"
    runner.write_text(
        f"""
const splitBatchInFlightStatuses = new Set(['generation_queued', 'generating', 'verifying']);
const splitBatchTerminalStatuses = new Set(['ready', 'partially_ready', 'completed_partial', 'failed']);
let transcriptId = 'tx-1';
let splitPlaceholderState = null;
let splitBatchFallbackState = null;
let latestSplitBatch = null;
let workspaceNoteDocuments = [];
let workspaceNoteHistoryDocuments = [];
let selectedNoteSlotKey = null;
let selectedNoteDocumentId = null;
const renderSelectedNote = () => {{}};
const syncGenerationAvailability = () => {{}};
const readActiveDraftText = () => '';
const setTab = () => {{}};
const splitDiagnosticMessage = () => 'failed';
const splitTopicsForPlaceholders = (draft) => draft.topics
  .filter((topic) => topic.disposition === 'separate_note' && topic.title.trim())
  .map((topic, index) => ({{ topicUuid: String(topic.topic_uuid), title: topic.title.trim(), isPrimary: Boolean(topic.is_primary), order: index }}));
const splitPlaceholderId = (topic) => `split-placeholder:${{transcriptId}}:${{topic.topicUuid}}`;
const splitSlotKey = (topic) => `split-slot:${{transcriptId}}:${{topic.topicUuid}}`;
const splitPlaceholderStatus = (phase) => phase === 'generation_queued'
  ? {{ status: 'queued', label: 'Queued' }}
  : splitBatchTerminalStatuses.has(phase)
    ? {{ status: 'failed', label: 'failed' }}
    : {{ status: 'processing', label: 'Processing' }};
{functions}
const topics = [
  {{ topic_uuid: 'topic-a', title: 'Alpha', is_primary: true, disposition: 'separate_note' }},
  {{ topic_uuid: 'topic-b', title: 'Beta', is_primary: false, disposition: 'separate_note' }},
];
const draft = {{ status: 'confirmed', topics }};
const alpha = {{ id: 'doc-a', title: 'Alpha', status: 'ready' }};
const historicalAlpha = {{ id: 'old-doc-a', title: 'Alpha', status: 'ready' }};
const beta = {{ id: 'doc-b', title: 'Beta', status: 'ready' }};
startSplitPlaceholderBatch({{ draft, batchId: 'batch-1', phase: 'ready', materializedDocumentIdsByTopicUuid: {{ 'topic-b': 'doc-b' }}, deletedTopicUuids: ['topic-a'] }});
let rendered = reconcileSplitPlaceholderState({{
  draft, batch: {{ batch_id: 'batch-1', status: 'ready', deleted_topic_uuids: ['topic-a'], materialized_document_ids_by_topic_uuid: {{ 'topic-b': 'doc-b' }} }}, noteDocuments: [historicalAlpha, beta],
}});
if (rendered.length !== 2 || rendered[0].id !== 'old-doc-a' || rendered[1].id !== 'doc-b') throw new Error('same-page deletion hid a same-title standalone note or recreated a failed slot');
rendered = reconcileSplitPlaceholderState({{
  draft, batch: {{ batch_id: 'batch-1', status: 'ready', deleted_topic_uuids: ['topic-a', 'topic-b'], materialized_document_ids_by_topic_uuid: {{}} }}, noteDocuments: [],
}});
if (rendered.length !== 0) throw new Error('all-deleted batch kept phantom slots');
rendered = reconcileSplitPlaceholderState({{
  draft, batch: {{ batch_id: 'batch-1', status: 'partially_ready', deleted_topic_uuids: [], materialized_document_ids_by_topic_uuid: {{ 'topic-a': 'doc-a' }} }}, noteDocuments: [alpha],
}});
if (rendered.length !== 2 || rendered[1].id !== 'split-placeholder:tx-1:topic-b' || rendered[1].status !== 'failed') {{
  throw new Error('true failed topic lost its unavailable slot');
}}
startSplitPlaceholderBatch({{ draft, batchId: 'batch-2', phase: 'generation_queued' }});
rendered = buildSplitDisplayDocuments([]);
if (rendered.length !== 2 || rendered.some((item) => item.status !== 'queued')) throw new Error('new explicit batch inherited deleted slots');

const renamedSplitAlpha = {{ id: 'split-alpha-new', title: 'Clinician renamed this note', status: 'ready' }};
const newestSplitBeta = {{ id: 'split-beta-new', title: 'Beta', status: 'ready' }};
const priorBatchAlpha = {{ id: 'prior-batch-alpha', title: 'Alpha', status: 'ready' }};
const oldStandaloneAlpha = {{ id: 'old-standalone-alpha', title: 'Alpha', status: 'ready' }};
splitPlaceholderState = null;
const restored = reconcileSplitPlaceholderState({{
  draft,
  batch: {{ batch_id: 'batch-3', status: 'ready', deleted_topic_uuids: [], materialized_document_ids_by_topic_uuid: {{ 'topic-a': 'split-alpha-new', 'topic-b': 'split-beta-new' }} }},
  noteDocuments: [newestSplitBeta, renamedSplitAlpha, priorBatchAlpha, oldStandaloneAlpha],
}});
if (restored.length !== 4 || !restored.some((item) => item.id === 'prior-batch-alpha') || !restored.some((item) => item.id === 'old-standalone-alpha')) {{
  throw new Error('reload hid a prior-batch or same-title standalone note');
}}
if (restored.find((item) => item.split_topic_uuid === 'topic-a')?.id !== 'split-alpha-new') throw new Error('renamed split note was not matched by its materialized ID');

splitPlaceholderState = null;
workspaceNoteHistoryDocuments = [oldStandaloneAlpha];
startSplitPlaceholderBatch({{ draft, batchId: null, phase: 'generation_queued' }});
const completedSamePage = reconcileSplitPlaceholderState({{
  draft,
  batch: {{ batch_id: 'batch-4', status: 'ready', deleted_topic_uuids: [], materialized_document_ids_by_topic_uuid: {{ 'topic-a': 'split-alpha-new', 'topic-b': 'split-beta-new' }} }},
  noteDocuments: [newestSplitBeta, renamedSplitAlpha, oldStandaloneAlpha],
}});
if (completedSamePage.length !== 3 || !completedSamePage.some((item) => item.id === 'old-standalone-alpha')) throw new Error('same-page completion hid a prior same-title note');
if (completedSamePage.find((item) => item.split_topic_uuid === 'topic-a')?.id !== 'split-alpha-new') throw new Error('same-page completion did not refresh materialized IDs');

splitPlaceholderState = null;
const noMapping = reconcileSplitPlaceholderState({{
  draft,
  batch: {{ batch_id: 'batch-4', status: 'ready', deleted_topic_uuids: [], materialized_document_ids_by_topic_uuid: {{}} }},
  noteDocuments: [oldStandaloneAlpha],
}});
if (!noMapping.some((item) => item.id === 'old-standalone-alpha')) throw new Error('missing exact mapping hid an existing note');
if (!noMapping.some((item) => item.split_placeholder)) throw new Error('missing exact mapping guessed a split note from its title');

startSplitPlaceholderBatch({{ draft, batchId: 'batch-5', phase: 'generation_queued', materializedDocumentIdsByTopicUuid: {{ 'topic-a': 'split-alpha-revision' }} }});
const queuedRevision = {{ id: 'split-alpha-revision', title: 'Alpha', status: 'processing' }};
rendered = buildSplitDisplayDocuments([queuedRevision]);
if (rendered.find((item) => item.split_topic_uuid === 'topic-a')?.status !== 'processing') throw new Error('mapped in-flight revision was replaced with a failed placeholder');
""",
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT)


def test_split_diagnostic_messages_are_stage_specific_and_never_render_untrusted_values(tmp_path):
    runner = tmp_path / "split-diagnostic-messages-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ splitDiagnosticMessage }} = await import('{module_uri}');
const analysisRef = '11111111-1111-4111-8111-111111111111';
const continuationRef = '22222222-2222-4222-8222-222222222222';
const analysis = splitDiagnosticMessage({{
  stage: 'analysis', errorCode: 'consultation_split_provider_failed', operationId: analysisRef, reusedAnalysis: true, canContinue: true,
}});
if (!analysis.includes('reuses this failed analysis') || !analysis.includes('Continue as one note') || !analysis.includes(analysisRef) || analysis.includes('consultation_split_provider_failed')) throw new Error('failed analysis guidance was incomplete');
const draft = splitDiagnosticMessage({{ stage: 'draft', errorCode: 'consultation_split_source_stale', operationId: analysisRef }});
if (!draft.includes('consultation changed') || !draft.includes(analysisRef)) throw new Error('draft stale guidance was incomplete');
const confirmation = splitDiagnosticMessage({{ stage: 'confirmation', errorCode: 'consultation_split_template_unavailable', operationId: continuationRef }});
if (!confirmation.includes('template is no longer available') || !confirmation.includes(continuationRef)) throw new Error('confirmation guidance was incomplete');
const continuation = splitDiagnosticMessage({{ stage: 'continuation', errorCode: 'consultation_split_template_unavailable', operationId: continuationRef }});
if (!continuation.includes('saved template is unavailable') || !continuation.includes(continuationRef)) throw new Error('continuation guidance was incomplete');
const regeneration = splitDiagnosticMessage({{ stage: 'regeneration', errorCode: 'consultation_split_batch_unavailable', operationId: continuationRef }});
if (!regeneration.includes('regeneration could not be started') || regeneration.includes('consultation_split_batch_unavailable')) throw new Error('regeneration guidance was incomplete');
const submission = splitDiagnosticMessage({{ stage: 'submission', errorCode: 'consultation_split_provider_failed' }});
if (!submission.includes('could not be started') || submission.includes('Continue as one note')) throw new Error('submission guidance suggested an unavailable recovery');
const unavailable = splitDiagnosticMessage({{ stage: 'analysis', errorCode: 'consultation_split_analysis_unavailable' }});
if (unavailable.includes('Continue as one note')) throw new Error('unavailable analysis suggested a missing continuation');
const generation = splitDiagnosticMessage({{ stage: 'generation', operationId: continuationRef }});
if (!generation.includes('couldn’t create') || !generation.includes(continuationRef)) throw new Error('terminal generation guidance was incomplete');
const staleWithoutCode = splitDiagnosticMessage({{ stage: 'analysis', analysisStatus: 'stale' }});
if (!staleWithoutCode.includes('consultation changed')) throw new Error('stale analysis status lost its controlled explanation');
const malicious = splitDiagnosticMessage({{
  stage: 'analysis', errorCode: '<img src=x onerror=alert(1)>', operationId: 'not-a-uuid', reusedAnalysis: true,
}});
if (malicious.includes('<img') || malicious.includes('not-a-uuid') || malicious.includes('Code:') || malicious.includes('Reference:')) throw new Error('untrusted diagnostic values were rendered');
""",
    )
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_multiple_problems_toggle_persists_per_consultation_and_manual_not_required_stays_in_review(tmp_path):
    runner = tmp_path / "multiple-problems-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createMultipleProblemsController, createSplitGenerateController, dispatchTemplateGeneration, isConsultationSplitCreateEnabled }} = await import('{module_uri}');
class Button {{
  constructor() {{ this.hidden = false; this.disabled = false; this.attrs = new Map(); this.handlers = new Map(); this.classList = {{ toggle: () => {{}} }}; }}
  setAttribute(key, value) {{ this.attrs.set(key, String(value)); }}
  getAttribute(key) {{ return this.attrs.get(key); }}
  addEventListener(type, handler) {{ this.handlers.set(type, handler); }}
  click() {{ this.handlers.get('click')?.(); }}
}}
let activeTranscriptId = 'tx-one';
let resolvePatch;
const pendingPatch = new Promise((resolve) => {{ resolvePatch = resolve; }});
const pending = [];
const button = new Button();
const toggle = createMultipleProblemsController({{
  button,
  getTranscriptId: () => activeTranscriptId,
  onPendingChange: (value) => pending.push(value),
  fetcher: async () => pendingPatch,
}});
toggle.applyWorkspaceState({{ capabilityAvailable: true, nextTranscriptId: 'tx-one', multipleProblems: false }});
button.click();
if (button.getAttribute('aria-pressed') !== 'true' || !button.disabled || pending.join() !== 'true') throw new Error('toggle was not optimistically pending');
toggle.applyWorkspaceState({{ capabilityAvailable: true, nextTranscriptId: 'tx-one', multipleProblems: false }});
if (button.getAttribute('aria-pressed') !== 'true') throw new Error('stale workspace payload replaced pending toggle');
activeTranscriptId = 'tx-two';
toggle.applyWorkspaceState({{ capabilityAvailable: true, nextTranscriptId: 'tx-two', multipleProblems: false }});
resolvePatch({{ ok: true, json: async () => ({{ multiple_problems: true }}) }});
await Promise.resolve(); await Promise.resolve();
if (toggle.isMarked() || toggle.isPending() || button.getAttribute('aria-pressed') !== 'false') throw new Error('stale transcript response changed the new consultation');
activeTranscriptId = 'tx-three';
const committedButton = new Button();
const committedToggle = createMultipleProblemsController({{
  button: committedButton,
  getTranscriptId: () => activeTranscriptId,
  fetcher: async () => ({{ ok: true, json: async () => ({{ multiple_problems: true }}) }}),
  onPersisted: async () => {{ throw new Error('workspace refresh unavailable'); }},
}});
committedToggle.applyWorkspaceState({{ capabilityAvailable: true, nextTranscriptId: activeTranscriptId, multipleProblems: false }});
if (!await committedToggle.toggle() || !committedToggle.isMarked()) throw new Error('successful marker save was treated as a failed refresh');
const createEnabled = isConsultationSplitCreateEnabled({{ workspaceEnabled: false, capabilityAvailable: true, multipleProblemsMarked: committedToggle.isMarked() }});
let splitStarts = 0; let ordinaryStarts = 0;
await dispatchTemplateGeneration({{
  capabilityEnabled: createEnabled,
  splitController: {{ start: async () => {{ splitStarts += 1; return true; }} }},
  transcriptId: activeTranscriptId,
  templateId: 'template-1',
  ordinary: async () => {{ ordinaryStarts += 1; return true; }},
}});
if (!createEnabled || splitStarts !== 1 || ordinaryStarts !== 0) throw new Error('persisted marker fell back to ordinary generation after refresh failure');
let operationTranscriptId = 'tx-manual';
const calls = [];
let opened = 0;
const reviewController = {{
  applyWorkspaceState: (state) => {{ if (!state.manualReview || state.draft.topics.length !== 1) throw new Error('manual review draft not passed to review controller'); }},
  open: () => {{ opened += 1; }},
}};
const manualController = createSplitGenerateController({{
  getCapabilityEnabled: () => true,
  getTranscriptId: () => operationTranscriptId,
  getTemplateId: () => 'template-1',
  createKey: () => 'key-1',
  reviewController,
  fetcher: async (url) => {{
    calls.push(url);
    if (url.endsWith('/consultation-split-intents')) return {{ ok: true, json: async () => ({{ intent_id: 'intent-1', manual_review_requested: true, analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }} }}) }};
    if (url.endsWith('/consultation-split-draft')) return {{ ok: true, json: async () => ({{ draft_id: 'draft-1' }}) }};
    throw new Error(`unexpected generation request: ${{url}}`);
  }},
  refreshWorkspace: async () => ({{ consultation_split_draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [{{ topic_uuid: 'topic-1', title: 'Synthetic issue', is_primary: true, disposition: 'separate_note', template_id: 'template-1' }}] }}, available_templates: [] }}),
}});
await manualController.start();
for (let index = 0; index < 8; index += 1) await Promise.resolve();
if (!calls.some((url) => url.endsWith('/consultation-split-draft')) || calls.some((url) => url.endsWith('/continue-as-one-note')) || opened !== 1) throw new Error('manual not-required analysis did not open review safely');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_review_serialization_preserves_uuid_and_primary_rule(tmp_path):
    runner = tmp_path / "split-review-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ normalizeSplitDraft, serializeSplitDraft, validateSplitDraft, validateSplitDraftForConfirmation }} = await import('{module_uri}');
const uuid = '00000000-0000-0000-0000-000000000001';
const draft = normalizeSplitDraft({{
  draft_id: 'draft-1', updated_at: '2026-01-01T00:00:00Z', status: 'active',
  topics: [{{ topic_uuid: uuid, title: 'Problem', order: 0, is_primary: true,
    disposition: 'include_in_primary', template_id: 'template-1' }}]
}});
if (draft.topics[0].disposition !== 'separate_note') throw new Error('primary disposition not forced');
const payload = serializeSplitDraft(draft);
if (payload.expected_updated_at !== draft.updated_at) throw new Error('missing optimistic timestamp');
if (payload.topics[0].topic_uuid !== uuid) throw new Error('topic UUID changed');
if ('order' in payload.topics[0]) throw new Error('wire payload leaked order');
const missingTemplate = normalizeSplitDraft({{
  draft_id: 'missing-template', updated_at: '2026-01-01T00:00:00Z', status: 'active',
  topics: [{{ topic_uuid: uuid, title: 'Problem', order: 0, is_primary: true,
    disposition: 'separate_note', template_id: null }}]
}});
const missingTemplateValidation = validateSplitDraft(missingTemplate);
if (missingTemplateValidation.valid || missingTemplateValidation.message !== 'Choose a template for each separate note.') {{
  throw new Error('a separate note without a template was confirmable');
}}
const oneNote = normalizeSplitDraft({{
  draft_id: 'one-note', updated_at: '2026-01-01T00:00:00Z', status: 'active',
  topics: [
    {{ topic_uuid: uuid, title: 'Primary', order: 0, is_primary: true, disposition: 'separate_note', template_id: 'template-1' }},
    {{ topic_uuid: '00000000-0000-0000-0000-000000000002', title: 'Included finding', order: 1, is_primary: false, disposition: 'include_in_primary', template_id: null }},
  ],
}});
if (!validateSplitDraft(oneNote).valid) throw new Error('a valid one-note draft was no longer saveable');
const oneNoteConfirmation = validateSplitDraftForConfirmation(oneNote);
if (oneNoteConfirmation.valid || oneNoteConfirmation.message !== 'Add another separate note to create a note split.') {{
  throw new Error('a one-note draft was confirmable');
}}
const malformed = normalizeSplitDraft({{ draft_id: 'too-many', status: 'active', topics: Array.from({{ length: 7 }}, (_, index) => ({{ topic_uuid: `topic-${{index}}`, title: `Topic ${{index}}` }})) }});
if (!malformed.malformed || malformed.status !== 'unavailable' || validateSplitDraft(malformed).valid) throw new Error('malformed draft was editable');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_analysis_restoration_poller_uses_bounded_sse_aware_workspace_refreshes(tmp_path):
    runner = tmp_path / "split-analysis-restoration-poller.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitAnalysisRestorationPoller }} = await import('{module_uri}');
const timers = [];
const setTimeoutFn = (callback, delay) => {{ const timer = {{ callback, delay, cancelled: false }}; timers.push(timer); return timer; }};
const clearTimeoutFn = (timer) => {{ timer.cancelled = true; }};
const scheduledDelay = () => [...timers].reverse().find((timer) => !timer.cancelled)?.delay;
const runNext = async () => {{
  let timer = timers.shift();
  while (timer?.cancelled) timer = timers.shift();
  if (!timer) throw new Error('missing timer');
  timer.callback();
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
}};
let state = {{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-1', status: 'queued', updated_at: 'v1' }} }};
let calls = [];
let rejectNext = false;
let resolvePending = null;
let pending = false;
const refreshWorkspace = (transcriptId) => {{
  calls.push(transcriptId);
  if (pending) return new Promise((resolve) => {{ resolvePending = resolve; }});
  if (rejectNext) {{ rejectNext = false; return Promise.reject(new Error('network')); }}
  return Promise.resolve(null);
}};
const poller = createSplitAnalysisRestorationPoller({{ getState: () => state, refreshWorkspace, setTimeoutFn, clearTimeoutFn }});
poller.updateWorkspace();
if (scheduledDelay() !== 1500) throw new Error('queued analysis did not start at 1.5 seconds');
await runNext();
if (calls.join() !== 'tx-1' || scheduledDelay() !== 3000) throw new Error('queued retry/backoff failed');
state = {{ ...state, unrelatedWorkspaceChange: true }};
poller.updateWorkspace();
if (scheduledDelay() !== 3000) throw new Error('unrelated workspace update reset backoff');
state = {{ ...state, analysis: {{ ...state.analysis, status: 'processing', updated_at: 'v2' }} }};
poller.updateWorkspace();
if (scheduledDelay() !== 1500) throw new Error('processing state did not reset backoff');
poller.setRealtimeConnected(true);
if (poller.getState().scheduled) throw new Error('open SSE did not pause polling');
poller.setRealtimeConnected(false);
if (scheduledDelay() !== 1500) throw new Error('SSE error did not resume polling');
state = {{ ...state, analysis: {{ ...state.analysis, status: 'ready' }} }};
poller.updateWorkspace();
if (poller.getState().scheduled) throw new Error('terminal analysis kept polling');
state = {{ ...state, intent: {{ intent_id: 'intent-1', manual_review_requested: true, status: 'analysis_pending', analysis_id: 'analysis-1' }} }};
poller.updateWorkspace();
if (scheduledDelay() !== 1500) throw new Error('accepted manual review stopped polling before its draft was projected');
await runNext();
if (scheduledDelay() !== 3000) throw new Error('missing manual review draft did not use bounded backoff');
state = {{ ...state, draft: {{ draft_id: 'draft-1', status: 'active', analysis_id: 'analysis-1' }} }};
poller.updateWorkspace();
if (poller.getState().scheduled) throw new Error('matching active review draft did not stop restoration polling');
state = {{ ...state, draft: null, intent: {{ ...state.intent, analysis_id: 'other-analysis' }} }};
poller.updateWorkspace();
if (poller.getState().scheduled) throw new Error('unbound manual intent kept polling another analysis');
state = {{ ...state, capabilityEnabled: false }};
poller.updateWorkspace();
if (poller.getState().scheduled) throw new Error('disabled capability kept polling');
state = {{ ...state, capabilityEnabled: true, analysis: null }};
poller.updateWorkspace();
if (poller.getState().scheduled) throw new Error('missing analysis kept polling');
state = {{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-1', status: 'queued', updated_at: 'v3' }} }};
poller.updateWorkspace(); rejectNext = true; await runNext();
if (scheduledDelay() !== 3000) throw new Error('failed refresh did not retry');
pending = true; await runNext(); poller.updateWorkspace();
if (calls.length !== 4) throw new Error('poller started a second in-flight refresh');
state = {{ capabilityEnabled: true, transcriptId: 'tx-2', analysis: {{ analysis_id: 'analysis-2', status: 'processing', updated_at: 'v4' }} }};
poller.updateWorkspace(); resolvePending(null); pending = false;
await Promise.resolve(); await Promise.resolve();
if (scheduledDelay() !== 1500) throw new Error('transcript switch did not reset polling');
await runNext();
if (calls[calls.length - 1] !== 'tx-2') throw new Error('stale transcript refresh was reused after switch');
let boundedTimers = [];
let boundedState = {{ capabilityEnabled: true, transcriptId: 'tx-3', analysis: {{ analysis_id: 'analysis-3', status: 'queued', updated_at: 'v1' }} }};
let boundedCalls = 0;
const bounded = createSplitAnalysisRestorationPoller({{
  getState: () => boundedState,
  refreshWorkspace: async () => {{ boundedCalls += 1; }},
  setTimeoutFn: (callback) => {{ const timer = {{ callback, cancelled: false }}; boundedTimers.push(timer); return timer; }},
  clearTimeoutFn: (timer) => {{ timer.cancelled = true; }}, backoffMs: [1], maxUnchangedCycles: 2,
}});
bounded.updateWorkspace();
for (let index = 0; index < 2; index += 1) {{ const timer = boundedTimers.shift(); timer.callback(); await Promise.resolve(); await Promise.resolve(); }}
if (boundedCalls !== 2 || bounded.getState().scheduled) throw new Error('unchanged analysis retry bound failed');
poller.stop();
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_workspace_fetch_coordinator_rejects_stale_joined_payload_after_transcript_switch(tmp_path):
    runner = tmp_path / "workspace-fetch-coordinator.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createWorkspaceFetchCoordinator }} = await import('{module_uri}');
let activeTranscriptId = 'old';
const resolvers = new Map();
const fetchCalls = [];
const applied = [];
const fetcher = (endpoint) => new Promise((resolve) => {{ fetchCalls.push(endpoint); resolvers.set(endpoint, resolve); }});
const responseFor = (id) => ({{ ok: true, json: async () => ({{ active_transcript: {{ id }} }}) }});
const coordinator = createWorkspaceFetchCoordinator({{
  fetcher,
  endpointForTranscript: (id) => `/workspace?transcript_id=${{id}}`,
  applyWorkspacePayload: (workspace) => applied.push(workspace.active_transcript.id),
  getActiveTranscriptId: () => activeTranscriptId,
}});
const guardedOld = coordinator.fetchWorkspace('old', {{ guardTranscriptId: 'old' }});
const unguardedOld = coordinator.fetchWorkspace('old');
await Promise.resolve();
if (fetchCalls.length !== 1) throw new Error('same endpoint was not deduplicated');
activeTranscriptId = 'new';
const newWorkspace = coordinator.fetchWorkspace('new', {{ allowTranscriptSwitch: true }});
await Promise.resolve();
resolvers.get('/workspace?transcript_id=new')(responseFor('new'));
await newWorkspace;
resolvers.get('/workspace?transcript_id=old')(responseFor('old'));
const [oldCreatorResult, oldJoinerResult] = await Promise.all([unguardedOld, guardedOld]);
if (oldCreatorResult || oldJoinerResult || applied.join() !== 'new') {{
  throw new Error('stale shared response overwrote the newly selected transcript');
}}
const switchB = coordinator.fetchWorkspace('B', {{ allowTranscriptSwitch: true }});
const switchC = coordinator.fetchWorkspace('C', {{ allowTranscriptSwitch: true }});
await Promise.resolve();
resolvers.get('/workspace?transcript_id=C')(responseFor('C'));
const switchCResult = await switchC;
resolvers.get('/workspace?transcript_id=B')(responseFor('B'));
const switchBResult = await switchB;
if (!switchCResult || switchBResult || applied.join() !== 'new,C') {{
  throw new Error('an earlier deliberate switch overwrote the later switch');
}}
const sameEndpointFirst = coordinator.fetchWorkspace('same', {{ allowTranscriptSwitch: true }});
const sameEndpointLatest = coordinator.fetchWorkspace('same', {{ allowTranscriptSwitch: true }});
await Promise.resolve();
if (fetchCalls.filter((endpoint) => endpoint === '/workspace?transcript_id=same').length !== 1) {{
  throw new Error('overlapping same-endpoint switches lost deduplication');
}}
resolvers.get('/workspace?transcript_id=same')(responseFor('same'));
const [sameFirstResult, sameLatestResult] = await Promise.all([sameEndpointFirst, sameEndpointLatest]);
if (sameFirstResult || !sameLatestResult || applied.join() !== 'new,C,same') {{
  throw new Error('latest same-endpoint switch did not win');
}}
const sessionLinkB = coordinator.fetchWorkspace('link-B', {{ allowTranscriptSwitch: true }});
activeTranscriptId = 'dictation-D';
const dictationD = coordinator.fetchWorkspace('dictation-D', {{ allowTranscriptSwitch: true }});
await Promise.resolve();
resolvers.get('/workspace?transcript_id=dictation-D')(responseFor('dictation-D'));
const dictationDResult = await dictationD;
resolvers.get('/workspace?transcript_id=link-B')(responseFor('link-B'));
const sessionLinkBResult = await sessionLinkB;
if (!dictationDResult || sessionLinkBResult || applied.join() !== 'new,C,same,dictation-D') {{
  throw new Error('dictation transition did not invalidate an earlier session switch');
}}
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_restoration_polling_does_not_change_generation_or_apply_stale_responses():
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text()
    split_review = (ROOT / "app/static/js/transcribe/splitReview.js").read_text()

    assert "createSplitAnalysisRestorationPoller" in app_js
    assert "createWorkspaceFetchCoordinator" in app_js
    assert "refreshWorkspace: (nextTranscriptId) => fetchWorkspace(nextTranscriptId" in app_js
    assert "guardTranscriptId: nextTranscriptId" in app_js
    assert "requestsByEndpoint: workspaceFetchesByEndpoint" in app_js
    assert "allowTranscriptSwitch: true" in app_js
    assert "await fetchWorkspace(transcript.id, { allowTranscriptSwitch: true });" in app_js
    assert "splitAnalysisRestorationPoller?.stop();" in app_js
    assert "`/api/v1/transcripts/${generationTranscriptId}/generate-output`" in app_js
    assert "/consultation-split-analysis" not in split_review
    assert "/consultation-split-intents" in split_review
    assert "consultation-split-draft', {\n        method: 'POST'" not in split_review


def test_split_generate_controller_preserves_ordinary_flow_and_coordinates_browser_intent(tmp_path):
    runner = tmp_path / "split-generate-controller-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
let enabled = false, transcriptId = 'tx-1', templateId = 'template-1';
let saves = [], calls = [], statuses = [], busy = [], refreshes = [], opens = 0, applies = 0, keyNo = 0, controller;
let draftResponse = null, intentResponse = {{ status: 202, ok: true, json: async () => ({{ intent_id: 'intent-1', analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}) }};
const fetcher = async (url, options) => {{ calls.push([url, options?.method]); if (url.includes('intents')) return intentResponse; return draftResponse; }};
const reviewController = {{ applyWorkspaceState: () => {{ applies += 1; }}, open: () => {{ opens += 1; }} }};
controller = createSplitGenerateController({{
  fetcher, getCapabilityEnabled: () => enabled, getTranscriptId: () => transcriptId, getTemplateId: () => templateId,
  saveSources: async ({{ transcriptId: sourceTranscriptId }} = {{}}) => {{ saves.push(`working:${{sourceTranscriptId}}`); saves.push(`dictation:${{sourceTranscriptId}}`); }}, refreshWorkspace: async (...args) => {{ refreshes.push(args); controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}); return null; }},
  reviewController, setBusy: (value) => busy.push(value), setStatus: (message) => statuses.push(message),
  createKey: () => `00000000-0000-0000-0000-${{String(++keyNo).padStart(12, '0')}}`,
}});
if (await controller.start()) throw new Error('disabled gate intercepted ordinary Generate');
enabled = true;
await controller.start();
if (saves.join() !== 'working:tx-1,dictation:tx-1' || calls.length !== 1 || !calls[0][0].includes('/consultation-split-intents')) throw new Error('sources were not target-bound before intent');
if (calls.some(([url]) => url.includes('/generate-output'))) throw new Error('split path called generate-output');
if (refreshes.length !== 1 || refreshes[0][0] !== transcriptId || refreshes[0][1]?.guardTranscriptId !== transcriptId) throw new Error('accepted queued intent did not refresh the guarded workspace');
if (!statuses.at(-1).includes('saved and will continue if you leave this page')) throw new Error('workspace refresh replaced accepted-intent feedback with generic analysis status');
await controller.start();
if (calls.length !== 1 || keyNo !== 1) throw new Error('duplicate Create started a second operation');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'processing' }} }});
if (!statuses.at(-1).includes('saved and will continue if you leave this page')) throw new Error('accepted processing state lost durable-work guidance');
draftResponse = {{ ok: true, json: async () => ({{ draft_id: 'draft-1' }}) }};
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft: null }});
await Promise.resolve(); await Promise.resolve();
if (calls.filter(([url]) => url.includes('consultation-split-draft')).length !== 1) throw new Error('ready draft was not initialized');
const draft = {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [
  {{ topic_uuid: 'topic-1', title: 'Synthetic primary', is_primary: true, disposition: 'separate_note', template_id: 'template-1' }},
  {{ topic_uuid: 'topic-2', title: 'Synthetic secondary', is_primary: false, disposition: 'separate_note', template_id: 'template-1' }},
] }};
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
if (opens !== 1 || applies !== 1) throw new Error('current ready draft did not open exactly once');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }} }});
if (!statuses.at(-1).includes('Starting one note')) throw new Error('not-required intent did not report its existing one-note continuation');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-restored', analysis: {{ analysis_id: 'analysis-r', status: 'ready' }}, draft: {{ ...draft, analysis_id: 'analysis-r' }} }});
if (opens !== 1) throw new Error('restored draft auto-opened');
intentResponse = {{ status: 503, ok: false, json: async () => ({{}}) }};
await controller.start(); await controller.start();
const retryBodies = calls.filter(([url]) => url.includes('intents')).slice(-2).map(([, method]) => method);
if (retryBodies.join() !== 'POST,POST' || keyNo !== 2) throw new Error('ambiguous retry did not reuse one browser key');
transcriptId = 'tx-2';
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
if (opens !== 1) throw new Error('stale transcript completion opened a modal');
"""
    )
    # The assertion body is intentionally a Node-only behavioral harness: no
    # browser implementation or timing API makes these branches nondeterministic.
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_accepted_queued_intent_seeds_restoration_for_one_note_without_sse(tmp_path):
    runner = tmp_path / "split-queued-restoration-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitAnalysisRestorationPoller, createSplitGenerateController }} = await import('{module_uri}');
let latestAnalysis = null, refreshes = 0, continueCalls = 0, controller, resolveContinued;
const continued = new Promise((resolve) => {{ resolveContinued = resolve; }});
const applyWorkspace = (analysis) => {{
  latestAnalysis = analysis;
  controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis }});
  poller.updateWorkspace();
}};
const poller = createSplitAnalysisRestorationPoller({{
  getState: () => ({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: latestAnalysis }}),
  backoffMs: [0],
  refreshWorkspace: async () => {{
    refreshes += 1;
    if (refreshes === 1) {{
      applyWorkspace({{ analysis_id: 'analysis-1', status: 'queued' }});
      return {{ consultation_split_analysis: latestAnalysis }};
    }}
    applyWorkspace({{ analysis_id: 'analysis-1', status: 'not_required' }});
    return {{ consultation_split_analysis: latestAnalysis }};
  }},
}});
controller = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
  createKey: () => 'key-1', saveSources: async () => {{}},
  refreshWorkspace: () => poller.getState().scheduled || refreshes === 0
    ? (refreshes += 1, applyWorkspace({{ analysis_id: 'analysis-1', status: 'queued' }}), Promise.resolve({{ consultation_split_analysis: latestAnalysis }}))
    : Promise.resolve(null),
  fetcher: async (url) => {{
    if (url.endsWith('/consultation-split-intents')) return {{ ok: true, json: async () => ({{ intent_id: 'intent-1', analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}) }};
    continueCalls += 1;
    resolveContinued();
    return {{ ok: true, json: async () => ({{ document: {{ id: 'document-1' }} }}) }};
  }},
}});
await controller.start();
await Promise.race([
  continued,
  new Promise((_, reject) => setTimeout(() => reject(new Error('restoration did not continue one note')), 250)),
]);
if (!latestAnalysis || latestAnalysis.status !== 'not_required') throw new Error('queued analysis did not advance through restoration');
if (continueCalls !== 1) throw new Error('one-note continuation did not start after restored analysis');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_single_issue_automatically_continues_as_one_note_single_flight_and_transcript_guarded(tmp_path):
    runner = tmp_path / "split-continue-one-note-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
let transcriptId = 'tx-1';
const calls = [];
let resolveContinue;
const fetcher = async (url, options) => {{
  calls.push({{ url, options }});
  if (url.endsWith('/consultation-split-intents')) return {{ ok: true, json: async () => ({{ intent_id: 'intent-1', analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }} }}) }};
  return new Promise((resolve) => {{ resolveContinue = () => resolve({{ ok: true, json: async () => ({{ intent_id: 'intent-1', idempotency_replayed: false, document: {{ id: 'doc-1' }}, consumed_document_deleted: false }}) }}); }});
}};
let continued = [];
let visible = [];
const controller = createSplitGenerateController({{
  fetcher, getCapabilityEnabled: () => true, getTranscriptId: () => transcriptId,
  getTemplateId: () => 'template-1', saveSources: async () => {{}}, createKey: () => 'key-1',
  setContinueAvailable: (value) => visible.push(value), onGeneratedDocument: async (document) => continued.push(document.id),
}});
const started = controller.start();
for (let attempt = 0; attempt < 5 && !resolveContinue; attempt += 1) {{
  await new Promise((resolve) => setTimeout(resolve, 0));
}}
if (visible.includes(true)) throw new Error('single-topic result exposed an unnecessary continue action');
const first = started;
const second = controller.continueAsOneNote();
if (first === second) {{ /* both may share completion state; request count is authoritative */ }}
if (calls.filter((call) => call.url.includes('continue-as-one-note')).length !== 1) throw new Error('continue was not single-flight');
const request = calls.find((call) => call.url.includes('continue-as-one-note'));
if (request.options.method !== 'POST' || 'body' in request.options || request.url.includes('generate-output')) throw new Error('continue request was not bodyless dedicated endpoint');
resolveContinue(); await first; await second;
await new Promise((resolve) => setTimeout(resolve, 0));
if (continued.join() !== 'doc-1') throw new Error('successful continuation did not feed generated-document flow');
transcriptId = 'tx-2';
if (await controller.continueAsOneNote()) throw new Error('stale transcript continued an old intent');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_single_issue_workspace_refresh_during_continuation_never_warns_no_note_generated(tmp_path):
    runner = tmp_path / "split-one-note-refresh-race-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
const statuses = [];
let controller;
const controllerOptions = {{
  getCapabilityEnabled: () => true,
  getTranscriptId: () => 'tx-1',
  getTemplateId: () => 'template-1',
  createKey: () => '00000000-0000-0000-0000-000000000001',
  saveSources: async () => {{}},
  setStatus: (message) => statuses.push(message),
  fetcher: async (url) => {{
    if (url.endsWith('/consultation-split-intents')) {{
      return {{ ok: true, json: async () => ({{
        intent_id: 'intent-1',
        analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }},
      }}) }};
    }}
    if (url.endsWith('/continue-as-one-note')) {{
      return {{ ok: true, json: async () => ({{ document: {{ id: 'doc-1' }} }}) }};
    }}
    throw new Error(`unexpected request: ${{url}}`);
  }},
  onGeneratedDocument: async () => {{
    // The application refreshes workspace/document state here. That refresh
    // can arrive after the intent is marked consumed but before success is
    // reported, which previously emitted a contradictory warning toast.
    controller.applyWorkspaceState({{
      capabilityEnabled: true,
      transcriptId: 'tx-1',
      analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }},
    }});
  }},
}};
controller = createSplitGenerateController(controllerOptions);
await controller.start();
for (let turn = 0; turn < 8 && controller.getOperation(); turn += 1) await Promise.resolve();
if (statuses.some((message) => message.includes('No note generated'))) {{
  throw new Error('workspace refresh during a successful one-note continuation showed a failure warning');
}}
if (!statuses.at(-1)?.includes('Queued one note.')) throw new Error('successful one-note continuation did not settle with success');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_suggested_problem_requests_are_manual_and_never_auto_continue_one_note(tmp_path):
    runner = tmp_path / "split-suggested-problems-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
const requestBodies = [];
let continueCalls = 0;
const controller = createSplitGenerateController({{
  getCapabilityEnabled: () => false,
  getCapabilityAvailable: () => true,
  getTranscriptId: () => 'tx-1',
  getTemplateId: () => 'template-1',
  createKey: () => `00000000-0000-0000-0000-${{String(requestBodies.length + 1).padStart(12, '0')}}`,
  saveSources: async () => {{}},
  fetcher: async (url, options = {{}}) => {{
    if (url.endsWith('/consultation-split-intents')) {{
      requestBodies.push(JSON.parse(options.body));
      return {{ ok: true, json: async () => ({{
        intent_id: `intent-${{requestBodies.length}}`,
        manual_review_requested: true,
        analysis: {{ analysis_id: `analysis-${{requestBodies.length}}`, status: 'not_required' }},
      }}) }};
    }}
    if (url.endsWith('/consultation-split-draft')) {{
      return {{ ok: true, json: async () => ({{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }}) }};
    }}
    if (url.includes('/continue-as-one-note')) {{ continueCalls += 1; throw new Error('manual review auto-continued one note'); }}
    throw new Error(`unexpected request: ${{url}}`);
  }},
  refreshWorkspace: async () => ({{ consultation_split_draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }} }}),
}});
await controller.start({{ manualReviewRequested: true }});
for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
if (requestBodies.length !== 1 || requestBodies[0].manual_review_requested !== true || 'retry_failed_analysis' in requestBodies[0]) {{
  throw new Error('Suggest problems did not send its strict manual-review request');
}}
if (continueCalls !== 0) throw new Error('single-problem suggestion automatically generated a note');
await controller.start({{ manualReviewRequested: true, retryFailedAnalysis: true }});
if (requestBodies.length !== 2 || requestBodies[1].manual_review_requested !== true || requestBodies[1].retry_failed_analysis !== true) {{
  throw new Error('Retry analysis did not send one fresh strict retry request');
}}
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_ambiguous_suggest_and_retry_analysis_transport_failures_reuse_their_keys(tmp_path):
    runner = tmp_path / "split-suggestion-transport-retry-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
for (const retryFailedAnalysis of [false, true]) {{
  const bodies = [];
  let calls = 0;
  let keyNumber = 0;
  const controller = createSplitGenerateController({{
    getCapabilityEnabled: () => false,
    getCapabilityAvailable: () => true,
    getTranscriptId: () => 'tx-1',
    getTemplateId: () => 'template-1',
    createKey: () => `00000000-0000-0000-0000-${{String(++keyNumber).padStart(12, '0')}}`,
    saveSources: async () => {{}},
    fetcher: async (_url, options) => {{
      bodies.push(JSON.parse(options.body));
      calls += 1;
      if (calls === 1) throw new Error('ambiguous transport failure');
      return {{ ok: true, json: async () => ({{
        intent_id: 'intent-1', manual_review_requested: true,
        analysis: {{ analysis_id: 'analysis-1', status: 'queued' }},
      }}) }};
    }},
    refreshWorkspace: async () => null,
  }});
  await controller.start({{ manualReviewRequested: true, retryFailedAnalysis, forceNewReview: true }});
  await controller.start({{ manualReviewRequested: true, retryFailedAnalysis, forceNewReview: true }});
  if (bodies.length !== 2 || bodies[0].client_idempotency_key !== bodies[1].client_idempotency_key) {{
    throw new Error('ambiguous manual request retried with a different idempotency key');
  }}
  if (keyNumber !== 1) throw new Error('ambiguous manual retry created a fresh candidate');
  if (bodies.some((body) => body.manual_review_requested !== true || Boolean(body.retry_failed_analysis) !== retryFailedAnalysis)) {{
    throw new Error('manual retry did not preserve its strict request flags');
  }}
}}
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_ambiguous_ordinary_create_cannot_be_retargeted_as_manual_review(tmp_path):
    runner = tmp_path / "split-ambiguous-semantics-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
const bodies = []; let keyNumber = 0; const statuses = [];
const controller = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getCapabilityAvailable: () => true,
  getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
  createKey: () => `00000000-0000-0000-0000-${{String(++keyNumber).padStart(12, '0')}}`,
  saveSources: async () => {{}}, setStatus: (message) => statuses.push(message),
  fetcher: async (_url, options) => {{ bodies.push(JSON.parse(options.body)); throw new Error('ambiguous transport failure'); }},
}});
await controller.start();
if (bodies.length !== 1 || bodies[0].manual_review_requested || keyNumber !== 1) throw new Error('ordinary Create setup did not issue one ordinary request');
if (await controller.start({{ manualReviewRequested: true, forceNewReview: true }})) throw new Error('ambiguous ordinary Create was retargeted as a manual request');
if (bodies.length !== 1 || keyNumber !== 1 || !statuses.at(-1).includes('Retry the original action')) {{
  throw new Error('manual action dispatched while an ordinary request was unknown');
}}
await controller.start();
if (bodies.length !== 2 || bodies[1].client_idempotency_key !== bodies[0].client_idempotency_key || bodies[1].manual_review_requested) {{
  throw new Error('same-semantic ordinary retry did not preserve its original request');
}}
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_confirmed_split_operation_is_retired_so_regenerate_starts_a_new_intent(tmp_path):
    runner = tmp_path / "split-repeat-regeneration-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
let intentCalls = 0;
const controller = createSplitGenerateController({{
  getCapabilityEnabled: () => true,
  getTranscriptId: () => 'transcript-1',
  getTemplateId: () => 'template-1',
  createKey: () => `key-${{intentCalls + 1}}`,
  saveSources: async () => {{}},
  fetcher: async (url) => {{
    if (!url.includes('/consultation-split-intents')) throw new Error('unexpected draft request');
    intentCalls += 1;
    return {{ ok: true, json: async () => ({{
      intent_id: `intent-${{intentCalls}}`,
      analysis: {{ analysis_id: 'analysis-1', status: 'queued' }},
    }}) }};
  }},
}});
await controller.start();
if (intentCalls !== 1) throw new Error('first split Generate did not create one intent');
controller.applyWorkspaceState({{
  capabilityEnabled: true,
  transcriptId: 'transcript-1',
  analysis: {{ analysis_id: 'analysis-1', status: 'ready' }},
  draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }},
}});
controller.applyWorkspaceState({{
  capabilityEnabled: true,
  transcriptId: 'transcript-1',
  analysis: {{ analysis_id: 'analysis-1', status: 'ready' }},
  draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'confirmed', topics: [] }},
}});
if (controller.getOperation()) throw new Error('confirmed split kept the completed browser operation');
await controller.start();
if (intentCalls !== 2) throw new Error('Regenerate after a confirmed split did not create a new intent');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_partial_note_actions_use_server_flags_single_flight_and_stale_guards(tmp_path):
    runner = tmp_path / "split-partial-actions-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitPartialActionsController }} = await import('{module_uri}');
 class Button {{ constructor() {{ this.hidden = false; this.disabled = false; this.listeners = new Map(); this.attributes = new Map(); }} addEventListener(k, v) {{ this.listeners.set(k, v); }} setAttribute(k, v) {{ this.attributes.set(k, v); }} removeAttribute(k) {{ this.attributes.delete(k); }} getAttribute(k) {{ return this.attributes.get(k) || null; }} }}
const retry = new Button(), keep = new Button();
const root = {{ hidden: false }}; const status = {{ textContent: '', dataset: {{}} }};
let transcriptId = 'tx-1', resolveRequest, calls = [], selected = [];
const controller = createSplitPartialActionsController({{
  getTranscriptId: () => transcriptId, actionsRoot: root, status, retryButton: retry, keepButton: keep,
  fetcher: (url, options) => {{ calls.push([url, options]); return new Promise((resolve) => {{ resolveRequest = () => resolve({{ ok: true, json: async () => ({{ document_ids: ['survivor'] }}) }}); }}); }},
  refreshWorkspace: async () => ({{ consultation_split_batch: {{ preferred_document_id: 'primary' }} }}),
  selectDocument: (id) => selected.push(id),
}});
controller.applyWorkspaceState({{ batch_id: 'batch-1', can_retry_missing: true, can_keep_available: true }}, 'tx-1');
const first = controller.retry(); const second = controller.retry();
if (calls.length !== 1 || !retry.disabled || !keep.disabled) throw new Error('retry was not single-flight');
if (!calls[0][0].endsWith('/transcripts/tx-1/consultation-split-batches/batch-1/retry-missing-notes') || calls[0][1].method !== 'POST') throw new Error('wrong nested retry request');
resolveRequest(); await first; await second;
if (selected.join() !== 'primary') throw new Error('did not select server preferred document');
controller.applyWorkspaceState({{ batch_id: 'batch-1', can_retry_missing: false, can_keep_available: true }}, 'tx-1');
const keepRun = controller.keep(); transcriptId = 'tx-2'; resolveRequest();
if (await keepRun || selected.length !== 1) throw new Error('stale action applied after transcript switch');
transcriptId = 'tx-1';
controller.applyWorkspaceState({{ batch_id: 'batch-1', status: 'partially_ready', primary_failed: true, can_retry_missing: false, can_keep_available: true }}, 'tx-1');
 if (!status.textContent.includes('primary note failed') || keep.hidden || root.hidden || keep.getAttribute('aria-describedby') !== 'split-partial-status') throw new Error('primary failure warning or Keep control missing');
controller.applyWorkspaceState({{ batch_id: 'batch-1', status: 'completed_partial', primary_failed: true, can_retry_missing: false, can_keep_available: false }}, 'tx-1');
 if (!status.textContent.includes('surviving secondary') || !retry.hidden || !keep.hidden || root.hidden) throw new Error('partial completion did not retain accessible primary-failure state');
 if (keep.getAttribute('aria-describedby')) throw new Error('hidden Keep control retained stale primary-failure description');
controller.applyWorkspaceState({{ batch_id: 'batch-1', can_retry_missing: true, can_keep_available: true }}, 'tx-1');
transcriptId = 'tx-2'; if (await controller.retry()) throw new Error('stale guard dispatched action');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_regenerate_queues_a_fresh_batch_without_opening_review(tmp_path):
    runner = tmp_path / "split-direct-batch-regeneration-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ dispatchTemplateGeneration }} = await import('{module_uri}');
let calls = 0, reviewStarts = 0;
const controller = {{
  start: async () => {{ reviewStarts += 1; return true; }},
  regenerateConfirmedBatch: async (value) => {{
    calls += 1;
    if (value.transcriptId !== 'tx-1' || value.batchId !== 'batch-1') throw new Error('wrong batch regeneration target');
    return true;
  }},
}};
const first = dispatchTemplateGeneration({{ capabilityEnabled: true, splitController: controller, transcriptId: 'tx-1', templateId: 'template-1', confirmedBatchId: 'batch-1' }});
const second = dispatchTemplateGeneration({{ capabilityEnabled: true, splitController: controller, transcriptId: 'tx-1', templateId: 'template-1', confirmedBatchId: 'batch-1' }});
await Promise.all([first, second]);
if (calls !== 2 || reviewStarts !== 0) throw new Error('main Regenerate reopened split review');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_generation_notifies_dictation_only_after_accepted_request(tmp_path):
    runner = tmp_path / "split-dictation-accepted-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController, dispatchTemplateGeneration }} = await import('{module_uri}');
const accepted = [];
const order = [];
const intentController = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
  createKey: () => 'intent-key', saveSources: async () => {{}},
  fetcher: async (url) => url.includes('consultation-split-draft')
    ? ({{ ok: true, json: async () => ({{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }}) }})
    : ({{ ok: true, json: async () => ({{ intent_id: 'intent-1', analysis: {{ analysis_id: 'analysis-1', status: 'ready' }} }}) }}),
  refreshWorkspace: async () => ({{
    consultation_split_draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }},
    available_templates: [],
  }}),
  reviewController: {{ applyWorkspaceState: () => order.push('review-applied'), open: () => order.push('review-opened') }},
}});
await dispatchTemplateGeneration({{
  capabilityEnabled: true, splitController: intentController, transcriptId: 'tx-1', templateId: 'template-1',
  onAccepted: () => {{ accepted.push('intent'); order.push('intent-accepted'); }},
}});
for (let turn = 0; turn < 5 && !order.includes('review-applied'); turn += 1) await Promise.resolve();
if (accepted.join() !== 'intent' || order[0] !== 'intent-accepted' || !order.includes('review-applied')) {{
  throw new Error('accepted intent did not notify before split review state was applied');
}}
const rejectedController = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
  createKey: () => 'rejected-key', saveSources: async () => {{}},
  fetcher: async () => ({{ ok: false, status: 400, json: async () => ({{}}) }}),
}});
await dispatchTemplateGeneration({{
  capabilityEnabled: true, splitController: rejectedController, transcriptId: 'tx-1', templateId: 'template-1',
  onAccepted: () => accepted.push('rejected-intent'),
}});
if (accepted.length !== 1) throw new Error('rejected intent notified dictation as accepted');
const regenerationOrder = [];
const regenerationController = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', createKey: () => 'batch-key',
  fetcher: async () => ({{ ok: true, json: async () => ({{ batch_id: 'batch-2' }}) }}),
  refreshWorkspace: async () => {{ regenerationOrder.push('refresh'); return null; }},
}});
await dispatchTemplateGeneration({{
  capabilityEnabled: true, splitController: regenerationController, transcriptId: 'tx-1', templateId: 'template-1', confirmedBatchId: 'batch-1',
  onAccepted: () => {{ accepted.push('regeneration'); regenerationOrder.push('accepted'); }},
}});
if (accepted.join() !== 'intent,regeneration' || regenerationOrder.join() !== 'accepted,refresh') {{
  throw new Error('accepted batch regeneration did not notify before workspace refresh');
}}
const rejectedRegenerationController = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', createKey: () => 'failed-batch-key',
  fetcher: async () => ({{ ok: false, status: 409, json: async () => ({{}}) }}),
}});
await dispatchTemplateGeneration({{
  capabilityEnabled: true, splitController: rejectedRegenerationController, transcriptId: 'tx-1', templateId: 'template-1', confirmedBatchId: 'batch-1',
  onAccepted: () => accepted.push('rejected-regeneration'),
}});
if (accepted.join() !== 'intent,regeneration') throw new Error('rejected batch regeneration notified dictation as accepted');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_split_draft_post_has_no_dedicated_llm_rate_limiter():
    routes = (ROOT / "app/routes/api_routes.py").read_text()
    draft_route = routes.split('@api.post(\n    "/transcripts/{transcript_id}/consultation-split-draft"', 1)[1].split('@api.get(', 1)[0]
    assert 'RATE_LIMIT' not in draft_route


def test_split_generate_draft_initialization_failures_settle_once_and_do_not_storm(tmp_path):
    runner = tmp_path / "split-draft-failure-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController }} = await import('{module_uri}');
const flush = async () => {{ for (let index = 0; index < 8; index += 1) await Promise.resolve(); }};
for (const failure of ['non_ok', 'malformed', 'network']) {{
  let draftCalls = 0, busy = [], statuses = [];
  const controller = createSplitGenerateController({{
    getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
    createKey: () => '00000000-0000-0000-0000-000000000001', saveSources: async () => {{}},
    setBusy: (value) => busy.push(value), setStatus: (message) => statuses.push(message),
    refreshWorkspace: async () => ({{ consultation_split_draft: {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }} }}),
    fetcher: async (url) => {{
      if (url.includes('intents')) return {{ ok: true, status: 202, json: async () => ({{ analysis: {{ analysis_id: 'analysis-1', status: 'ready' }} }}) }};
      draftCalls += 1;
      if (failure === 'non_ok') return {{ ok: false, status: 409, json: async () => ({{}}) }};
      if (failure === 'malformed') return {{ ok: true, status: 200, json: async () => {{ throw new Error('bad json'); }} }};
      throw new Error('network');
    }},
  }});
  await controller.start(); await flush();
  if (draftCalls !== 1 || busy.filter((value) => !value).length !== 1 || !statuses.at(-1).includes('No note generated')) throw new Error(`${{failure}} did not settle safely`);
  controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft: null }});
  controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft: null }});
  await flush();
  if (draftCalls !== 1) throw new Error(`${{failure}} retried draft POST from workspace updates`);
  await controller.start(); await flush();
  if (draftCalls !== 2) throw new Error(`${{failure}} blocked deliberate draft retry`);
}}
let resolveDraft; let draftCalls = 0;
const overlapping = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => 'template-1',
  createKey: () => '00000000-0000-0000-0000-000000000002', saveSources: async () => {{}},
  fetcher: async (url) => url.includes('intents')
    ? {{ ok: true, json: async () => ({{ analysis: {{ analysis_id: 'analysis-2', status: 'ready' }} }}) }}
    : (draftCalls += 1, new Promise((resolve) => {{ resolveDraft = () => resolve({{ ok: true, json: async () => ({{ draft_id: 'draft-2' }}) }}); }})),
  refreshWorkspace: async () => ({{ consultation_split_draft: {{ draft_id: 'draft-2', analysis_id: 'analysis-2', status: 'active', topics: [] }} }}),
}});
await overlapping.start(); await flush();
overlapping.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-2', status: 'ready' }}, draft: null }});
overlapping.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'tx-1', analysis: {{ analysis_id: 'analysis-2', status: 'ready' }}, draft: null }});
if (draftCalls !== 1) throw new Error('overlapping workspace updates started multiple draft POSTs');
resolveDraft(); await flush();
let resolveAcceptance, acceptedCount = 0;
const awaitingAcceptance = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'A', getTemplateId: () => 'template-1',
  createKey: () => '00000000-0000-0000-0000-000000000004',
  fetcher: async () => new Promise((resolve) => {{ resolveAcceptance = resolve; }}),
}});
const awaitingRequest = awaitingAcceptance.start({{ onAccepted: () => {{ acceptedCount += 1; }} }});
await flush();
awaitingAcceptance.applyWorkspaceState({{ capabilityEnabled: true, transcriptId: 'A', analysis: null }});
if (!awaitingAcceptance.getOperation()?.pending) throw new Error('workspace read cleared pending acceptance');
resolveAcceptance({{ ok: true, json: async () => ({{ intent_id: 'intent-A', analysis: {{ analysis_id: 'analysis-A', status: 'queued' }} }}) }});
await awaitingRequest;
if (acceptedCount !== 1) throw new Error('workspace read lost acceptance callback');
let activeTranscript = 'A', resolveIntent, switchedBusy = [], switchedStatuses = [], switchedDraftCalls = 0;
const switched = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => activeTranscript, getTemplateId: () => 'template-1',
  createKey: () => '00000000-0000-0000-0000-000000000003', saveSources: async () => {{}}, setBusy: (value) => switchedBusy.push(value), setStatus: (message) => switchedStatuses.push(message),
  fetcher: async (url) => url.includes('intents')
    ? new Promise((resolve) => {{ resolveIntent = resolve; }})
    : (switchedDraftCalls += 1, {{ ok: true, json: async () => ({{ draft_id: 'unexpected' }}) }}),
}});
const pendingIntent = switched.start(); await flush(); activeTranscript = 'B';
resolveIntent({{ ok: true, status: 202, json: async () => ({{ analysis: {{ analysis_id: 'analysis-A', status: 'queued' }} }}) }});
await pendingIntent; await flush();
if (switched.getOperation() || switchedBusy.at(-1) !== false || switchedStatuses.at(-1) !== '' || switchedDraftCalls !== 0) throw new Error('A intent response applied after switching to B');
let selectedTemplate = 'template-1', keyNumber = 0, intentBodies = [], failedDraftCalls = 0;
const changedTemplate = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-1', getTemplateId: () => selectedTemplate,
  createKey: () => `00000000-0000-0000-0000-${{String(++keyNumber).padStart(12, '0')}}`, saveSources: async () => {{}},
  fetcher: async (url, options) => {{
    if (url.includes('intents')) {{ intentBodies.push(JSON.parse(options.body)); return {{ ok: true, json: async () => ({{ analysis: {{ analysis_id: `analysis-${{intentBodies.length}}`, status: 'ready' }} }}) }}; }}
    failedDraftCalls += 1; return {{ ok: false, status: 409, json: async () => ({{}}) }};
  }},
}});
await changedTemplate.start(); await flush(); selectedTemplate = 'template-2';
await changedTemplate.start(); await flush();
if (failedDraftCalls !== 2 || intentBodies.length !== 2 || keyNumber !== 2 || intentBodies[0].client_idempotency_key === intentBodies[1].client_idempotency_key || intentBodies[1].selected_template_id !== 'template-2') throw new Error('changed template retried old draft initialization');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_generate_submit_wiring_dispatches_ordinary_or_split_without_loading_app_shell(tmp_path):
    runner = tmp_path / "generate-submit-wiring-runner.mjs"
    actions_uri = (ROOT / "app/static/js/transcribe/actions.js").as_uri()
    split_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ attachTranscribeActions }} = await import('{actions_uri}');
const {{ dispatchTemplateGeneration }} = await import('{split_uri}');
class Form {{
  constructor() {{ this.listeners = new Map(); }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  querySelector() {{ return null; }}
  async submit() {{ let prevented = false; await this.listeners.get('submit')({{ preventDefault: () => {{ prevented = true; }} }}); if (!prevented) throw new Error('form submission was not intercepted'); }}
}}
globalThis.window = {{ addEventListener: () => {{}}, document: {{ addEventListener: () => {{}} }}, matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}), localStorage: {{ getItem: () => null, setItem: () => {{}} }} }};
globalThis.document = globalThis.window.document;
const form = new Form(); const selected = {{ value: 'template-1', addEventListener: () => {{}} }};
let gate = false, ordinary = 0, split = 0, captured = null;
attachTranscribeActions({{
  dom: {{ generateOutputForm: form, generateOutputTemplateSelect: selected }}, routeBase: '/transcribe',
  getTranscriptId: () => 'tx-1', enqueueTemplateGeneration: async (args) => {{
    captured = args;
    return dispatchTemplateGeneration({{
      capabilityEnabled: gate,
      splitController: {{ start: async (input) => {{ split += 1; if (input.transcriptId !== 'tx-1' || input.templateId !== 'template-1') throw new Error('captured Generate values changed'); return true; }} }},
      ...args, ordinary: async () => {{ ordinary += 1; return true; }},
    }});
  }},
  getTranscriptText: () => '', getActiveIngestionMode: () => 'whole_file', getIsLiveCaptureUiActive: () => false,
  getIsRecordingSwitchBlocked: () => false, showFlash: () => {{}}, showCopyToast: () => {{}}, parseErrorMessage: async () => '',
  fetchWorkspace: async () => null, pollWorkspace: () => {{}}, scheduleWorkspaceRefreshBurst: () => {{}}, syncTranscriptTitleIfNeeded: async () => {{}},
  persistPendingEditorsBeforeWorkspaceSwitch: async () => true, setVisibleStatus: () => {{}}, setSessionProgress: () => {{}},
  setRetryAvailability: () => {{}}, reflectBackendStatus: () => {{}}, syncGenerationAvailability: () => {{}}, persistUserAppPreferences: async () => {{}},
}});
await form.submit();
if (ordinary !== 1 || split !== 0 || captured.transcriptId !== 'tx-1') throw new Error('gate-off submit did not reach ordinary generation');
gate = true; await form.submit();
if (ordinary !== 1 || split !== 1) throw new Error('gate-on submit did not use split dispatcher');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_consultation_history_switch_waits_for_create_submission_acceptance(tmp_path):
    runner = tmp_path / "generation-navigation-barrier-runner.mjs"
    actions_uri = (ROOT / "app/static/js/transcribe/actions.js").as_uri()
    runner.write_text(
        f"""
class FakeElement {{
  constructor() {{ this.dataset = {{}}; this.listeners = new Map(); this.closestMatches = {{}}; this.href = ''; }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  fire(type, event = {{}}) {{ this.listeners.get(type)?.({{ preventDefault() {{}}, target: this, ...event }}); }}
  closest(selector) {{ return this.closestMatches[selector] || null; }}
  contains() {{ return true; }}
  querySelector() {{ return null; }}
  querySelectorAll() {{ return []; }}
}}
globalThis.Element = FakeElement;
globalThis.HTMLInputElement = class extends FakeElement {{}};
globalThis.HTMLTextAreaElement = class extends FakeElement {{}};
globalThis.document = {{ cookie: '', addEventListener() {{}}, querySelector() {{ return null; }} }};
globalThis.window = {{
  document: globalThis.document, addEventListener() {{}}, confirm: () => true,
  matchMedia: () => ({{ matches: false, addEventListener() {{}} }}),
  localStorage: {{ getItem: () => null, setItem() {{}} }},
  location: {{ href: 'http://localhost/transcribe?transcript_id=A', origin: 'http://localhost', assign(url) {{ navigations.push(url); }} }},
  history: {{ pushState() {{}} }},
}};
const {{ attachTranscribeActions }} = await import('{actions_uri}');
const sessionList = new FakeElement();
const newSessionForm = new FakeElement();
const link = new FakeElement(); link.dataset.transcriptId = 'B'; link.href = '/transcribe?transcript_id=B'; link.closestMatches['[data-session-link]'] = link;
let release; let accepted = true; let switches = 0; let starts = 0; const navigations = [];
globalThis.fetch = async (url) => {{
  if (url !== '/api/v1/transcripts/start') throw new Error(`unexpected fetch ${{url}}`);
  starts += 1;
  return {{ ok: true, json: async () => ({{ id: 'new-consultation' }}) }};
}};
const pending = () => new Promise((resolve) => {{ release = () => resolve(accepted); }});
let barrier = pending();
attachTranscribeActions({{
  dom: {{ sessionList, newSessionForm }}, routeBase: '/transcribe', getTranscriptId: () => 'A',
  getTranscriptText: () => '', getActiveIngestionMode: () => 'whole_file', getIsLiveCaptureUiActive: () => false,
  getIsRecordingSwitchBlocked: () => false, showFlash() {{}}, showCopyToast() {{}}, parseErrorMessage: async (_response, fallback) => fallback,
  fetchWorkspace: async (id, options) => {{ if (id === 'B' && options?.allowTranscriptSwitch) switches += 1; return {{ active_transcript: {{ id }} }}; }},
  pollWorkspace() {{}}, scheduleWorkspaceRefreshBurst() {{}}, syncTranscriptTitleIfNeeded: async () => {{}},
  persistPendingEditorsBeforeWorkspaceSwitch: async () => true, waitForGenerationSubmission: () => barrier,
  enqueueTemplateGeneration: async () => true, setVisibleStatus() {{}}, setSessionProgress() {{}}, setRetryAvailability() {{}},
  reflectBackendStatus() {{}}, syncGenerationAvailability() {{}}, persistUserAppPreferences: async () => {{}},
  setMicButtons() {{}}, setTab() {{}}, structuredEditor: {{}}, saveWorkingNoteBeforeGeneration: async () => {{}}, saveDictationBeforeGeneration: async () => {{}},
}});
sessionList.fire('click', {{ target: link }});
for (let tick = 0; tick < 4; tick += 1) await Promise.resolve();
if (switches !== 0) throw new Error('history switched before Create reached durable acceptance');
release(); for (let tick = 0; tick < 6; tick += 1) await Promise.resolve();
if (switches !== 1) throw new Error('history did not switch after durable acceptance');
accepted = false; barrier = pending(); sessionList.fire('click', {{ target: link }}); release();
for (let tick = 0; tick < 6; tick += 1) await Promise.resolve();
if (switches !== 1) throw new Error('history switched after Create submission failed');
accepted = true; barrier = pending(); newSessionForm.fire('submit');
for (let tick = 0; tick < 4; tick += 1) await Promise.resolve();
if (starts !== 0) throw new Error('new consultation started before Create reached durable acceptance');
release(); for (let tick = 0; tick < 8; tick += 1) await Promise.resolve();
if (starts !== 1 || navigations.at(-1) !== '/transcribe?transcript_id=new-consultation') throw new Error('new consultation did not start after durable acceptance');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})


def test_living_problem_plan_review_flows_with_rendered_fake_dom(tmp_path):
    """Exercise the living problem-plan editor through its actual DOM events."""
    runner = tmp_path / "living-problem-plan-review.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
class Classes {{ constructor() {{ this.s = new Set(); }} add(x) {{ this.s.add(x); }} remove(x) {{ this.s.delete(x); }} contains(x) {{ return this.s.has(x); }} toggle(x, v) {{ v ? this.add(x) : this.remove(x); }} }}
const camel = (s) => s.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
class E {{
  constructor(tag='div') {{ this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={{}}; this.attrs=new Map(); this.listeners=new Map(); this.classList=new Classes(); this.hidden=false; this.disabled=false; this.value=''; this.textContent=''; this.style={{}}; this.parentNode=null; this.draggable=false; }}
  setAttribute(k,v) {{ this.attrs.set(k,String(v)); if(k.startsWith('data-')) this.dataset[camel(k)]=String(v); }} getAttribute(k) {{ return this.attrs.get(k) ?? null; }} hasAttribute(k) {{ return this.attrs.has(k) || (k.startsWith('data-') && this.dataset[camel(k)]!==undefined); }}
  append(...xs) {{ for(const x of xs) {{ x.parentNode=this; this.children.push(x); }} }} prepend(x) {{ x.parentNode=this; this.children.unshift(x); }} replaceChildren(...xs) {{ this.children.forEach(x=>x.parentNode=null); this.children=[]; this.append(...xs); }}
  addEventListener(k,fn) {{ if(!this.listeners.has(k)) this.listeners.set(k,[]); this.listeners.get(k).push(fn); }} removeEventListener(k,fn) {{ this.listeners.set(k,(this.listeners.get(k)||[]).filter(x=>x!==fn)); }} fire(k,e={{}}) {{ const ev={{target:e.target||this, preventDefault(){{this.defaultPrevented=true;}}, stopPropagation(){{}}, ...e}}; for(const fn of this.listeners.get(k)||[]) fn(ev); if(!ev.stopped) this.parentNode?.fire(k,ev); }} click() {{ this.fire('click'); }} focus() {{ document.activeElement=this; }}
  matches(sel) {{ if(Array.isArray(sel)) return sel.some(x=>this.matches(x)); const tag=sel.match(/^[a-z]+/i)?.[0]; if(tag&&tag.toUpperCase()!==this.tagName)return false; for(const m of sel.matchAll(/\\[([^=\\]]+)(?:=["']?([^\\]"']+)["']?)?\\]/g)) {{ let [,k,v]=m; if(!this.hasAttribute(k))return false; if(v&&String(this.getAttribute(k)??this.dataset[camel(k)])!==v)return false; }} if(sel.startsWith('.'))return this.className?.split(' ').includes(sel.slice(1))||this.classList.contains(sel.slice(1)); return true; }}
  closest(s) {{ for(let n=this;n;n=n.parentNode)if(n.matches(s))return n; return null; }} contains(n) {{ for(let x=n;x;x=x.parentNode)if(x===this)return true; return false; }}
  querySelectorAll(s) {{ const out=[]; const walk=n=>{{for(const c of n.children){{if(s.split(',').some(a=>c.matches(a.trim())))out.push(c);walk(c);}}}}; walk(this); return out; }} querySelector(s) {{ return this.querySelectorAll(s)[0]||null; }} getBoundingClientRect() {{ return this.rect||{{left:0,right:100,top:0,bottom:100}}; }}
}}
class Area extends E {{ constructor(){{super('textarea');this.value='';this.selectionStart=0;this.selectionEnd=0;this.scrollHeight=40;}} }}
class Sel extends E {{constructor(){{super('select');}}}}
globalThis.Element=E; globalThis.Node=E; globalThis.HTMLTextAreaElement=Area; globalThis.HTMLSelectElement=Sel;
globalThis.CSS={{escape:s=>String(s)}};
const document={{activeElement:null,cookie:'',body:new E('body'),createElement(t){{return t==='textarea'?new Area():t==='select'?new Sel():new E(t);}},createElementNS(_n,t){{return new E(t);}},querySelector(){{return null;}},addEventListener(){{}},removeEventListener(){{}}}}; globalThis.document=document;
globalThis.window={{confirm:()=>true, requestAnimationFrame:fn=>fn(), addEventListener(){{}}, getComputedStyle:()=>({{}})}};
const {{createSplitReviewController}}=await import('{module_uri}');
const tick=async(n=10)=>{{for(let i=0;i<n;i++)await new Promise(r=>setTimeout(r,0));}};
const flush=async()=>{{await tick(8);}};
const root=new E('div'), modal=new E('div'), list=new E('div'), status=new E('p'), count=new E('p'), create=new E('button'), add=new E('button'), suggest=new E('button'), ignored=new E('button'), pop=new E('div'), dialog=new E('div');
modal.querySelector=(s)=>s==='.split-review-modal__dialog'?dialog:(s==='[data-split-review-loading]'?loading:null);
const loading=new E('div'); list.rect={{left:0,right:600,top:0,bottom:600}}; add.dataset.splitReviewAdd=''; list.append(add); ignored.dataset.splitReviewIgnored=''; pop.dataset.splitReviewIgnoredPopover='';
let writes=0, writeBody=null, releaseSave=null, suggestRelease=null, createCalls=0, holdSave=false, saveFailureStatus=null;
const template={{id:'tpl-primary',name:'Primary'}};

// A committed PUT whose response body cannot be read must be retried with its
// original idempotency key before any newer local snapshot is submitted.
const ambiguousModal=new E('div'), ambiguousList=new E('div'), ambiguousStatus=new E('p'), ambiguousCreate=new E('button');
ambiguousModal.querySelector=(s)=>s==='[data-split-review-loading]'?new E('div'):null;
let ambiguousPlan=null, ambiguousJsonReject=true, ambiguousMalformedOnce=true; const ambiguousBodies=[], ambiguousCommits=new Map();
const ambiguousController=createSplitReviewController({{trigger:new E('button'),modal:ambiguousModal,topicList:ambiguousList,status:ambiguousStatus,createButton:ambiguousCreate,closeButtons:[],getTranscriptId:()=> 'tx-ambiguous',getSelectedTemplateId:()=>template.id,
  fetcher:async(_url,opts)=>{{const body=JSON.parse(opts.body);ambiguousBodies.push(body);let committed=ambiguousCommits.get(body.operation_id);if(!committed){{const currentRevision=ambiguousPlan?.revision||1;if(body.expected_revision!==currentRevision)return {{ok:false,status:409,json:async()=>({{error:{{code:'consultation_problem_plan_conflict'}}}})}};committed={{plan_id:'plan-ambiguous',revision:currentRevision+1,manual_override:true,tiles:body.tiles,problems:body.problems,suggestion:null}};ambiguousCommits.set(body.operation_id,committed);ambiguousPlan=committed;}}return {{ok:true,status:200,json:async()=>{{if(ambiguousJsonReject){{ambiguousJsonReject=false;throw new Error('response body unavailable');}}if(ambiguousMalformedOnce){{ambiguousMalformedOnce=false;return null;}}return committed;}}}};}} ,
  refreshWorkspace:async()=>({{available_templates:[template]}})}});
ambiguousController.applyWorkspaceState({{problemPlan:{{plan_id:'plan-ambiguous',revision:1,manual_override:false,tiles:[{{uuid:'amb-primary',is_primary:true,template_id:template.id}}],problems:[{{uuid:'amb-problem',title:'Initial finding',origin:'manual',ignored:false,group_id:'amb-primary'}}]}},problemPlanProjectionAvailable:true,availableTemplates:[template],nextCapabilityAvailable:true,nextTranscriptId:'tx-ambiguous'}});
ambiguousController.open(); const ambiguousTitle=ambiguousList.querySelector('[data-problem-title]');
ambiguousTitle.value='Edited finding'; ambiguousTitle.fire('input');
await new Promise(r=>setTimeout(r,700)); await flush();
if(ambiguousBodies.length!==1||!ambiguousStatus.textContent.includes('edits are preserved'))throw new Error('unreadable committed save response did not preserve pending state');
const ambiguousOriginalBody=JSON.stringify(ambiguousBodies[0]);
ambiguousTitle.value='Edited while save outcome was unknown'; ambiguousTitle.fire('input');
await new Promise(r=>setTimeout(r,700)); await flush();
if(ambiguousBodies.length!==2||JSON.stringify(ambiguousBodies[1])!==ambiguousOriginalBody||!ambiguousStatus.textContent.includes('edits are preserved'))throw new Error('malformed successful save response discarded its pending operation');
ambiguousTitle.value='Edited again while save outcome was unknown'; ambiguousTitle.fire('input');
await new Promise(r=>setTimeout(r,700)); await flush();
if(ambiguousBodies.length!==4||ambiguousBodies.slice(0,3).some(body=>JSON.stringify(body)!==ambiguousOriginalBody)
  ||JSON.stringify(ambiguousBodies[2])!==ambiguousOriginalBody||ambiguousBodies[3].expected_revision!==2||ambiguousBodies[3].problems[0].title!=='Edited again while save outcome was unknown'
  ||ambiguousController.getPlan().problems[0].title!=='Edited again while save outcome was unknown'||ambiguousController.getPlan().revision!==3||ambiguousController.isDirty())
  throw new Error('ambiguous save retry did not replay the committed operation and retain the local edit: '+JSON.stringify({{bodies:ambiguousBodies,plan:ambiguousController.getPlan(),dirty:ambiguousController.isDirty(),status:ambiguousStatus.textContent}}));

const initial={{plan_id:'plan-1',revision:1,status:'active',manual_override:false,tiles:[{{uuid:'tile-primary',is_primary:true,template_id:'tpl-primary'}},{{uuid:'tile-b',is_primary:false,template_id:'tpl-other'}}],problems:[{{uuid:'p1',title:'Knee pain',origin:'suggested',ignored:false,group_id:'tile-primary'}},{{uuid:'p2',title:'Blood pressure',origin:'suggested',ignored:false,group_id:'tile-b'}}],suggestion:null}};
let currentTranscript='tx1';
const c=createSplitReviewController({{trigger:new E('button'),modal,topicList:list,status,problemCount:count,createButton:create,addButton:add,suggestButton:suggest,ignoredButton:ignored,ignoredPopover:pop,closeButtons:[],getTranscriptId:()=>currentTranscript,getSelectedTemplateId:()=>template.id,
  fetcher:async(_url,opts)=>{{writes++;writeBody=JSON.parse(opts.body);const persisted={{plan_id:'plan-1',revision:writes+1,manual_override:true,tiles:writeBody.tiles,problems:writeBody.problems,suggestion:null}};if(holdSave)return await new Promise(r=>{{releaseSave=()=>r({{ok:true,status:200,json:async()=>persisted}});}});if(saveFailureStatus)return {{ok:false,status:saveFailureStatus,json:async()=>({{error:{{code:saveFailureStatus===409?'consultation_problem_plan_conflict':'temporary_failure'}}}})}};return {{ok:true,status:200,json:async()=>persisted}};}},
  refreshWorkspace:async()=>({{available_templates:[template,{{id:'tpl-other',name:'Other'}}]}}),
  createNoteBatch:async()=>{{createCalls++;return {{status:'generation_queued',batch_id:'batch-1'}};}},
  suggestProblems:async()=>await new Promise(r=>{{suggestRelease=r;}}),
}});
c.applyWorkspaceState({{problemPlan:initial,problemPlanProjectionAvailable:true,availableTemplates:[template,{{id:'tpl-other',name:'Other'}}],nextCapabilityAvailable:true,nextSourceReady:true,nextTranscriptId:'tx1'}});
c.open(); await flush();
const by=(selector,scope=list)=>scope.querySelector(selector), all=(selector,scope=list)=>scope.querySelectorAll(selector);
const title=(key)=>all('[data-problem-title]').find(x=>x.dataset.problemKey===key);
if(!c.isOpen()||writes!==0)throw new Error('opening Review performed a plan write');
if(create.disabled) throw new Error('transcript-only Primary plan should enable Create: '+JSON.stringify({{plan:c.getPlan(),status:status.textContent,count:create.textContent,templates:template}}));
// Editing title is allowed but Create is blocked until autosave succeeds.
const p1=title('p1'); p1.value='Knee symptoms'; p1.fire('input'); holdSave=true;
if(!create.disabled) throw new Error('Create remained enabled with an unsaved title edit');
await new Promise(r=>setTimeout(r,700)); await flush();
if(writes<1||!create.disabled)throw new Error('title edit did not autosave/block Create during save: '+JSON.stringify({{writes,disabled:create.disabled,plan:c.getPlan(),status:status.textContent}}));
releaseSave(); holdSave=false; await flush(); if(create.disabled)throw new Error('Create stayed disabled after successful save');
if(!c.getPlan().manual_override)throw new Error('first saved manual edit did not switch the consultation to manual control');
if(status.textContent)throw new Error('successful autosave exposed routine status text');
const savedTitleField=title('p1'); savedTitleField.value='Knee symptoms, updated'; savedTitleField.fire('input'); saveFailureStatus=500;
await new Promise(r=>setTimeout(r,700)); await flush();
if(!create.disabled||c.getPlan().problems.find(p=>p.uuid==='p1').title!=='Knee symptoms, updated'||!status.textContent.includes('edits are preserved'))throw new Error('failed autosave lost edits or left Create enabled');
saveFailureStatus=null; await c.save(); await flush();
if(create.disabled||status.textContent)throw new Error('successful retry did not clear save error and reenable Create');
// Manual edit makes the plan manual; move keeps destination template and stable tile identity.
let move=all('[data-problem-move]').find(x=>x.dataset.problemKey==='p1'); move.value='tile-b'; move.fire('change'); await flush();
if(c.getPlan().problems.find(p=>p.uuid==='p1').group_id!=='tile-b')throw new Error('problem did not move to arbitrary destination');
if(c.getPlan().tiles.find(t=>t.uuid==='tile-b').template_id!=='tpl-other')throw new Error('move changed destination template');
// Primary promotion reorders the same tile object identity and keeps assignments.
const bTile=all('.split-review-tile').find(x=>x.dataset.tileUuid==='tile-b');
const promote=all('[data-tile-action="make-primary"]',bTile)[0]; promote.click();
if(c.getPlan().tiles[0].uuid!=='tile-b'||!c.getPlan().tiles[0].is_primary||c.getPlan().problems.find(p=>p.uuid==='p1').group_id!=='tile-b')throw new Error('primary promotion did not preserve tile identity and assignments');
// Ignore is recoverable; Delete removes routing assignment and falls back to Primary.
const ignoredAction=all('[data-problem-action="ignore"]').find(x=>x.dataset.problemKey==='p2'); ignoredAction.click();
if(!c.getPlan().problems.find(p=>p.uuid==='p2').ignored||c.getPlan().problems.find(p=>p.uuid==='p2').group_id!==null)throw new Error('Ignore did not exclude problem while retaining it');
ignored.click(); const restore=all('[data-problem-action="restore"]',pop).find(x=>x.dataset.problemKey==='p2'); restore.click();
if(c.getPlan().problems.find(p=>p.uuid==='p2').group_id!=='tile-b')throw new Error('Restore failed to return problem to existing tile');
all('[data-problem-action="delete"]').find(x=>x.dataset.problemKey==='p2').click();
if(c.getPlan().problems.some(p=>p.uuid==='p2'))throw new Error('Delete retained explicit problem assignment');
// Explicit suggest preserves ignored exclusions and locks editing while request is in flight.
const ignoredProblem=c.getPlan().problems.find(p=>p.uuid==='p1');
const ignoredButton=all('[data-problem-action="ignore"]').find(x=>x.dataset.problemKey==='p1'); ignoredButton.click();
const ignoredBefore=c.getPlan().problems.find(p=>p.uuid==='p1');
suggest.click(); await flush();
if(!c.isSuggestionRunning())throw new Error('Auto suggest did not enter in-flight state');
const lockedTitle=all('[data-problem-title]')[0]; if(lockedTitle&&!lockedTitle.disabled)throw new Error('suggestion did not lock problem controls');
const beforeSuggestionFailure=c.getPlan();
suggestRelease({{...beforeSuggestionFailure,suggestion:{{status:'failed'}}}}); await flush();
if(c.isSuggestionRunning()||!c.getPlan().problems.some(p=>p.ignored))throw new Error('suggestion failure lost existing ignored exclusion');
// A status error remains actionable, while routine status stays quiet.
if(status.textContent&&!status.textContent.toLowerCase().includes('could not'))throw new Error('routine status leaked after terminal suggestion');
// Add note copies Primary's template. Rapidly titled temporary rows must all become saveable.
list.querySelector('[data-split-review-add]').click(); const temp=c.getPlan().tiles.find(t=>t._temporary), blank=c.getPlan().problems.find(p=>p._temporary);
if(!temp||temp.template_id!=='tpl-other'||!blank||!create.disabled)throw new Error('temporary note did not inherit current Primary template or guard Create');
const blankField=title(blank.uuid), blankIgnore=all('[data-problem-action="ignore"]').find(x=>x.dataset.problemKey===blank.uuid);
if(!blankField||blankField.disabled||!blankIgnore?.disabled)throw new Error('blank title was not editable with Ignore disabled');
await c.close();
if(c.getPlan().tiles.some(t=>t._temporary)||c.getPlan().problems.some(p=>p._temporary))throw new Error('closing Review retained an empty temporary note tile');
c.open(); await flush();
list.querySelector('[data-split-review-add]').click();
all('[data-tile-action="add-problem"]').find(x=>x.dataset.tileKey==='tile-b').click();
all('[data-tile-action="add-problem"]').find(x=>x.dataset.tileKey==='tile-b').click();
const temporaryFields=all('[data-problem-title]').filter(x=>c.getPlan().problems.find(p=>p.uuid===x.dataset.problemKey)?._temporary);
if(temporaryFields.length!==3)throw new Error('expected one temporary note row and two added problem rows');
temporaryFields.forEach((field,index)=>{{field.value=`Synthetic addition ${{index+1}}`;field.fire('input');}});
await new Promise(r=>setTimeout(r,700)); await flush();
if(c.getPlan().problems.some(p=>p._temporary)||c.getPlan().tiles.some(t=>t._temporary))throw new Error('rapid title debounce promoted only the last temporary row');
// Ignored problems count toward the six-problem limit.
all('[data-tile-action="add-problem"]').find(x=>x.dataset.tileKey==='tile-b').click();
all('[data-tile-action="add-problem"]').find(x=>x.dataset.tileKey==='tile-b').click();
const lastTwo=all('[data-problem-title]').filter(x=>c.getPlan().problems.find(p=>p.uuid===x.dataset.problemKey)?._temporary);
lastTwo.forEach((field,index)=>{{field.value=`Synthetic final ${{index+1}}`;field.fire('input');}});
await new Promise(r=>setTimeout(r,700)); await flush();
if(c.getPlan().problems.length!==6||all('[data-tile-action="add-problem"]').some(button=>!button.disabled))throw new Error('Add problem allowed more than six entries including ignored problems: '+JSON.stringify({{count:c.getPlan().problems.length,buttons:all('[data-tile-action="add-problem"]').map(b=>b.disabled),plan:c.getPlan()}}));
const addNoteAtLimit=list.querySelector('[data-split-review-add]'), tileCountAtLimit=all('.split-review-tile').length;
if(!addNoteAtLimit.disabled)throw new Error('Add note was not disabled at the six-problem limit');
list.fire('click',{{target:addNoteAtLimit}});
if(all('.split-review-tile').length!==tileCountAtLimit)throw new Error('Add note created an orphan tile at the problem limit');
const draggable=all('[data-problem-drag-handle]').find(handle=>handle.draggable);
const transfer={{setData(){{}},setDragImage(){{}}}};
draggable.fire('dragstart',{{clientX:10,clientY:10,dataTransfer:transfer}});
if(list.querySelector('[data-split-review-add]').disabled)throw new Error('six-problem limit prevented existing problems from dragging to a new note');
list.fire('dragend');
if(!list.querySelector('[data-split-review-add]').disabled)throw new Error('Add note did not return to disabled after drag cancellation');
const routeKey=c.getPlan().problems.find(problem=>!problem.ignored&&problem.group_id==='tile-b').uuid;
const moveAtLimit=all('[data-problem-move]').find(select=>select.dataset.problemKey===routeKey);
moveAtLimit.value='__new_note__'; moveAtLimit.fire('change'); await flush();
if(c.getPlan().problems.length!==6||all('.split-review-tile').length!==tileCountAtLimit+1)throw new Error('moving an existing problem to a new note failed at six problems');
// A submitted batch locks only Create; live plan fields remain editable.
c.applyWorkspaceState({{batch:{{status:'generation_queued'}},problemPlan:c.getPlan(),problemPlanProjectionAvailable:true,availableTemplates:[template,{{id:'tpl-other',name:'Other'}}],nextTranscriptId:'tx1'}});
if(!create.disabled)throw new Error('active batch left Create enabled');
const editableDuringBatch=all('[data-problem-title]').find(x=>!x.disabled);
if(!editableDuringBatch)throw new Error('active batch locked plan editing');
editableDuringBatch.value='Edited while batch is running'; editableDuringBatch.fire('input');
if(!create.disabled)throw new Error('Create reenabled while batch was running with unsaved edits');
saveFailureStatus=409;
await new Promise(r=>setTimeout(r,700)); await flush();
if(!create.disabled||!status.textContent.includes('changed elsewhere')||!c.getPlan().problems.some(p=>p.title==='Edited while batch is running'))throw new Error('plan conflict did not preserve edits and keep Create blocked: '+JSON.stringify({{disabled:create.disabled,status:status.textContent,plan:c.getPlan()}}));
// An untouched virtual Primary plan plus a blank temporary tile is still a no-op.
const modal2=new E('div'), list2=new E('div'), status2=new E('p'), create2=new E('button'); let emptyPlanWrites=0;
modal2.querySelector=(s)=>s==='[data-split-review-loading]'?new E('div'):null;
const emptyController=createSplitReviewController({{trigger:new E('button'),modal:modal2,topicList:list2,status:status2,createButton:create2,closeButtons:[],getTranscriptId:()=> 'tx-virtual',getSelectedTemplateId:()=> 'tpl-primary',refreshWorkspace:async()=>({{available_templates:[template]}}),fetcher:async()=>{{emptyPlanWrites++;return {{ok:false,status:422,json:async()=>({{}})}};}}}});
emptyController.applyWorkspaceState({{nextTranscriptId:'tx-virtual',nextCapabilityAvailable:true,nextSourceReady:true,problemPlanProjectionAvailable:true,availableTemplates:[template]}});
emptyController.open(); await flush(); list2.querySelector('[data-split-review-add]').click();
const ephemeral=list2.querySelector('[data-problem-title]'); ephemeral.value='Brief title'; ephemeral.fire('input'); ephemeral.value=''; ephemeral.fire('input');
await new Promise(r=>setTimeout(r,700)); await flush();
await emptyController.close();
if(emptyPlanWrites!==0||emptyController.isOpen()||emptyController.getPlan().tiles.filter(t=>!t._temporary).length!==0)throw new Error('erasing a temporary problem on the first unsaved plan attempted an empty-plan write or blocked close: '+JSON.stringify({{writes:emptyPlanWrites,open:emptyController.isOpen(),plan:emptyController.getPlan(),status:status2.textContent}}));

// The untouched virtual Primary placeholder must accept the first server plan.
const modal3=new E('div'), list3=new E('div'), create3=new E('button');
modal3.querySelector=(s)=>s==='[data-split-review-loading]'?new E('div'):null;
const incomingController=createSplitReviewController({{trigger:new E('button'),modal:modal3,topicList:list3,status:new E('p'),createButton:create3,closeButtons:[],getTranscriptId:()=> 'tx-incoming',getSelectedTemplateId:()=> 'tpl-primary',refreshWorkspace:async()=>({{available_templates:[template,{{id:'tpl-other',name:'Other'}}]}}),fetcher:async()=>({{ok:true,status:200,json:async()=>({{}})}})}});
incomingController.applyWorkspaceState({{nextTranscriptId:'tx-incoming',nextCapabilityAvailable:true,nextSourceReady:true,problemPlanProjectionAvailable:true,availableTemplates:[template,{{id:'tpl-other',name:'Other'}}]}});
if(incomingController.getPlan().tiles.length!==1||!incomingController.getPlan().tiles[0]._temporary)throw new Error('initial workspace did not create the expected virtual Primary');
const generatedPlan={{plan_id:'plan-generated',revision:1,status:'active',manual_override:false,tiles:[{{uuid:'primary-generated',is_primary:true,template_id:'tpl-primary'}},{{uuid:'secondary-generated',is_primary:false,template_id:'tpl-other'}}],problems:[{{uuid:'generated-1',title:'Synthetic primary finding',origin:'suggested',ignored:false,group_id:'primary-generated'}},{{uuid:'generated-2',title:'Synthetic secondary finding',origin:'suggested',ignored:false,group_id:'secondary-generated'}}],suggestion:{{status:'ready'}}}};
incomingController.applyWorkspaceState({{problemPlan:generatedPlan,problemPlanProjectionAvailable:true}});
incomingController.open(); await flush();
if(incomingController.getPlan().tiles.length!==2||incomingController.getPlan().problems.length!==2||create3.disabled)throw new Error('first server plan did not replace untouched virtual Primary or enable Create: '+JSON.stringify({{plan:incomingController.getPlan(),disabled:create3.disabled}}));

// Partial workspace updates preserve omitted template and active-batch state.
const modal4=new E('div'), list4=new E('div'), create4=new E('button');
modal4.querySelector=(s)=>s==='[data-split-review-loading]'?new E('div'):null;
const partialController=createSplitReviewController({{trigger:new E('button'),modal:modal4,topicList:list4,status:new E('p'),createButton:create4,closeButtons:[],getTranscriptId:()=> 'tx-partial',getSelectedTemplateId:()=> 'tpl-primary',refreshWorkspace:async()=>({{available_templates:[template,{{id:'tpl-other',name:'Other'}}]}}),fetcher:async()=>({{ok:true,status:200,json:async()=>({{}})}})}});
partialController.applyWorkspaceState({{problemPlan:initial,problemPlanProjectionAvailable:true,availableTemplates:[template,{{id:'tpl-other',name:'Other'}}],batch:{{status:'generation_queued'}},nextCapabilityAvailable:true,nextTranscriptId:'tx-partial'}});
partialController.open(); await flush();
partialController.applyWorkspaceState({{problemPlan:initial,problemPlanProjectionAvailable:true}});
if(!create4.disabled)throw new Error('partial workspace update cleared the active batch and enabled Create');
const templateSelect4=list4.querySelector('[data-tile-template]');
if(!templateSelect4?.children.some(option=>option.value==='tpl-other'))throw new Error('partial workspace update cleared available templates');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"}, timeout=30)
