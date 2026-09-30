import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_detected_initial_highlighting_requires_its_exact_standalone_case(tmp_path):
    runner = tmp_path / "transcript_pii_highlight_runner.cjs"
    runner.write_text(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');
        const start = source.indexOf('const renderHighlightedTranscript =');
        const end = source.indexOf('const renderDraft =', start);
        assert.ok(start >= 0 && end > start);
        const renderSource = source.slice(start, end)
          .replace('const renderHighlightedTranscript =', 'globalThis.renderHighlightedTranscript =');
        const activeDraft = { textContent: '', innerHTML: '' };
        const sandbox = {
          activeDraft,
          uniquePiiEntities: (entities) => entities,
          escapeRegExp: (value) => String(value).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'),
          escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
          maskedPiiText: () => '•••',
          RegExp, Map, Set,
        };
        vm.createContext(sandbox);
        vm.runInContext(renderSource, sandbox, { filename: __SOURCE_PATH__ });

        sandbox.renderHighlightedTranscript('Mr. A has a rash.', [{ value: 'A', source: 'detected', start_index: 4, end_index: 5 }]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 1);
        assert.match(activeDraft.innerHTML, /<mark class="pii-highlight" data-real-value="A">A<\\/mark> has a rash/);

        sandbox.renderHighlightedTranscript('Mr. A has a rash.', [{ value: 'A', source: 'manual' }]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 2);

        sandbox.renderHighlightedTranscript('AA Aa AB éA Aβ Á A B.', [
          { value: 'A', source: 'detected', start_index: 18, end_index: 19 },
          { value: 'B', source: 'detected', start_index: 20, end_index: 21 },
        ]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 2);
        assert.match(activeDraft.innerHTML, /AB éA Aβ Á <mark class="pii-highlight" data-real-value="A">A<\\/mark> <mark class="pii-highlight" data-real-value="B">B<\\/mark>/);

        sandbox.renderHighlightedTranscript('A a', [
          { value: 'A', source: 'detected' }, { value: 'A', source: 'manual' },
        ], { maskPii: true });
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 2);
        assert.match(activeDraft.innerHTML, />•••<\\/mark>/);

        sandbox.renderHighlightedTranscript('Jane & <Smith> attended.', [{ value: 'Jane & <Smith>', source: 'detected' }]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 0);

        sandbox.renderHighlightedTranscript('Alice met Alice.', [
          { id: 'first', value: 'Alice', source: 'detected', start_index: 0, end_index: 5 },
        ]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 1);
        assert.match(activeDraft.innerHTML, /<\\/mark> met Alice\\./);

        sandbox.renderHighlightedTranscript('a patient has a rash', [{ value: 'a', source: 'clinical' }]);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 0);
        """.replace("__SOURCE_PATH__", repr(str(ROOT / "app/static/js/transcribe/app.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=ROOT)
