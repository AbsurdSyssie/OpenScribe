import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_split_review_workspace_accessibility_hooks_and_cachebusters():
    workspace = (ROOT / "app/templates/transcribe/_workspace.html").read_text()
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text()
    shell = (ROOT / "app/templates/transcribe/_shell_extras.html").read_text()
    head = (ROOT / "app/templates/transcribe/_head_assets.html").read_text()

    assert 'data-split-review-trigger hidden aria-haspopup="dialog"' in workspace
    assert 'data-split-review-modal hidden' in workspace
    assert 'role="dialog" aria-modal="true"' in workspace
    assert 'data-split-review-status role="status" aria-live="polite"' in workspace
    assert 'Review note split' in workspace
    assert 'Review later' in workspace
    assert 'splitReview.js?v=20260911-direct-batch-regeneration' in app_js
    assert 'splitReviewController?.applyWorkspaceState' in app_js
    assert 'app.js?v=20260911-direct-batch-regeneration' in shell
    assert 'transcribe.css?v=20260911-note-regeneration-layer-fix' in head
    assert 'data-split-continue-one-note hidden' in workspace
    assert 'data-split-review-continue hidden disabled' in workspace
    assert 'data-split-retry-missing hidden' in workspace
    assert 'data-split-keep-available hidden' in workspace
    assert 'id="split-partial-status"' in workspace


def test_split_review_serialization_preserves_uuid_and_primary_rule(tmp_path):
    runner = tmp_path / "split-review-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ normalizeSplitDraft, serializeSplitDraft, validateSplitDraft }} = await import('{module_uri}');
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
if (calls.length !== 3) throw new Error('poller started a second in-flight refresh');
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
let saves = [], calls = [], statuses = [], busy = [], opens = 0, applies = 0, keyNo = 0;
let draftResponse = null, intentResponse = {{ status: 202, ok: true, json: async () => ({{ analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}) }};
const fetcher = async (url, options) => {{ calls.push([url, options?.method]); if (url.includes('intents')) return intentResponse; return draftResponse; }};
const reviewController = {{ applyWorkspaceState: () => {{ applies += 1; }}, open: () => {{ opens += 1; }} }};
const controller = createSplitGenerateController({{
  fetcher, getCapabilityEnabled: () => enabled, getTranscriptId: () => transcriptId, getTemplateId: () => templateId,
  saveSources: async () => {{ saves.push('working'); saves.push('dictation'); }}, refreshWorkspace: async () => null,
  reviewController, setBusy: (value) => busy.push(value), setStatus: (message) => statuses.push(message),
  createKey: () => `00000000-0000-0000-0000-${{String(++keyNo).padStart(12, '0')}}`,
}});
if (await controller.start()) throw new Error('disabled gate intercepted ordinary Generate');
enabled = true;
await controller.start();
if (saves.join() !== 'working,dictation' || calls.length !== 1 || !calls[0][0].includes('/consultation-split-intents')) throw new Error('sources were not saved before intent');
if (calls.some(([url]) => url.includes('/generate-output'))) throw new Error('split path called generate-output');
await controller.start();
if (calls.length !== 1 || keyNo !== 1) throw new Error('duplicate Create started a second operation');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'processing' }} }});
if (!statuses.at(-1).includes('Preparing')) throw new Error('processing state was not persistent');
draftResponse = {{ ok: true, json: async () => ({{ draft_id: 'draft-1' }}) }};
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft: null }});
await Promise.resolve(); await Promise.resolve();
if (calls.filter(([url]) => url.includes('consultation-split-draft')).length !== 1) throw new Error('ready draft was not initialized');
const draft = {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }};
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
if (opens !== 1 || applies !== 1) throw new Error('current ready draft did not open exactly once');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'not_required' }} }});
if (!statuses.at(-1).includes('No note generated')) throw new Error('terminal status lacked safe guidance');
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


def test_split_review_continue_control_requires_current_browser_intent(tmp_path):
    runner = tmp_path / "split-review-continue-visibility-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitGenerateController, createSplitReviewController }} = await import('{module_uri}');
class Button {{
  constructor() {{ this.hidden = false; this.disabled = false; this.listeners = new Map(); }}
  addEventListener(type, callback) {{ this.listeners.set(type, callback); }}
  click() {{ this.listeners.get('click')?.(); }}
}}
const draft = {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [] }};
const passiveButton = new Button();
let passiveConsumes = 0;
const passiveReview = createSplitReviewController({{
  continueButton: passiveButton, continueAsOneNote: async () => {{ passiveConsumes += 1; return true; }},
}});
passiveReview.applyWorkspaceState({{ draft, nextTranscriptId: 'tx-1' }});
if (!passiveButton.hidden || !passiveButton.disabled) throw new Error('restored draft exposed one-note action');
passiveButton.click(); await Promise.resolve();
if (passiveConsumes !== 0) throw new Error('restored draft click consumed an arbitrary intent');

const currentButton = new Button();
let transcriptId = 'tx-1', resolveConsume, consumeCalls = 0;
let currentReview;
const controller = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => transcriptId, getTemplateId: () => 'template-1',
  createKey: () => 'key-1', saveSources: async () => {{}},
  setContinueAvailable: (available) => currentReview.setContinueAvailable(available),
  fetcher: async (url) => {{
    if (url.endsWith('/consultation-split-intents')) {{
      return {{ ok: true, json: async () => ({{ intent_id: 'intent-1', analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}) }};
    }}
    consumeCalls += 1;
    return new Promise((resolve) => {{ resolveConsume = () => resolve({{ ok: true, json: async () => ({{ document: {{ id: 'doc-1' }} }}) }}); }});
  }},
}});
currentReview = createSplitReviewController({{
  continueButton: currentButton, continueAsOneNote: () => controller.continueAsOneNote(),
}});
await controller.start();
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'ready' }}, draft }});
if (currentButton.hidden || currentButton.disabled) throw new Error('current browser review did not expose one-note action');
currentButton.click(); currentButton.click();
if (consumeCalls !== 1) throw new Error('current review continue was not single-flight');
resolveConsume(); await Promise.resolve(); await Promise.resolve();
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


def test_split_review_controller_uses_put_conflict_reload_and_dirty_reconciliation():
    controller = (ROOT / "app/static/js/transcribe/splitReview.js").read_text()
    assert "method: 'PUT'" in controller
    assert "expected_updated_at" in controller
    assert "your edits were not merged" in controller
    assert "await refreshWorkspace();" in controller
    assert "incoming.status !== ACTIVE_STATUS" in controller
    assert "window.confirm('Discard unsaved changes and review this split later?')" in controller


def test_split_review_controller_fake_dom_harness_covers_edit_reconcile_validation_and_conflicts(tmp_path):
    runner = tmp_path / "split-review-controller-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
class FakeClassList {{ constructor() {{ this.values = new Set(); }} add(value) {{ this.values.add(value); }} remove(value) {{ this.values.delete(value); }} }}
const dataName = (name) => name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
class FakeElement {{
  constructor(tag = 'div') {{ this.tagName = tag.toUpperCase(); this.children = []; this.attrs = new Map(); this.dataset = {{}}; this.listeners = new Map(); this.hidden = false; this.disabled = false; this.value = ''; this.checked = false; this.classList = new FakeClassList(); this.ownerDocument = documentTarget; }}
  setAttribute(name, value) {{ this.attrs.set(name, String(value)); if (name.startsWith('data-')) this.dataset[dataName(name)] = String(value); }}
  getAttribute(name) {{ return this.attrs.get(name) ?? null; }}
  hasAttribute(name) {{ return this.attrs.has(name) || (name.startsWith('data-') && this.dataset[dataName(name)] !== undefined); }}
  append(...nodes) {{ nodes.forEach((node) => {{ node.parentNode = this; this.children.push(node); }}); }}
  replaceChildren(...nodes) {{ this.children.forEach((node) => {{ node.parentNode = null; }}); this.children = [...nodes]; this.children.forEach((node) => {{ node.parentNode = this; }}); }}
  addEventListener(type, callback) {{ if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }}
  fire(type, extra = {{}}, event = null) {{
    const currentEvent = event || {{ target: extra.target || this, key: extra.key, shiftKey: extra.shiftKey, preventDefault() {{}} }};
    for (const callback of this.listeners.get(type) || []) callback(currentEvent);
    this.parentNode?.fire(type, extra, currentEvent);
  }}
  click() {{ this.fire('click'); }}
  focus() {{ documentTarget.activeElement = this; }}
  querySelectorAll(selector) {{
    const selectors = selector.split(',').map((item) => item.trim());
    const all = []; const visit = (node) => {{ for (const child of node.children || []) {{ if (child.matches?.(selectors)) all.push(child); visit(child); }} }};
    visit(this); return all;
  }}
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  matches(selectors) {{
    if (Array.isArray(selectors)) return selectors.some((selector) => this.matches(selector));
    const tag = selectors.match(/^[a-z]+/i)?.[0]; if (tag && this.tagName !== tag.toUpperCase()) return false;
    for (const attr of selectors.matchAll(/\\[([^=\\]]+)(?:=["']?([^\\]"']+)["']?)?\\]/g)) {{
      const name = attr[1]; const expected = attr[2];
      if (!this.hasAttribute(name) && !(name.startsWith('data-') && this.dataset[dataName(name)] !== undefined)) return false;
      if (expected && String(this.getAttribute(name) ?? this.dataset[dataName(name)]) !== expected) return false;
    }}
    return true;
  }}
  closest(selector) {{ return selector === '[hidden]' && this.hidden ? this : null; }}
}}
class FakeInput extends FakeElement {{ constructor() {{ super('input'); }} }}
class FakeSelect extends FakeElement {{ constructor() {{ super('select'); }} }}
const documentTarget = {{
  activeElement: null, cookie: '',
  body: {{ classList: new FakeClassList() }},
  createElement(tag) {{ return tag === 'input' ? new FakeInput() : (tag === 'select' ? new FakeSelect() : new FakeElement(tag)); }},
  createTextNode(text) {{ return new FakeElement('span'); }},
  focusFallback: null,
  querySelector() {{ return this.focusFallback; }},
}};
globalThis.document = documentTarget; globalThis.Element = FakeElement; globalThis.HTMLInputElement = FakeInput; globalThis.HTMLSelectElement = FakeSelect;
globalThis.window = {{ confirm: () => true, requestAnimationFrame: (callback) => callback() }};
const {{ createSplitReviewController }} = await import('{module_uri}');
const trigger = new FakeElement('button');
const modal = new FakeElement('div');
const topicList = new FakeElement('div');
const status = new FakeElement('p');
const saveButton = new FakeElement('button');
const closeButton = new FakeElement('button');
const calls = []; let refreshPayload = null;
const fetcher = async (_url, options) => {{ calls.push(JSON.parse(options.body)); return {{ status: 200, ok: true, json: async () => refreshPayload || {{ draft_id: 'draft-1', status: 'active', updated_at: 'saved', topics: [] }} }}; }};
const controller = createSplitReviewController({{ trigger, modal, topicList, status, saveButton, closeButtons: [closeButton], fetcher, getTranscriptId: () => 'tx-1', refreshWorkspace: async () => refreshPayload }});
const base = {{ draft_id: 'draft-1', status: 'active', updated_at: 'v1', topics: [
  {{ topic_uuid: 'topic-1', title: 'Main problem', order: 0, is_primary: true, disposition: 'separate_note', template_id: 'tpl-1' }},
  {{ topic_uuid: 'topic-2', title: 'Other problem', order: 1, is_primary: false, disposition: 'exclude_from_notes', template_id: 'tpl-1' }}
] }};
controller.applyWorkspaceState({{ draft: base, availableTemplates: [{{ id: 'tpl-1', name: 'General', latest_version: {{ mode: 'freeform' }} }}], nextTranscriptId: 'tx-1' }});
if (trigger.hidden || trigger.getAttribute('aria-expanded') !== 'false') throw new Error('trigger state');
trigger.click();
if (modal.hidden || trigger.getAttribute('aria-expanded') !== 'true') throw new Error('open state');
const title = topicList.querySelector('input[data-split-review-title]'); title.value = 'Edited title'; title.fire('input');
const primary = topicList.querySelector('input[data-split-review-primary]'); primary.checked = false;
const secondPrimary = topicList.querySelectorAll('input[data-split-review-primary]')[1]; secondPrimary.checked = true; secondPrimary.fire('change');
const template = topicList.querySelector('select[data-split-review-template]'); template.value = 'tpl-1'; template.fire('change');
const serialized = controller.serialize();
if (serialized.topics[0].topic_uuid !== 'topic-1' || serialized.topics[1].is_primary !== true || serialized.topics[1].disposition !== 'separate_note') throw new Error('edit serialization');
closeButton.click();
if (!modal.hidden || trigger.getAttribute('aria-expanded') !== 'false') throw new Error('close state');
trigger.click();
const invalidTitle = topicList.querySelector('input[data-split-review-title]'); invalidTitle.value = '   '; invalidTitle.fire('input');
await controller.save(); if (calls.length !== 0 || !status.textContent.includes('title')) throw new Error('invalid save fetched');
invalidTitle.value = 'Main problem'; invalidTitle.fire('input');
controller.applyWorkspaceState({{ draft: {{ ...base, topics: base.topics.map((topic) => ({{ ...topic, title: topic.title + ' server', updated_at: undefined }})) }} }});
if (topicList.querySelector('input[data-split-review-title]').value !== 'Main problem') throw new Error('dirty SSE clobbered local');
controller.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'draft-2', status: 'active' }} }});
if (controller.getDraft().status !== 'unavailable' || calls.length !== 0) throw new Error('different draft did not fail closed');
refreshPayload = {{ consultation_split_draft: {{ ...base, status: 'stale', updated_at: 'v2' }} }};
let staleController;
const staleCloseButton = new FakeElement('button');
staleController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: new FakeElement('div'), status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [staleCloseButton], fetcher: async () => ({{ status: 409, ok: false, json: async () => ({{ error: {{ code: 'consultation_split_source_stale' }} }}) }}), getTranscriptId: () => 'tx-1', refreshWorkspace: async () => {{ staleController.applyWorkspaceState({{ draft: refreshPayload.consultation_split_draft, nextTranscriptId: 'tx-1' }}); return refreshPayload; }} }});
staleController.applyWorkspaceState({{ draft: base, nextTranscriptId: 'tx-1' }}); staleController.open(); staleController.getDraft().topics[0].title = 'dirty';
await staleController.save(); if (staleController.getDraft().status !== 'stale' || staleController.getRemoteDraft().status !== 'stale') throw new Error('stale conflict handling');
if (staleController.getDraft().status === 'active') throw new Error('stale remained editable');
if (staleCloseButton.disabled) throw new Error('stale modal could not close'); staleCloseButton.click(); if (staleController.isOpen()) throw new Error('stale modal close failed');
const conflictRefresh = {{ consultation_split_draft: {{ ...base, updated_at: 'v2', topics: base.topics.map((topic) => ({{ ...topic, title: `${{topic.title}} server` }})) }} }};
const conflictTopicList = new FakeElement('div');
const conflictModal = new FakeElement('div');
const conflictTrigger = new FakeElement('button');
const conflictStatus = new FakeElement('p');
const conflictSaveButton = new FakeElement('button');
const conflictCalls = [];
let conflictController;
conflictController = createSplitReviewController({{ trigger: conflictTrigger, modal: conflictModal, topicList: conflictTopicList, status: conflictStatus, saveButton: conflictSaveButton, closeButtons: [], fetcher: async (_url, options) => {{ conflictCalls.push(JSON.parse(options.body)); return {{ status: 409, ok: false, json: async () => ({{ error: {{ code: 'consultation_split_draft_conflict' }} }}) }}; }}, getTranscriptId: () => 'tx-1', refreshWorkspace: async () => {{ conflictController.applyWorkspaceState({{ draft: conflictRefresh.consultation_split_draft, nextTranscriptId: 'tx-1' }}); return conflictRefresh; }} }});
conflictController.applyWorkspaceState({{ draft: base, nextTranscriptId: 'tx-1' }}); conflictController.open();
const conflictTitle = conflictTopicList.querySelector('input[data-split-review-title]'); conflictTitle.value = 'local only'; conflictTitle.fire('input');
await conflictController.save();
if (conflictCalls.length !== 1 || conflictController.getDraft().topics[0].title !== 'Main problem server' || conflictController.getRemoteDraft().topics[0].title !== 'Main problem server') throw new Error('conflict edits merged or latest draft not loaded');
if (conflictController.getDraft().status !== 'active' || !conflictStatus.textContent.includes('changed elsewhere')) throw new Error('conflict did not reload safely');
const unicodeTopicList = new FakeElement('div');
let unicodeCalls = 0;
const unicodeController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: unicodeTopicList, status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [], fetcher: async () => {{ unicodeCalls += 1; throw new Error('duplicate Unicode titles fetched'); }}, getTranscriptId: () => 'tx-1' }});
unicodeController.applyWorkspaceState({{ draft: {{ ...base, topics: [{{ ...base.topics[0], title: 'Alpha' }}, {{ ...base.topics[1], title: 'Beta', is_primary: false }}] }} }}); unicodeController.open();
const unicodeTitles = unicodeTopicList.querySelectorAll('input[data-split-review-title]'); unicodeTitles[0].value = 'Straße'; unicodeTitles[0].fire('input'); unicodeTitles[1].value = 'STRASSE'; unicodeTitles[1].fire('input');
await unicodeController.save(); if (unicodeCalls !== 0 || !unicodeController.getDraft().topics[0].title.includes('Straße')) throw new Error('Unicode duplicate was not rejected locally');
const pendingTopicList = new FakeElement('div');
const pendingModal = new FakeElement('div');
const pendingTrigger = new FakeElement('button');
const pendingStatus = new FakeElement('p');
const pendingSaveButton = new FakeElement('button');
const pendingCloseButton = new FakeElement('button');
let resolvePending;
let pendingCalls = 0;
const pendingResponse = new Promise((resolve) => {{ resolvePending = resolve; }});
const pendingController = createSplitReviewController({{ trigger: pendingTrigger, modal: pendingModal, topicList: pendingTopicList, status: pendingStatus, saveButton: pendingSaveButton, closeButtons: [pendingCloseButton], fetcher: async () => {{ pendingCalls += 1; return pendingResponse; }}, getTranscriptId: () => 'tx-1' }});
pendingController.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'pending-draft' }}, nextTranscriptId: 'tx-1' }}); pendingController.open();
const pendingTitle = pendingTopicList.querySelector('input[data-split-review-title]'); pendingTitle.value = 'Pending local'; pendingTitle.fire('input');
const pendingSave = pendingController.save(); if (!pendingSaveButton.disabled || !pendingCloseButton.disabled || !pendingTitle.disabled) throw new Error('save did not lock modal controls');
pendingTitle.value = 'Ignored while saving'; pendingTitle.fire('input'); pendingCloseButton.click(); pendingSaveButton.click();
if (pendingController.isOpen() !== true || pendingController.getDraft().topics[0].title !== 'Pending local' || pendingCalls !== 1) throw new Error('pending save accepted edit, close, or duplicate');
resolvePending({{ status: 200, ok: true, json: async () => ({{ ...base, draft_id: 'pending-draft', status: 'active', updated_at: 'saved' }}) }}); await pendingSave;
if (!pendingSaveButton.disabled || pendingCloseButton.disabled || pendingTopicList.querySelector('input[data-split-review-title]').disabled) throw new Error('save controls did not restore');
const errorTopicList = new FakeElement('div');
const errorCloseButton = new FakeElement('button');
const errorSaveButton = new FakeElement('button');
const errorController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: errorTopicList, status: new FakeElement('p'), saveButton: errorSaveButton, closeButtons: [errorCloseButton], fetcher: async () => ({{ status: 500, ok: false, json: async () => ({{}}) }}), getTranscriptId: () => 'tx-1' }});
errorController.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'error-draft' }}, nextTranscriptId: 'tx-1' }}); errorController.open(); errorController.getDraft().topics[0].title = 'error';
await errorController.save(); if (errorCloseButton.disabled || errorTopicList.querySelector('input[data-split-review-title]').disabled || !errorSaveButton.disabled) throw new Error('save controls did not restore after error');
let failedRefreshCalls = 0;
const failedRefreshController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: new FakeElement('div'), status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [], fetcher: async () => ({{ status: 409, ok: false, json: async () => ({{ error: {{ code: 'consultation_split_draft_unavailable' }} }}) }}), getTranscriptId: () => 'tx-1', refreshWorkspace: async () => {{ failedRefreshCalls += 1; return null; }} }});
failedRefreshController.applyWorkspaceState({{ draft: base, nextTranscriptId: 'tx-1' }}); failedRefreshController.open(); failedRefreshController.getDraft().topics[0].title = 'failed refresh';
await failedRefreshController.save(); if (failedRefreshCalls !== 1 || failedRefreshController.getDraft().status !== 'unavailable' || failedRefreshController.getRemoteDraft().status !== 'unavailable') throw new Error('409 did not fail closed before failed refresh');
failedRefreshController.close({{ force: true }}); failedRefreshController.open(); if (failedRefreshController.getDraft().status !== 'unavailable') throw new Error('failed refresh reopened as editable');
const forcedCloseTrigger = new FakeElement('button');
const forcedCloseController = createSplitReviewController({{ trigger: forcedCloseTrigger, modal: new FakeElement('div'), topicList: new FakeElement('div'), status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [], getTranscriptId: () => 'tx-1' }});
const focusFallback = new FakeElement('button'); documentTarget.focusFallback = focusFallback;
forcedCloseController.applyWorkspaceState({{ draft: base, nextTranscriptId: 'tx-1' }}); forcedCloseTrigger.focus(); forcedCloseController.open(); forcedCloseController.applyWorkspaceState({{ draft: null, nextTranscriptId: null }});
if (documentTarget.activeElement !== focusFallback || !forcedCloseTrigger.hidden) throw new Error('forced close left focus hidden');
let resolveSwitch;
const switchResponse = new Promise((resolve) => {{ resolveSwitch = resolve; }});
const switchTrigger = new FakeElement('button');
const switchController = createSplitReviewController({{ trigger: switchTrigger, modal: new FakeElement('div'), topicList: new FakeElement('div'), status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [], fetcher: async () => switchResponse, getTranscriptId: () => 'tx-1' }});
switchController.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'switch-old' }}, nextTranscriptId: 'tx-1' }}); switchTrigger.focus(); switchController.open(); switchController.getDraft().topics[0].title = 'old save';
const switchSave = switchController.save(); switchController.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'switch-new', updated_at: 'new' }}, nextTranscriptId: 'tx-2' }}); switchController.close({{ force: true }});
resolveSwitch({{ status: 200, ok: true, json: async () => ({{ ...base, draft_id: 'switch-old', updated_at: 'old-saved' }}) }}); await switchSave;
if (switchController.getDraft()?.draft_id !== 'switch-new' || documentTarget.activeElement !== switchTrigger) throw new Error('stale save response resurrected switched draft or focus was lost');
let activeConfirmTranscript = 'tx-confirm'; let resolveConfirm; let confirmCalls = 0;
const confirmResponse = new Promise((resolve) => {{ resolveConfirm = resolve; }});
const confirmController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: new FakeElement('div'),
  status: new FakeElement('p'), saveButton: new FakeElement('button'), confirmButton: new FakeElement('button'),
  closeButtons: [], getTranscriptId: () => activeConfirmTranscript, getConfirmIntentId: () => 'intent-confirm',
  fetcher: async (_url, options) => {{ confirmCalls += 1; const body = JSON.parse(options.body); if (body.intent_id !== 'intent-confirm' || body.expected_updated_at !== 'confirm-v1') throw new Error('confirm body leaked or missed optimistic fields'); return confirmResponse; }},
}});
const confirmDraft = {{ ...base, draft_id: 'confirm-draft', updated_at: 'confirm-v1', topics: base.topics.map((topic) => ({{ ...topic, disposition: 'separate_note' }})) }};
confirmController.applyWorkspaceState({{ draft: confirmDraft, nextTranscriptId: 'tx-confirm' }});
const firstConfirm = confirmController.confirm(); const secondConfirm = confirmController.confirm();
if (confirmCalls !== 1) throw new Error('confirm was not single-flight');
activeConfirmTranscript = 'tx-other'; resolveConfirm({{ status: 202, ok: true, json: async () => ({{ idempotency_replayed: false }}) }});
if (await firstConfirm || await secondConfirm || confirmController.getDraft().status !== 'active') throw new Error('stale transcript confirm response changed draft');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})
