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
    assert 'data-split-review-modal\nhidden' in workspace
    assert 'role="dialog" aria-modal="true"' in workspace
    assert 'data-split-review-status' in workspace
    assert 'role="status"' in workspace
    assert 'aria-live="polite"' in workspace
    assert 'Review note split' in workspace
    assert 'Review later' in workspace
    assert 'data-split-review-problem-count' in workspace
    assert 'data-split-review-add' in workspace
    assert 'data-split-review-create' in workspace
    assert 'splitReview.js?v=20261006-durable-create' in app_js
    assert 'splitReviewController?.applyWorkspaceState' in app_js
    assert 'app.js?v=20261006-durable-create' in shell
    assert 'transcribe.css?v=20261005-multiple-problems' in head
    assert 'data-split-continue-one-note hidden' in workspace
    assert 'data-split-review-confirm' not in workspace
    assert 'data-split-review-continue' in workspace
    assert 'data-multiple-problems-toggle' in workspace
    controller = (ROOT / "app/static/js/transcribe/splitReview.js").read_text()
    assert 'data-split-review-title' in controller
    assert 'data-split-review-primary' not in controller
    assert 'data-split-review-disposition' not in controller
    assert 'data-split-retry-missing hidden' in workspace
    assert 'data-split-keep-available hidden' in workspace
    assert 'id="split-partial-status"' in workspace


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
let saves = [], calls = [], statuses = [], busy = [], refreshes = [], opens = 0, applies = 0, keyNo = 0;
let draftResponse = null, intentResponse = {{ status: 202, ok: true, json: async () => ({{ analysis: {{ analysis_id: 'analysis-1', status: 'queued' }} }}) }};
const fetcher = async (url, options) => {{ calls.push([url, options?.method]); if (url.includes('intents')) return intentResponse; return draftResponse; }};
const reviewController = {{ applyWorkspaceState: () => {{ applies += 1; }}, open: () => {{ opens += 1; }} }};
const controller = createSplitGenerateController({{
  fetcher, getCapabilityEnabled: () => enabled, getTranscriptId: () => transcriptId, getTemplateId: () => templateId,
  saveSources: async ({{ transcriptId: sourceTranscriptId }} = {{}}) => {{ saves.push(`working:${{sourceTranscriptId}}`); saves.push(`dictation:${{sourceTranscriptId}}`); }}, refreshWorkspace: async (...args) => {{ refreshes.push(args); return null; }},
  reviewController, setBusy: (value) => busy.push(value), setStatus: (message) => statuses.push(message),
  createKey: () => `00000000-0000-0000-0000-${{String(++keyNo).padStart(12, '0')}}`,
}});
if (await controller.start()) throw new Error('disabled gate intercepted ordinary Generate');
enabled = true;
await controller.start();
if (saves.join() !== 'working:tx-1,dictation:tx-1' || calls.length !== 1 || !calls[0][0].includes('/consultation-split-intents')) throw new Error('sources were not target-bound before intent');
if (calls.some(([url]) => url.includes('/generate-output'))) throw new Error('split path called generate-output');
if (refreshes.length !== 1 || refreshes[0][0] !== transcriptId || refreshes[0][1]?.guardTranscriptId !== transcriptId) throw new Error('accepted queued intent did not refresh the guarded workspace');
await controller.start();
if (calls.length !== 1 || keyNo !== 1) throw new Error('duplicate Create started a second operation');
controller.applyWorkspaceState({{ capabilityEnabled: true, transcriptId, analysis: {{ analysis_id: 'analysis-1', status: 'processing' }} }});
if (!statuses.at(-1).includes('Preparing')) throw new Error('processing state was not persistent');
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


def test_terminal_confirmed_split_starts_editable_review_only_after_explicit_action(tmp_path):
    runner = tmp_path / "split-review-terminal-edit-runner.mjs"
    module_uri = (ROOT / "app/static/js/transcribe/splitReview.js").as_uri()
    runner.write_text(
        f"""
const {{ createSplitReviewController }} = await import('{module_uri}');
let editCalls = 0;
let triggerHandler = null;
let controller;
const confirmed = {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'confirmed', topics: [] }};
controller = createSplitReviewController({{
  trigger: {{
    hidden: false,
    addEventListener: (_, handler) => {{ triggerHandler = handler; }},
    setAttribute: () => {{}},
  }},
  getTranscriptId: () => 'tx-1',
  beginEdit: async () => {{
    editCalls += 1;
    controller.applyWorkspaceState({{
      draft: {{ ...confirmed, status: 'active', updated_at: 'v2' }},
      batch: {{ batch_id: 'batch-1', status: 'ready' }},
      nextTranscriptId: 'tx-1',
    }});
    return true;
  }},
}});
controller.applyWorkspaceState({{
  draft: confirmed,
  batch: {{ batch_id: 'batch-1', status: 'generation_queued' }},
  nextTranscriptId: 'tx-1',
}});
controller.applyWorkspaceState({{
  draft: confirmed,
  batch: {{ batch_id: 'batch-1', status: 'ready' }},
  nextTranscriptId: 'tx-1',
}});
await Promise.resolve(); await Promise.resolve();
if (editCalls !== 0 || controller.getDraft().status !== 'confirmed') throw new Error('terminal state started review without an explicit action');
await triggerHandler();
await Promise.resolve(); await Promise.resolve();
if (editCalls !== 1 || controller.getDraft().status !== 'active') throw new Error('explicit review action did not become editable');
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
const draft = {{ draft_id: 'draft-1', analysis_id: 'analysis-1', status: 'active', topics: [
  {{ topic_uuid: 'topic-1', title: 'Synthetic primary', is_primary: true, disposition: 'separate_note', template_id: 'template-1' }},
  {{ topic_uuid: 'topic-2', title: 'Synthetic secondary', is_primary: false, disposition: 'separate_note', template_id: 'template-1' }},
] }};
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
currentReview.applyWorkspaceState({{ draft, nextTranscriptId: transcriptId }});
currentReview.setContinueAvailable(true);
if (currentButton.hidden || currentButton.disabled) throw new Error('current browser review did not expose one-note action');
currentButton.click(); currentButton.click();
if (consumeCalls !== 1) throw new Error('current review continue was not single-flight');
resolveConsume(); await Promise.resolve(); await Promise.resolve();
let restoredCalls = 0; let restoredDocuments = 0;
const restoredController = createSplitGenerateController({{
  getCapabilityEnabled: () => true, getTranscriptId: () => 'tx-restored',
  fetcher: async (url) => {{
    if (!url.endsWith('/tx-restored/consultation-split-intents/intent-restored/continue-as-one-note')) throw new Error('restored intent targeted the wrong consultation');
    restoredCalls += 1;
    return {{ ok: true, json: async () => ({{ document: {{ id: 'doc-restored' }}, idempotency_replayed: true }}) }};
  }},
  onGeneratedDocument: async () => {{ restoredDocuments += 1; }},
}});
if (!await restoredController.continueAsOneNote({{ transcriptId: 'tx-restored', intentId: 'intent-restored' }})) throw new Error('restored intent was not explicitly consumable');
if (restoredCalls !== 1 || restoredDocuments !== 1) throw new Error('restored intent continuation was not single and visible');
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
  closest(selector) {{ let node = this; while (node) {{ if (node.matches?.(selector)) return node; node = node.parentNode; }} return null; }}
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
const problemCount = new FakeElement('div');
const saveButton = new FakeElement('button');
const createButton = new FakeElement('button');
const addButton = new FakeElement('button');
const continueButton = new FakeElement('button');
const closeButton = new FakeElement('button');
const calls = []; let refreshPayload = null;
const fetcher = async (_url, options) => {{ calls.push(JSON.parse(options.body)); return {{ status: 200, ok: true, json: async () => refreshPayload || {{ draft_id: 'draft-1', status: 'active', updated_at: 'saved', topics: [] }} }}; }};
let continuedOneNote = 0;
const controller = createSplitReviewController({{ trigger, modal, topicList, status, problemCount, saveButton, createButton, addButton, continueButton, closeButtons: [closeButton], fetcher, getTranscriptId: () => 'tx-1', getConfirmIntentId: () => 'intent-1', continueAsOneNote: async () => {{ continuedOneNote += 1; return true; }}, refreshWorkspace: async () => refreshPayload }});
const base = {{ draft_id: 'draft-1', status: 'active', updated_at: 'v1', topics: [
  {{ topic_uuid: 'topic-1', title: 'Main problem', order: 0, is_primary: true, disposition: 'separate_note', template_id: 'tpl-1' }},
  {{ topic_uuid: 'topic-2', title: 'Other problem', order: 1, is_primary: false, disposition: 'exclude_from_notes', template_id: 'tpl-1' }}
] }};
controller.applyWorkspaceState({{ draft: base, availableTemplates: [{{ id: 'tpl-1', name: 'General', latest_version: {{ mode: 'freeform' }} }}], nextTranscriptId: 'tx-1' }});
if (!createButton.hidden || !createButton.disabled || !status.textContent.includes('Add another separate note')) throw new Error('one-note draft exposed invalid split confirmation');
controller.setContinueAvailable(true);
if (continueButton.hidden || continueButton.disabled || !status.textContent.includes('template selected when you started Create')) throw new Error('one-note continuation was not explained with its frozen template');
continueButton.click(); await Promise.resolve();
if (continuedOneNote !== 1) throw new Error('one-note continuation was unavailable for the browser intent');
const twoNotes = {{ ...base, topics: base.topics.map((topic) => ({{ ...topic, disposition: 'separate_note' }})) }};
controller.applyWorkspaceState({{ draft: twoNotes, availableTemplates: [{{ id: 'tpl-1', name: 'General', latest_version: {{ mode: 'freeform' }} }}], nextTranscriptId: 'tx-1' }});
if (createButton.hidden || createButton.disabled || status.textContent.includes('Add another separate note') || status.textContent.includes('Continue as one note')) throw new Error('one-note guidance remained after restoring a second note');
controller.applyWorkspaceState({{ draft: base, availableTemplates: [{{ id: 'tpl-1', name: 'General', latest_version: {{ mode: 'freeform' }} }}], nextTranscriptId: 'tx-1' }});
if (trigger.hidden || trigger.getAttribute('aria-expanded') !== 'false') throw new Error('trigger state');
trigger.click();
if (modal.hidden || trigger.getAttribute('aria-expanded') !== 'true') throw new Error('open state');
const topicRows = topicList.querySelectorAll('fieldset');
if (topicRows.length !== 2) throw new Error('topic order');
topicRows[1].querySelector('[data-split-review-undo]').click();
topicList.querySelectorAll('fieldset')[1].querySelector('[data-split-review-make-primary]').click();
topicList.querySelectorAll('fieldset')[0].querySelector('[data-split-review-merge]').click();
const template = topicList.querySelector('select[data-split-review-template]'); template.value = 'tpl-1'; template.fire('change');
const serialized = controller.serialize();
if (serialized.topics[0].topic_uuid !== 'topic-1' || serialized.topics[0].disposition !== 'include_in_primary' || serialized.topics[1].is_primary !== true || serialized.topics[1].disposition !== 'separate_note') throw new Error('edit serialization');
if (problemCount.textContent !== '2 problems in review' || createButton.textContent !== 'Create 1 note') throw new Error('split review counts');
closeButton.click();
if (!modal.hidden || trigger.getAttribute('aria-expanded') !== 'false') throw new Error('close state');
trigger.click();
const invalidTemplate = topicList.querySelector('select[data-split-review-template]'); invalidTemplate.value = ''; invalidTemplate.fire('change');
await controller.save(); if (calls.length !== 0 || !status.textContent.includes('template')) throw new Error('invalid save fetched');
invalidTemplate.value = 'tpl-1'; invalidTemplate.fire('change');
controller.applyWorkspaceState({{ draft: {{ ...base, topics: base.topics.map((topic) => ({{ ...topic, title: topic.title + ' server', updated_at: undefined }})) }} }});
if (controller.getDraft().topics[0].title !== 'Main problem') throw new Error('dirty SSE clobbered local');
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
const conflictTemplate = conflictTopicList.querySelector('select[data-split-review-template]'); conflictTemplate.value = 'tpl-2'; conflictTemplate.fire('change');
await conflictController.save();
if (conflictCalls.length !== 1 || conflictController.getDraft().topics[0].title !== 'Main problem server' || conflictController.getRemoteDraft().topics[0].title !== 'Main problem server') throw new Error('conflict edits merged or latest draft not loaded');
if (conflictController.getDraft().status !== 'active' || !conflictStatus.textContent.includes('changed elsewhere')) throw new Error('conflict did not reload safely');
const unicodeTopicList = new FakeElement('div');
let unicodeCalls = 0;
const unicodeController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: unicodeTopicList, status: new FakeElement('p'), saveButton: new FakeElement('button'), closeButtons: [], fetcher: async () => {{ unicodeCalls += 1; throw new Error('duplicate Unicode titles fetched'); }}, getTranscriptId: () => 'tx-1' }});
unicodeController.applyWorkspaceState({{ draft: {{ ...base, topics: [{{ ...base.topics[0], title: 'Alpha' }}, {{ ...base.topics[1], title: 'Beta', is_primary: false }}] }} }}); unicodeController.open();
unicodeController.getDraft().topics[0].title = 'Straße'; unicodeController.getDraft().topics[1].title = 'STRASSE';
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
const pendingSelect = pendingTopicList.querySelector('select[data-split-review-template]'); pendingSelect.value = 'tpl-2'; pendingSelect.fire('change');
const pendingSave = pendingController.save(); if (!pendingSaveButton.disabled || !pendingCloseButton.disabled || !pendingSelect.disabled) throw new Error('save did not lock modal controls');
pendingSelect.value = 'tpl-3'; pendingSelect.fire('change'); pendingCloseButton.click(); pendingSaveButton.click();
if (pendingController.isOpen() !== true || pendingController.getDraft().topics[0].template_id !== 'tpl-2' || pendingCalls !== 1) throw new Error('pending save accepted edit, close, or duplicate');
resolvePending({{ status: 200, ok: true, json: async () => ({{ ...base, draft_id: 'pending-draft', status: 'active', updated_at: 'saved' }}) }}); await pendingSave;
if (!pendingSaveButton.disabled || pendingCloseButton.disabled || pendingTopicList.querySelector('select[data-split-review-template]').disabled) throw new Error('save controls did not restore');
const errorTopicList = new FakeElement('div');
const errorCloseButton = new FakeElement('button');
const errorSaveButton = new FakeElement('button');
const errorController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: errorTopicList, status: new FakeElement('p'), saveButton: errorSaveButton, closeButtons: [errorCloseButton], fetcher: async () => ({{ status: 500, ok: false, json: async () => ({{}}) }}), getTranscriptId: () => 'tx-1' }});
errorController.applyWorkspaceState({{ draft: {{ ...base, draft_id: 'error-draft' }}, nextTranscriptId: 'tx-1' }}); errorController.open(); errorController.getDraft().topics[0].title = 'error';
await errorController.save(); if (errorCloseButton.disabled || errorTopicList.querySelector('select[data-split-review-template]').disabled || !errorSaveButton.disabled) throw new Error('save controls did not restore after error');
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
const savedGuidanceTopicList = new FakeElement('div');
const savedGuidanceStatus = new FakeElement('p');
const savedGuidanceDraft = {{ ...base, draft_id: 'saved-guidance', updated_at: 'saved-guidance-v1' }};
const savedGuidanceController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: savedGuidanceTopicList,
  status: savedGuidanceStatus, saveButton: new FakeElement('button'), closeButtons: [], getTranscriptId: () => 'tx-1',
  fetcher: async () => ({{ status: 200, ok: true, json: async () => savedGuidanceDraft }}),
}});
savedGuidanceController.applyWorkspaceState({{ draft: savedGuidanceDraft, nextTranscriptId: 'tx-1' }});
savedGuidanceController.open();
const savedGuidanceTemplate = savedGuidanceTopicList.querySelector('select[data-split-review-template]');
savedGuidanceTemplate.value = 'tpl-2'; savedGuidanceTemplate.fire('change');
await savedGuidanceController.save();
if (savedGuidanceStatus.dataset.statusKind !== 'one-note-guidance') throw new Error('saved one-note draft lost its dedicated guidance state');
savedGuidanceController.applyWorkspaceState({{ draft: {{ ...savedGuidanceDraft, topics: savedGuidanceDraft.topics.map((topic) => ({{ ...topic, disposition: 'separate_note' }})) }}, nextTranscriptId: 'tx-1' }});
if (savedGuidanceStatus.textContent.includes('Add another separate note') || savedGuidanceStatus.textContent.includes('Continue as one note')) throw new Error('saved one-note guidance remained after restoring a second note');
const manualRefreshDraft = {{ ...base, draft_id: 'manual-refresh', topics: [{{ ...base.topics[0] }}] }};
savedGuidanceController.applyWorkspaceState({{ draft: manualRefreshDraft, nextTranscriptId: 'tx-1', manualReview: true }});
if (!savedGuidanceStatus.textContent.includes('One problem was detected')) throw new Error('manual one-problem guidance was not shown');
savedGuidanceController.applyWorkspaceState({{ draft: manualRefreshDraft, nextTranscriptId: 'tx-1' }});
if (!savedGuidanceStatus.textContent.includes('One problem was detected')) throw new Error('same-draft workspace refresh erased manual guidance');
const mergedTopicList = new FakeElement('div');
const mergedModal = new FakeElement('div');
const mergedSaveButton = new FakeElement('button');
const mergedContinueButton = new FakeElement('button');
let mergedIntent = 'intent-merged'; let mergedPutCalls = 0; let mergedContinues = 0; let resolveMergedSave;
const mergedSaveResponse = new Promise((resolve) => {{ resolveMergedSave = resolve; }});
const mergedDraft = {{ ...base, draft_id: 'all-merged', topics: base.topics.map((topic) => ({{ ...topic, disposition: 'separate_note' }})) }};
const mergedController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: mergedModal, topicList: mergedTopicList,
  status: new FakeElement('p'), saveButton: mergedSaveButton, continueButton: mergedContinueButton, closeButtons: [], getTranscriptId: () => 'tx-1',
  getConfirmIntentId: () => mergedIntent, continueAsOneNote: async () => {{ mergedContinues += 1; return true; }},
  fetcher: async () => {{ mergedPutCalls += 1; return mergedSaveResponse; }},
}});
mergedController.applyWorkspaceState({{ draft: mergedDraft, nextTranscriptId: 'tx-1' }}); mergedController.setContinueAvailable(true); mergedController.open();
mergedTopicList.querySelectorAll('fieldset')[1].querySelector('[data-split-review-merge]').click();
if (mergedSaveButton.textContent !== 'Continue as one note' || mergedSaveButton.disabled || !mergedContinueButton.hidden) throw new Error('all-merged draft did not expose exactly one immediate one-note action');
mergedSaveButton.click(); mergedSaveButton.click();
if (mergedPutCalls !== 1 || !mergedSaveButton.disabled) throw new Error('all-merged action did not single-flight its save');
resolveMergedSave({{ status: 200, ok: true, json: async () => ({{ ...mergedDraft, updated_at: 'merged-saved', topics: mergedDraft.topics.map((topic, index) => ({{ ...topic, disposition: index ? 'include_in_primary' : 'separate_note' }})) }}) }});
for (let tick = 0; tick < 10; tick += 1) await Promise.resolve();
if (mergedContinues !== 1 || mergedController.isOpen()) throw new Error('all-merged draft did not continue immediately and close the review');
const replacedIntentTopicList = new FakeElement('div'); const replacedIntentSaveButton = new FakeElement('button');
let replacedIntent = 'intent-original'; let replacedContinues = 0; let resolveReplacedSave;
const replacedSaveResponse = new Promise((resolve) => {{ resolveReplacedSave = resolve; }});
const replacedController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: replacedIntentTopicList,
  status: new FakeElement('p'), saveButton: replacedIntentSaveButton, closeButtons: [], getTranscriptId: () => 'tx-1',
  getConfirmIntentId: () => replacedIntent, continueAsOneNote: async () => {{ replacedContinues += 1; return true; }},
  fetcher: async () => replacedSaveResponse,
}});
replacedController.applyWorkspaceState({{ draft: {{ ...mergedDraft, draft_id: 'intent-replaced' }}, nextTranscriptId: 'tx-1' }}); replacedController.setContinueAvailable(true); replacedController.open();
replacedIntentTopicList.querySelectorAll('fieldset')[1].querySelector('[data-split-review-merge]').click(); replacedIntentSaveButton.click();
replacedIntent = 'intent-replacement';
resolveReplacedSave({{ status: 200, ok: true, json: async () => ({{ ...mergedDraft, draft_id: 'intent-replaced', updated_at: 'replacement-saved', topics: mergedDraft.topics.map((topic, index) => ({{ ...topic, disposition: index ? 'include_in_primary' : 'separate_note' }})) }}) }});
for (let tick = 0; tick < 10; tick += 1) await Promise.resolve();
if (replacedContinues !== 0 || !replacedController.isOpen()) throw new Error('replaced intent was consumed after the all-merged save');
const failedMergeTopicList = new FakeElement('div'); const failedMergeSaveButton = new FakeElement('button');
let failedMergeCalls = 0; let failedMergeContinues = 0;
const failedMergeController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: failedMergeTopicList,
  status: new FakeElement('p'), saveButton: failedMergeSaveButton, closeButtons: [], getTranscriptId: () => 'tx-1',
  getConfirmIntentId: () => 'intent-failed-save', continueAsOneNote: async () => {{ failedMergeContinues += 1; return true; }},
  fetcher: async () => {{ failedMergeCalls += 1; return {{ status: 500, ok: false, json: async () => ({{}}) }}; }},
}});
failedMergeController.applyWorkspaceState({{ draft: {{ ...mergedDraft, draft_id: 'failed-merge' }}, nextTranscriptId: 'tx-1' }}); failedMergeController.setContinueAvailable(true); failedMergeController.open();
failedMergeTopicList.querySelectorAll('fieldset')[1].querySelector('[data-split-review-merge]').click(); failedMergeSaveButton.click();
for (let tick = 0; tick < 10; tick += 1) await Promise.resolve();
if (failedMergeCalls !== 1 || failedMergeContinues !== 0 || !failedMergeController.isOpen() || failedMergeSaveButton.textContent !== 'Continue as one note') throw new Error('failed all-merged save consumed or lost its retry action');
const retryTopicList = new FakeElement('div'); const retrySaveButton = new FakeElement('button');
let retryContinues = 0;
const retryController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: retryTopicList,
  status: new FakeElement('p'), saveButton: retrySaveButton, closeButtons: [], getTranscriptId: () => 'tx-1',
  getConfirmIntentId: () => 'intent-retry', continueAsOneNote: async () => ++retryContinues > 1,
}});
const savedMergedDraft = {{ ...mergedDraft, draft_id: 'saved-merged', topics: mergedDraft.topics.map((topic, index) => ({{ ...topic, disposition: index ? 'include_in_primary' : 'separate_note' }})) }};
retryController.applyWorkspaceState({{ draft: savedMergedDraft, nextTranscriptId: 'tx-1' }}); retryController.setContinueAvailable(true); retryController.open();
if (retrySaveButton.textContent !== 'Continue as one note' || retrySaveButton.disabled) throw new Error('saved all-merged draft did not remain immediately continuable');
retrySaveButton.click(); for (let tick = 0; tick < 10; tick += 1) await Promise.resolve();
if (retryContinues !== 1 || !retryController.isOpen()) throw new Error('failed continuation did not remain retryable');
retryController.setContinueAvailable(false);
if (retrySaveButton.textContent !== 'Save split' || !retrySaveButton.disabled) throw new Error('revoked continuation availability did not restore normal save behavior');
retryController.setContinueAvailable(true);
retryTopicList.querySelector('[data-split-review-undo]').click();
if (retrySaveButton.textContent !== 'Save split') throw new Error('unmerged secondary problem retained the one-note action');
retryTopicList.querySelectorAll('fieldset')[1].querySelector('[data-split-review-skip]').click();
if (retrySaveButton.textContent !== 'Save split') throw new Error('excluded secondary problem exposed the one-note action');
const addedTopicList = new FakeElement('div');
const addedAddButton = new FakeElement('button');
const addedSaveButton = new FakeElement('button');
const addedCreateButton = new FakeElement('button');
const addedCalls = [];
const addedBase = {{ ...base, draft_id: 'clinician-additions', updated_at: 'add-v1', topics: [
  {{ ...base.topics[0] }},
  {{ ...base.topics[1], disposition: 'separate_note', template_id: 'tpl-1' }},
  {{ topic_uuid: 'topic-3', title: 'Third', order: 2, is_primary: false, disposition: 'separate_note', template_id: 'tpl-1' }},
  {{ topic_uuid: 'topic-4', title: 'Fourth', order: 3, is_primary: false, disposition: 'separate_note', template_id: 'tpl-1' }},
] }};
const addedSaved = {{ ...addedBase, updated_at: 'add-v2', topics: [...addedBase.topics, {{ topic_uuid: 'server-added-1', title: 'Added one', order: 4, is_primary: false, disposition: 'separate_note', template_id: 'tpl-1' }}, {{ topic_uuid: 'server-added-2', title: 'Added two', order: 5, is_primary: false, disposition: 'separate_note', template_id: 'tpl-1' }}] }};
const addedController = createSplitReviewController({{
  trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: addedTopicList,
  status: new FakeElement('p'), addButton: addedAddButton, saveButton: addedSaveButton, createButton: addedCreateButton,
  closeButtons: [], getTranscriptId: () => 'tx-1', getConfirmIntentId: () => 'intent-added',
  fetcher: async (_url, options) => {{ addedCalls.push(JSON.parse(options.body)); return {{ status: 200, ok: true, json: async () => addedSaved }}; }},
}});
addedController.applyWorkspaceState({{ draft: addedBase, availableTemplates: [{{ id: 'tpl-1', name: 'General', latest_version: {{ mode: 'freeform' }} }}], nextTranscriptId: 'tx-1' }});
addedController.open(); addedAddButton.click(); addedAddButton.click();
if (addedController.getDraft().topics.length !== 6 || !addedAddButton.disabled) throw new Error('add problem did not enforce the six-topic limit');
const addedTitles = addedTopicList.querySelectorAll('input[data-split-review-title]');
const addedTemplates = addedTopicList.querySelectorAll('select[data-split-review-template]');
addedTitles[4].value = 'Added one'; addedTitles[4].fire('input');
addedTemplates[4].value = 'tpl-1'; addedTemplates[4].fire('change');
addedTitles[5].value = 'Added two'; addedTitles[5].fire('input');
addedTemplates[5].value = 'tpl-1'; addedTemplates[5].fire('change');
if (!addedCreateButton.hidden || !addedCreateButton.disabled) throw new Error('unsaved clinician additions allowed confirmation');
await addedController.save();
if (addedCalls.length !== 1 || addedCalls[0].topics[4].topic_uuid || addedCalls[0].topics[5].topic_uuid) throw new Error('new topics leaked browser identities into PUT');
if (addedController.getDraft().topics[4].topic_uuid !== 'server-added-1' || addedController.getDraft().topics[5].topic_uuid !== 'server-added-2') throw new Error('saved added topics did not use server UUIDs');
if (addedCreateButton.hidden || addedCreateButton.disabled) throw new Error('saved valid additions did not enable confirmation');
const savedTitle = addedTopicList.querySelectorAll('input[data-split-review-title]')[4]; savedTitle.value = 'Corrected added problem'; savedTitle.fire('input');
if (addedController.getDraft().topics[4].title !== 'Corrected added problem') throw new Error('saved added title was not editable');
const unsavedTopicList = new FakeElement('div'); const unsavedAddButton = new FakeElement('button');
const unsavedController = createSplitReviewController({{ trigger: new FakeElement('button'), modal: new FakeElement('div'), topicList: unsavedTopicList, addButton: unsavedAddButton, saveButton: new FakeElement('button'), closeButtons: [], getTranscriptId: () => 'tx-1' }});
unsavedController.applyWorkspaceState({{ draft: {{ ...addedBase, topics: addedBase.topics.slice(0, 4) }}, nextTranscriptId: 'tx-1' }}); unsavedController.open(); unsavedAddButton.click();
unsavedTopicList.querySelector('[data-split-review-remove]').click();
if (unsavedController.getDraft().topics.length !== 4 || !unsavedController.isDirty()) throw new Error('unsaved clinician addition was not removable');
"""
    )
    subprocess.run(["node", str(runner)], check=True, cwd=ROOT, env={**os.environ, "NODE_NO_WARNINGS": "1"})
