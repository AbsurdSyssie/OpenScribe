import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_detected_pii_false_positive_override_ui_contract():
    workspace = (ROOT / "app/templates/transcribe/_workspace.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app/static/js/transcribe/app.js").read_text(encoding="utf-8")
    desktop_css = (ROOT / "app/static/css/transcribe.css").read_text(encoding="utf-8")
    mobile_css = (ROOT / "app/static/css/transcribe-mobile.css").read_text(encoding="utf-8")

    assert "future redacted copies and AI requests may include its original text" in workspace
    assert "A manual PII entry still redacts that value." in workspace
    assert "entity.source === 'detected' && entity.id" in app_js
    assert "entity.source === 'clinical'" in app_js
    assert "data-pii-dismiss" in workspace
    assert "data-pii-dismiss" in app_js
    assert "Mark false positive" in workspace
    assert "Mark false positive" in app_js
    assert "Undo" in workspace
    assert "Undo" in app_js
    assert "/detected-pii/${entityId}/dismiss" in app_js
    assert "method: isDismissed ? 'DELETE' : 'POST'" in app_js
    assert "data-pii-group" in app_js
    assert "data-pii-jump" in app_js
    assert ".pii-row-override" in desktop_css
    assert ".pii-row-override" in mobile_css


def test_detected_pii_override_groups_rows_but_highlights_only_active_occurrence(tmp_path):
    runner = tmp_path / "detected-pii-override-runner.cjs"
    runner.write_text(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');
        const uniqueStart = source.indexOf('const uniquePiiEntities =');
        const escapeStart = source.indexOf('const escapeRegExp =', uniqueStart);
        const highlightStart = source.indexOf('const renderHighlightedTranscript =', escapeStart);
        const renderStart = source.indexOf('const renderDraft =', highlightStart);
        const groupStart = source.indexOf('const groupPiiEntities =', renderStart);
        const piiRenderStart = source.indexOf('const renderPiiEntities =', groupStart);
        assert.ok(uniqueStart >= 0 && escapeStart > uniqueStart && renderStart > highlightStart && piiRenderStart > groupStart);
        const code = `${source.slice(uniqueStart, escapeStart)}${source.slice(highlightStart, renderStart)}${source.slice(groupStart, piiRenderStart)}`
          .replace('const uniquePiiEntities =', 'globalThis.uniquePiiEntities =')
          .replace('const renderHighlightedTranscript =', 'globalThis.renderHighlightedTranscript =')
          .replace('const groupPiiEntities =', 'globalThis.groupPiiEntities =');
        const activeDraft = { textContent: '', innerHTML: '' };
        const sandbox = {
          activeDraft,
          escapeRegExp: (value) => String(value).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'),
          escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
          maskedPiiText: () => '•••',
          RegExp, Map, Set,
        };
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox, { filename: __SOURCE_PATH__ });

        const entities = [
          { id: 'first', source: 'detected', entity_type: 'PERSON', value: 'Alex', dismissed: true, start_index: 0, end_index: 4 },
          { id: 'second', source: 'detected', entity_type: 'LOCATION', value: 'Alex', dismissed: false, start_index: 16, end_index: 20 },
        ];
        assert.equal(sandbox.uniquePiiEntities(entities).length, 2);
        const groups = sandbox.groupPiiEntities(sandbox.uniquePiiEntities(entities));
        assert.equal(groups.length, 1);
        assert.equal(groups[0].count, 2);
        assert.equal(groups[0].entities.length, 2);
        const stale = sandbox.uniquePiiEntities([
          { source: 'detected', entity_type: 'PERSON', value: 'Sam', placeholder: '[PHI-1]' },
          { source: 'detected', entity_type: 'PERSON', value: 'Sam', placeholder: '[PHI-2]' },
        ]);
        assert.equal(sandbox.groupPiiEntities(stale)[0].count, 2);
        const clinical = sandbox.uniquePiiEntities([
          { source: 'clinical', entity_type: 'SYMPTOM', value: 'cough', occurrence_count: 1 },
          { source: 'clinical', entity_type: 'SYMPTOM', value: 'cough', occurrence_count: 1 },
        ]);
        assert.equal(sandbox.groupPiiEntities(clinical)[0].count, 2);
        assert.equal(sandbox.groupPiiEntities([{ source: 'clinical', value: 'a', entity_type: 'DISEASE', occurrence_count: 1 }]).length, 0);
        sandbox.renderHighlightedTranscript('Alex spoke with Alex.', entities);
        assert.equal((activeDraft.innerHTML.match(/<mark /g) || []).length, 1);
        assert.match(activeDraft.innerHTML, /Alex spoke with <mark /);
        """.replace("__SOURCE_PATH__", repr(str(ROOT / "app/static/js/transcribe/app.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=ROOT)
