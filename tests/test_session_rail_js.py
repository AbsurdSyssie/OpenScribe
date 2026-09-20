import os
import subprocess
import textwrap
from pathlib import Path


def test_session_rail_formats_and_groups_timestamps_in_browser_timezone(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "session_rail_timezone_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const source = fs.readFileSync(sourcePath, 'utf8')
              .replaceAll('export function ', 'function ');
            const sandbox = { Date, Intl, Number };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            assert.equal(
              sandbox.formatSessionRailCreatedAt('2026-07-19T12:00:00+00:00'),
              '13:00',
            );
            assert.equal(
              sandbox.formatSessionRailCreatedAt('2026-01-19T12:00:00+00:00'),
              '12:00',
            );
            assert.deepEqual(
              JSON.parse(JSON.stringify(sandbox.sessionRailGroup('2026-07-19T23:30:00+00:00'))),
              {
                dateKey: '2026-07-20',
                dateLabel: '20/07/2026',
                period: 'morning',
              },
            );
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app/static/js/transcribe/sessionRail.js"))),
        encoding="utf-8",
    )

    env = {**os.environ, "TZ": "Europe/London"}
    subprocess.run(["node", str(runner)], check=True, cwd=root, env=env)


def test_session_rail_localizes_and_reveals_existing_server_rendered_timestamp(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "session_rail_existing_timestamp_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const source = fs.readFileSync(sourcePath, 'utf8')
              .replaceAll('export function ', 'function ');
            const sandbox = { Date, Intl, Number };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            const createdAt = {
              dataset: { sessionCreatedAt: '2026-07-19T12:34:00+00:00' },
              attributes: { 'aria-hidden': 'true' },
              textContent: '12:34',
              removeAttribute: (name) => { delete createdAt.attributes[name]; },
            };
            const existingServerRenderedRow = {
              querySelector: (selector) => selector === '[data-session-created-at]' ? createdAt : null,
            };

            assert.equal(createdAt.textContent, '12:34');
            assert.equal(createdAt.attributes['aria-hidden'], 'true');
            assert.equal(createdAt.dataset.sessionCreatedAtReady, undefined);

            sandbox.syncSessionRailCreatedAt(
              existingServerRenderedRow,
              '2026-07-19T12:34:00+00:00',
            );

            assert.equal(createdAt.textContent, '13:34');
            assert.equal(createdAt.dataset.sessionCreatedAt, '2026-07-19T12:34:00+00:00');
            assert.equal(createdAt.dataset.sessionCreatedAtReady, 'true');
            assert.equal(createdAt.attributes['aria-hidden'], undefined);
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app/static/js/transcribe/sessionRail.js"))),
        encoding="utf-8",
    )

    env = {**os.environ, "TZ": "Europe/London"}
    subprocess.run(["node", str(runner)], check=True, cwd=root, env=env)


def test_session_rail_pending_timestamp_uses_a_motion_aware_decorative_skeleton():
    css = Path("app/static/css/transcribe.css").read_text()

    pending_rule = css.split(
        "[data-session-created-at]:not([data-session-created-at-ready]) {", 1
    )[1].split("}", 1)[0]
    skeleton_rule = css.split(
        "[data-session-created-at]:not([data-session-created-at-ready])::after {", 1
    )[1].split("}", 1)[0]

    assert "color: transparent;" in pending_rule
    assert "content: '';" in skeleton_rule
    assert "animation: session-created-at-pulse" in skeleton_rule
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation: none;" in css


def test_session_rail_preserves_visible_item_and_nudges_clipped_items(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "session_rail_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const source = fs.readFileSync(sourcePath, 'utf8')
              .replaceAll('export function ', 'function ');
            const sandbox = { Math };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            const calls = [];
            const scrollContainer = {
              scrollTop: 263,
              clientHeight: 1212,
              getBoundingClientRect: () => ({ top: 65, bottom: 1277, height: 1212 }),
              scrollTo: (options) => {
                calls.push(options);
                scrollContainer.scrollTop = options.top;
              },
            };
            const visibleFinalItem = {
              getBoundingClientRect: () => ({ top: 1215, bottom: 1277, height: 62 }),
            };

            const unchangedTop = sandbox.keepSessionRailItemVisible({
              scrollContainer,
              item: visibleFinalItem,
            });

            assert.equal(unchangedTop, 263);
            assert.equal(calls.length, 0);

            const clippedBottomItem = {
              getBoundingClientRect: () => ({ top: 1240, bottom: 1302, height: 62 }),
            };
            const nudgedDownTop = sandbox.keepSessionRailItemVisible({
              scrollContainer,
              item: clippedBottomItem,
            });

            assert.equal(nudgedDownTop, 300);
            assert.equal(calls[0].top, 300);
            assert.equal(calls[0].behavior, 'smooth');

            scrollContainer.scrollTop = 263;
            const clippedTopItem = {
              getBoundingClientRect: () => ({ top: 50, bottom: 112, height: 62 }),
            };
            const nudgedUpTop = sandbox.keepSessionRailItemVisible({
              scrollContainer,
              item: clippedTopItem,
            });

            assert.equal(nudgedUpTop, 236);
            assert.equal(calls[1].top, 236);
            assert.equal(calls[1].behavior, 'smooth');
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app/static/js/transcribe/sessionRail.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_session_rail_reconcile_preserves_loaded_rows_when_workspace_appends_old_active_item(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "session_rail_reconcile_runner.mjs"
    runner.write_text(
        textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import vm from 'node:vm';

            const sourcePath = __SOURCE_PATH__;
            const source = fs.readFileSync(sourcePath, 'utf8')
              .replaceAll('export function ', 'function ');
            const sandbox = { Math, Date };
            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: sourcePath });

            const item = (index, title = `Consultation ${index}`) => ({
              id: `consultation-${String(index).padStart(2, '0')}`,
              title,
              created_at: new Date(Date.UTC(2026, 6, 19, 12, 0, 23 - index)).toISOString(),
            });
            const loaded = Array.from({ length: 23 }, (_, index) => item(index));
            const selectedOld = item(16, 'Updated selected consultation');
            const workspaceItems = [...loaded.slice(0, 12), selectedOld];

            const reconciled = sandbox.reconcileSessionRailItems({
              currentItems: loaded,
              workspaceItems,
              pageSize: 12,
              preserveLoaded: true,
            });

            assert.deepEqual(
              Array.from(reconciled, (entry) => entry.id),
              loaded.map((entry) => entry.id),
            );
            assert.equal(reconciled.length, 23);
            assert.equal(reconciled[16].title, 'Updated selected consultation');
            """
        ).replace("__SOURCE_PATH__", repr(str(root / "app/static/js/transcribe/sessionRail.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)
