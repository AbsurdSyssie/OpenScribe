import subprocess
import textwrap
from pathlib import Path


def test_vad_inactivity_prompt_dismiss_rearms_and_resets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "vad_inactivity_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            f"""
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const root = {str(root)!r};
            const mediaPath = `${{root}}/app/static/js/transcribe/media.js`;
            const mediaSource = fs.readFileSync(mediaPath, 'utf8')
              .replace("import {{ csrfFetch }} from '../csrf.js';", "const csrfFetch = async () => ({{ ok: true }});")
              .replace('export function createAudioCaptureController', 'function createAudioCaptureController');

            const makeElement = () => ({{
              hidden: true,
              dataset: {{}},
              style: {{ setProperty() {{}} }},
              listeners: {{}},
              appendChild() {{}},
              addEventListener(type, listener) {{ this.listeners[type] = listener; }},
              click() {{ this.listeners.click?.(); }},
            }});

            const makeHarness = () => {{
              let now = 0;
              let nextTimerId = 1;
              const timers = new Map();
              const callbacks = [];
              const instances = [];
              const documentListeners = {{}};

              const setTimeoutFake = (callback, delay = 0) => {{
                const id = nextTimerId;
                nextTimerId += 1;
                timers.set(id, {{ callback, dueAt: now + Number(delay || 0), interval: false }});
                return id;
              }};
              const clearTimerFake = (id) => timers.delete(id);
              const setIntervalFake = (callback, delay = 0) => {{
                const id = nextTimerId;
                nextTimerId += 1;
                timers.set(id, {{ callback, dueAt: now + Number(delay || 0), delay: Number(delay || 0), interval: true }});
                return id;
              }};
              const advance = (ms) => {{
                now += ms;
                for (;;) {{
                  const due = [...timers.entries()]
                    .filter(([, timer]) => timer.dueAt <= now)
                    .sort((left, right) => left[1].dueAt - right[1].dueAt);
                  if (due.length === 0) break;
                  const [id, timer] = due[0];
                  if (!timer.interval) timers.delete(id);
                  timer.callback();
                  if (timer.interval && timers.has(id)) timer.dueAt = now + timer.delay;
                }}
              }};

              const sandbox = {{
                Blob,
                CustomEvent: class CustomEvent {{ constructor(type) {{ this.type = type; }} }},
                Date: {{ now: () => now }},
                Error,
                Float32Array,
                FormData,
                JSON,
                Math,
                Number,
                Promise,
                console,
                document: {{
                  hidden: false,
                  createDocumentFragment: () => ({{ appendChild() {{}} }}),
                  createElement: () => makeElement(),
                  addEventListener(type, listener) {{ documentListeners[type] = listener; }},
                  dispatchEvent() {{ return true; }},
                }},
                navigator: {{ mediaDevices: {{ getUserMedia: async () => ({{ getTracks: () => [] }}) }} }},
                window: null,
              }};
              sandbox.window = {{
                clearInterval: clearTimerFake,
                clearTimeout: clearTimerFake,
                localStorage: {{ getItem: () => null, setItem() {{}} }},
                setInterval: setIntervalFake,
                setTimeout: setTimeoutFake,
                vad: {{
                  MicVAD: {{
                    new: async (options) => {{
                      callbacks.push(options);
                      const instance = {{ start() {{}}, pause() {{}}, destroy() {{ instance.destroyed = true; }} }};
                      instances.push(instance);
                      return instance;
                    }},
                  }},
                }},
              }};
              sandbox.globalThis = sandbox;
              vm.createContext(sandbox);
              vm.runInContext(mediaSource, sandbox, {{ filename: mediaPath }});

              const dom = {{
                audioActionTrigger: makeElement(),
                fileInput: makeElement(),
                micTimer: makeElement(),
                micVisualizer: makeElement(),
                recordToggleButton: makeElement(),
                silencePrompt: makeElement(),
                silencePromptDismiss: makeElement(),
                uploadForm: makeElement(),
              }};
              const state = {{
                activeIngestionMode: 'live_chunked',
                latestIngestionJobStatus: '',
                nextLiveChunkSequenceNo: 1,
                transcriptId: 'transcript-1',
              }};
              const controller = sandbox.createAudioCaptureController({{
                dom,
                config: {{
                  liveChunkOverlapMs: 0,
                  liveChunkRateLimitRetryMs: 0,
                  liveMaxChunkMs: 1000,
                  liveMinChunkMs: 100,
                  livePostRollTrimMs: 0,
                  livePreRollMs: 0,
                  liveRestartDelayMs: 0,
                  liveSilenceThresholdMs: 100,
                  liveVadAssetBasePath: '/vad/',
                  liveVadModel: 'v5',
                  liveVadOnnxBasePath: '/onnx/',
                  liveVadSampleRate: 16000,
                  vadSilencePromptMs: 50,
                }},
                uploadBatchAudio: async () => {{}},
                canUseLiveInput: () => true,
                canUseWholeFileInput: () => true,
                getState: () => state,
                setNextLiveChunkSequenceNo: (value) => {{ state.nextLiveChunkSequenceNo = value; }},
                getDefaultMicStatusState: () => ({{ message: 'Idle', kind: 'idle' }}),
                syncTranscriptTitleIfNeeded: async () => {{}},
                finalizeLiveCapture: async () => {{}},
                fetchWorkspace: async () => {{}},
                pollWorkspace: () => {{}},
                scheduleWorkspaceRefreshBurst: () => {{}},
                parseErrorMessage: async () => 'error',
                setMicButtons: () => {{}},
                setMicStatus: () => {{}},
                setVisibleStatus: () => {{}},
                setSessionProgress: () => {{}},
                setRetryAvailability: () => {{}},
                showFlash: () => {{}},
                reflectBackendStatus: () => {{}},
                reportMicIssue: () => {{}},
              }});
              controller.attachDomListeners();

              const start = async () => {{
                dom.recordToggleButton.click();
                await Promise.resolve();
                await Promise.resolve();
                assert.equal(callbacks.length, 1);
              }};

              return {{ advance, callbacks, controller, dom, documentListeners, instances, start }};
            }};

            const dismissHarness = makeHarness();
            await dismissHarness.start();
            assert.equal(dismissHarness.dom.silencePrompt.hidden, true);
            dismissHarness.advance(50);
            assert.equal(dismissHarness.dom.silencePrompt.hidden, false, 'silent timer shows prompt');
            dismissHarness.dom.silencePromptDismiss.click();
            assert.equal(dismissHarness.dom.silencePrompt.hidden, true, 'dismiss hides prompt');
            dismissHarness.advance(200);
            assert.equal(dismissHarness.dom.silencePrompt.hidden, true, 'dismiss suppresses same silent interval');
            dismissHarness.callbacks[0].onSpeechStart();
            dismissHarness.callbacks[0].onVADMisfire();
            dismissHarness.advance(50);
            assert.equal(dismissHarness.dom.silencePrompt.hidden, false, 'new silent interval re-arms after speech');
            dismissHarness.dom.recordToggleButton.click();
            dismissHarness.advance(200);
            assert.equal(dismissHarness.dom.silencePrompt.hidden, true, 'stop resets prompt and timer');

            const lifecycleHarness = makeHarness();
            await lifecycleHarness.start();
            lifecycleHarness.advance(50);
            assert.equal(lifecycleHarness.dom.silencePrompt.hidden, false);
            assert.equal(lifecycleHarness.controller.handlePageLifecycleExit(), true);
            lifecycleHarness.advance(200);
            assert.equal(lifecycleHarness.dom.silencePrompt.hidden, true, 'page lifecycle reset clears prompt and timer');
            """
        ),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
