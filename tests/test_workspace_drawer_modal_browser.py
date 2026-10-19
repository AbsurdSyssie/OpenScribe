"""Synthetic no-DB browser coverage for workspace drawer/modal interaction."""

import functools
import http.server
import threading
from pathlib import Path

import pytest


playwright_sync = pytest.importorskip("playwright.sync_api")


class _QuietStaticHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *args):
        return


@pytest.fixture
def static_asset_server():
    # Serve only public static assets; the test never exposes the repository
    # root, which contains local configuration and Git metadata.
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    handler = functools.partial(_QuietStaticHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mobile_drawer_preserves_real_guide_modal_and_yields_keyboard_focus(static_asset_server):
    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_sync.Error as exc:
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.goto(f"{static_asset_server}/js/workspace/app.js")
            page.set_content(
                """<div data-workspace-shell>
                  <aside id="workspace-sidebar" tabindex="-1">
                    <button id="guide-opener" data-start-guide>Open guide</button>
                    <button id="drawer-last">Last drawer control</button>
                  </aside>
                  <button data-workspace-drawer-toggle aria-expanded="false">Menu</button>
                  <button data-workspace-drawer-close>Close drawer</button>
                  <main id="workspace-main">Workspace</main>
                </div>
                <dialog data-tour-overlay aria-labelledby="guide-title" aria-describedby="guide-body">
                  <section data-tour-card tabindex="-1">
                    <h2 id="guide-title" data-tour-title tabindex="-1"></h2>
                    <p id="guide-body" data-tour-body></p>
                    <p data-tour-progress></p>
                    <button id="guide-back" data-tour-back disabled>Back</button>
                    <button id="guide-close" data-tour-close-button>Close</button>
                    <button id="guide-next" data-tour-next>Next</button>
                    <p data-tour-announcement role="status" aria-live="polite"></p>
                  </section>
                </dialog>
                <div id="outside-content">Outside content</div>"""
            )
            page.add_script_tag(
                url=f"{static_asset_server}/js/workspace/app.js",
                type="module",
            )
            page.wait_for_function("document.querySelector('#workspace-sidebar').hasAttribute('inert')")

            page.evaluate(
                """async (moduleUrl) => {
                  const { createGuidedTour } = await import(moduleUrl);
                  createGuidedTour({
                    dom: {
                      guideStartButtons: [document.querySelector('[data-start-guide]')],
                      tourDialog: document.querySelector('[data-tour-overlay]'),
                      tourCard: document.querySelector('[data-tour-card]'),
                      tourTitle: document.querySelector('[data-tour-title]'),
                      tourBody: document.querySelector('[data-tour-body]'),
                      tourProgress: document.querySelector('[data-tour-progress]'),
                      tourAnnouncement: document.querySelector('[data-tour-announcement]'),
                      tourBackButton: document.querySelector('[data-tour-back]'),
                      tourNextButton: document.querySelector('[data-tour-next]'),
                      tourCloseButtons: [...document.querySelectorAll('[data-tour-close-button]')],
                    },
                    steps: [{ title: 'Guide step', body: 'Synthetic guide content.' }],
                  }).attach();
                }""",
                f"{static_asset_server}/js/transcribe/tour.js",
            )
            page.locator("[data-workspace-drawer-toggle]").click()
            opener = page.locator("#guide-opener")
            opener.click()
            dialog = page.locator("[data-tour-overlay]")
            assert dialog.evaluate("element => element.open")
            assert not dialog.evaluate("element => element.hasAttribute('inert')")
            assert page.locator("#outside-content").evaluate("element => element.hasAttribute('inert')")

            # The real guide's window-capture handler must receive keyboard
            # events before the workspace document-level drawer trap.
            assert page.locator("[data-tour-card]").evaluate(
                "element => document.activeElement === element"
            ), "Opening the guide did not move focus into its card"
            page.keyboard.press("Tab")
            assert page.locator("#guide-close").evaluate(
                "element => document.activeElement === element"
            ), "Guide entry did not focus its first enabled control"
            page.keyboard.press("Tab")
            assert page.locator("#guide-next").evaluate(
                "element => document.activeElement === element"
            ), "The drawer Tab trap blocked normal focus movement inside the guide"
            page.keyboard.press("Tab")
            assert page.locator("#guide-close").evaluate(
                "element => document.activeElement === element"
            ), "The guide did not wrap focus from its last to first control"
            page.keyboard.press("Shift+Tab")
            assert page.locator("#guide-next").evaluate(
                "element => document.activeElement === element"
            ), "The guide did not wrap focus from its first to last control"

            page.keyboard.press("Escape")
            assert not dialog.evaluate("element => element.open")
            page.wait_for_function("document.activeElement === document.querySelector('#guide-opener')")
            assert page.locator("[data-workspace-drawer-toggle]").get_attribute("aria-expanded") == "true"
            assert page.locator("#workspace-sidebar").evaluate("element => !element.hasAttribute('inert')")
            assert page.locator("#outside-content").evaluate("element => element.hasAttribute('inert')")
        finally:
            browser.close()
