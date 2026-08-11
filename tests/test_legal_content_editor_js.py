import subprocess
import textwrap
from pathlib import Path


def test_legal_markdown_preview_ignores_response_for_changed_source(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "legal_content_editor_preview_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const script = fs.readFileSync(sourcePath, 'utf8');

            class HTMLElement {
              constructor() {
                this.classList = { toggle() {} };
                this.hidden = false;
                this.listeners = {};
                this.childElementCount = 0;
                this.attributes = {};
                this.tabIndex = 0;
                this.focused = false;
              }

              addEventListener(type, listener) {
                this.listeners[type] = listener;
              }

            setAttribute(name, value) { this.attributes[name] = String(value); }
            focus() { this.focused = true; }
              replaceChildren() {
                this._innerHTML = '';
                this.childElementCount = 0;
              }
            }

            class HTMLFormElement extends HTMLElement {}
            class HTMLTextAreaElement extends HTMLElement {}
            class HTMLButtonElement extends HTMLElement {}

            const form = new HTMLFormElement();
            const source = new HTMLTextAreaElement();
            const writeTab = new HTMLButtonElement();
            const previewTab = new HTMLButtonElement();
            const writePanel = new HTMLElement();
            const previewPanel = new HTMLElement();
            const previewBody = new HTMLElement();
            const previewMessage = new HTMLElement();
            const csrfInput = { value: 'csrf-token' };
            previewPanel.hidden = true;
            previewTab.tabIndex = -1;

            Object.defineProperty(previewBody, 'innerHTML', {
              get() { return this._innerHTML || ''; },
              set(value) {
                this._innerHTML = value;
                this.childElementCount = value ? 1 : 0;
              },
            });

            const elements = {
              '[data-legal-markdown-source]': source,
              '[data-legal-write-tab]': writeTab,
              '[data-legal-preview-tab]': previewTab,
              '[data-legal-write-panel]': writePanel,
              '[data-legal-preview-panel]': previewPanel,
              '[data-legal-preview-body]': previewBody,
              '[data-legal-preview-message]': previewMessage,
              "input[name='_csrf_token']": csrfInput,
            };
            form.querySelector = (selector) => elements[selector] || null;

            const pending = [];
            const submittedSources = [];
            const fetch = (_url, options) => {
              submittedSources.push(options.body.get('markdown_source'));
              return new Promise((resolve) => pending.push(resolve));
            };
            const response = (html) => ({
              ok: true,
              text: async () => html,
              headers: { get: () => null },
            });
            const flush = async () => {
              for (let index = 0; index < 6; index += 1) await Promise.resolve();
            };

            const sandbox = {
              document: { querySelector: () => form },
              Error,
              FormData,
              HTMLElement,
              HTMLButtonElement,
              HTMLFormElement,
              HTMLTextAreaElement,
              fetch,
              Promise,
            };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(script, sandbox, { filename: sourcePath });

            assert.equal(writeTab.tabIndex, 0);
            assert.equal(previewTab.tabIndex, -1);
            const keydown = (tab, key) => {
              let prevented = false;
              tab.listeners.keydown({
                currentTarget: tab,
                key,
                preventDefault() { prevented = true; },
              });
              assert.equal(prevented, true);
            };
            source.value = 'keyboard source';
            keydown(writeTab, 'ArrowRight');
            await flush();
            assert.deepEqual(submittedSources, ['keyboard source']);
            assert.equal(previewTab.tabIndex, 0);
            assert.equal(writeTab.tabIndex, -1);
            assert.equal(previewPanel.hidden, false);
            assert.equal(previewTab.focused, true);
            pending.shift()(response('<p>keyboard source</p>'));
            await flush();
            assert.equal(previewBody.innerHTML, '<p>keyboard source</p>');
            keydown(previewTab, 'ArrowLeft');
            assert.equal(writeTab.tabIndex, 0);
            assert.equal(previewPanel.hidden, true);
            keydown(writeTab, 'End');
            await flush();
            assert.equal(previewTab.tabIndex, 0);
            assert.deepEqual(submittedSources, ['keyboard source']);
            keydown(previewTab, 'Home');
            assert.equal(writeTab.tabIndex, 0);

            source.value = 'source A';
            previewTab.listeners.click();
            await flush();
            assert.deepEqual(submittedSources, ['keyboard source', 'source A']);

            source.value = 'source B';
            source.listeners.input();
            pending.shift()(response('<p>source A</p>'));
            await flush();
            assert.equal(previewBody.innerHTML, '');

            previewTab.listeners.click();
            await flush();
            assert.deepEqual(submittedSources, ['keyboard source', 'source A', 'source B']);
            pending.shift()(response('<p>source B</p>'));
            await flush();
            assert.equal(previewBody.innerHTML, '<p>source B</p>');
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app/static/js/legal-content-editor.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
