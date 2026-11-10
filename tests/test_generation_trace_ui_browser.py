from pathlib import Path

import pytest


playwright_sync = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


def test_generation_trace_loads_on_demand_renders_safely_and_clears_on_expiry():
    source = (ROOT / "app/static/js/transcribe/generationTrace.js").read_text(encoding="utf-8")
    source = source.replace("import { csrfFetch } from '../csrf.js';", "const csrfFetch = window.__csrfFetch;")
    tokens = (ROOT / "app/static/css/tokens.css").read_text(encoding="utf-8")
    styles = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in (
        "app/static/css/transcribe-tailwind.css",
        "app/static/css/components.css",
        "app/static/css/workspace.css",
        "app/static/css/transcribe.css",
        "app/static/css/transcribe-mobile.css",
    ))

    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(
            """<!doctype html><html><head></head><body class="workspace-page workspace-page--scribe">
            <div class="workspace-shell" data-workspace-shell style="height:100vh;min-height:0">
              <main data-workspace-scribe-main style="height:100vh;min-height:0;display:flex;flex-direction:column">
                <header class="transcribe-capture-header" style="flex:0 0 5rem">Scribe workspace</header>
                <section class="structured-workspace" style="height:auto;min-height:0;flex:1">
                  <div class="structured-workspace__body" data-generation-trace-scroll-container>
                    <div class="structured-workspace" style="height:auto;min-height:0">
                      <div data-latest-generated-id="doc-1"></div>
                      <div data-generated-structured-panel><textarea data-structured-line-input data-section-label="History">Visible editor  \nline</textarea></div>
                      <details class="generation-trace" data-generation-trace data-transcript-id="trace-test" data-enabled="true">
                        <summary data-generation-trace-summary><span>Generation trace</span><span class="generation-trace__state" data-generation-trace-indicator>Capture on · owner only</span></summary>
                        <div class="generation-trace__body">
                          <p class="generation-trace__notice" data-generation-trace-notice>When enabled, future traces show redacted provider requests and model responses. Only you can read them. Each trace expires within 24 hours, or earlier when this consultation expires or is deleted.</p>
                          <p class="generation-trace__notice" data-generation-trace-completeness>Trace history can be incomplete if capture was off, failed or expired. An event marked complete describes that event only; no event does not prove that no provider call was made.</p>
                          <p class="generation-trace__status" data-generation-trace-status role="status" aria-live="polite">Open this panel to load trace history.</p>
                          <button type="button" class="btn-ghost-sm" data-generation-trace-refresh>Refresh trace history</button>
                          <ol class="generation-trace__events" data-generation-trace-events aria-label="Generation trace events"></ol>
                          <section class="generation-trace__detail" data-generation-trace-detail aria-live="polite" hidden></section>
                          <button type="button" class="btn-ghost-sm" data-generation-trace-clear hidden>Clear traces for this consultation</button>
                        </div>
                      </details>
                    </div>
                  </div>
                </section>
              </main>
            </div></body></html>"""
        )
        page.add_style_tag(content=tokens + "\n" + styles)
        page.evaluate(
            """() => {
              window.__traceCalls = [];
                  window.__traceCleared = false;
                  window.__traceAvailable = false;
                  window.__deferDetail = false;
                  window.__csrfFetch = async (url, options = {}) => {
                    window.__traceCalls.push([url, options.method || 'GET']);
                    if (options.method === 'DELETE') { window.__traceCleared = true; return new Response(null, {status: 204}); }
                    if (url.endsWith('/ev-1') && window.__deferDetail) return new Promise(resolve => {
                      window.__resolveDetail = () => resolve(new Response(JSON.stringify({id:'ev-1', request:'STALE CONTENT'}), {status:200, headers:{'Content-Type':'application/json'}}));
                    });
                    if (url.endsWith('/ev-1')) return new Response(JSON.stringify({
                  id: 'ev-1', state: 'incomplete', generated_document_id: 'doc-1',
                  expires_at: new Date(Date.now() + 1500).toISOString(),
                  request: JSON.stringify({messages:[{role:'user', content:'line  one\\n\\tTabbed'}]}),
                  response: '<img src=x onerror=alert(1)>  ok\\nnext',
                  parsed_json_available: true, parsed_json_preview: {history: 'parsed'},
                  validation: 'invalid', error_code: 'schema_mismatch', decision: 'repair',
                  provider_model: 'model-test', provider_adapter: 'provider-test', retry_wait_ms: 1500,
                  correlation_id: 'corr-12345678', attempt_number: 2, repair_of_attempt_id: 'attempt-1'
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
                const events = window.__traceCleared || !window.__traceAvailable ? [] : [{
                  id: 'ev-1', workflow: 'template_generation', state: 'incomplete',
                  captured_at: new Date().toISOString(), expires_at: new Date(Date.now() + 1500).toISOString(),
                  generated_document_id: 'doc-1', request_ordinal: 2, provider_model: 'model-test', retry_wait_ms: 1500,
                  correlation_id: 'corr-12345678', attempt_number: 2, repair_of_attempt_id: 'attempt-1'
                }];
                return new Response(JSON.stringify({enabled: true, events,
                  missing_attempts: window.__traceAvailable ? [{attempt_id:'miss-1', request_ordinal:3, occurred_at:new Date().toISOString(), outcome:'timeout', retry_wait_ms:1000}] : [],
                  missing_attempts_bounded: true}), {status: 200, headers: {'Content-Type': 'application/json'}});
              };
            }"""
        )
        page.add_script_tag(content=source)

        assert page.evaluate("window.__traceCalls.length") == 0
        page.locator("[data-generation-trace] > summary").click()
        page.get_by_text("Capture is on. No traces have been recorded for this consultation yet.").wait_for()
        assert page.evaluate("window.__traceCalls.length") == 1
        assert page.locator("[data-generation-trace-detail]").evaluate("node => getComputedStyle(node).display") == "none"

        page.evaluate("window.__traceAvailable = true")
        page.locator("[data-generation-trace-refresh]").click()
        page.locator(".generation-trace__events button").wait_for()
        assert page.evaluate("window.__traceCalls.length") == 2
        assert "Capture unavailable (off, expired, or failed)" in page.locator("[data-generation-trace-events]").inner_text()
        assert "Only the most recent bounded set is shown." in page.locator("[data-generation-trace-events]").inner_text()

        page.locator(".generation-trace__events button").click()
        detail = page.locator("[data-generation-trace-detail]")
        detail.get_by_text("Current editor contents").wait_for()
        assert page.evaluate("window.__traceCalls.length") == 3
        assert "Workflow validation: invalid" in detail.inner_text()
        assert "Next: repair" in detail.inner_text()
        assert "Visible editor" in detail.inner_text()
        assert "schema_mismatch" in detail.inner_text()
        assert "Repair of attempt-" in page.locator(".generation-trace__events").inner_text()
        derived_prompt = detail.locator(".generation-trace__content").filter(has=page.locator("h3", has_text="Derived prompt text · user"))
        assert derived_prompt.locator("pre").inner_text() == "line  one\n\tTabbed"
        response_pre = detail.locator(".generation-trace__content").filter(has=page.locator("h3", has_text="Raw model response")).locator("pre")
        assert "<img src=x onerror=alert(1)>" in response_pre.inner_text()
        assert detail.locator("img").count() == 0
        detail.locator(".generation-trace__whitespace input").first.check()
        request_pre = detail.locator(".generation-trace__content").filter(has=page.locator("h3", has_text="Provider-bound request")).locator("pre")
        assert "·" in request_pre.inner_text()
        assert "\\t" in request_pre.inner_text()
        assert "\\n" in request_pre.inner_text()
        derived_prompt.locator(".generation-trace__whitespace input").check()
        assert "→" in derived_prompt.locator("pre").inner_text()
        assert "↵" in derived_prompt.locator("pre").inner_text()
        assert page.locator("[data-generation-trace-scroll-container]").evaluate("node => getComputedStyle(node).overflowY") == "auto"
        page.evaluate("""() => {
          const scroll = document.querySelector('[data-generation-trace-scroll-container]');
          scroll.style.height = `${scroll.scrollHeight}px`;
          scroll.style.overflow = 'visible';
          document.querySelector('[data-workspace-scribe-main]').style.height = 'auto';
          document.querySelector('[data-workspace-shell]').style.height = 'auto';
          document.body.style.height = 'auto';
          document.body.style.overflow = 'visible';
        }""")
        page.screenshot(path="/tmp/openscribe-generation-trace-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.evaluate("""() => {
          const scroll = document.querySelector('[data-generation-trace-scroll-container]');
          scroll.style.height = `${scroll.scrollHeight}px`;
        }""")
        page.screenshot(path="/tmp/openscribe-generation-trace-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})

        page.wait_for_function("document.querySelector('[data-generation-trace-detail]').hidden", timeout=3000)
        assert page.get_by_text("This trace has expired and its content has been cleared.").count() == 1
        assert "<img src=x onerror=alert(1)>" not in detail.inner_text()

        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("[data-generation-trace-refresh]").click()
        page.locator(".generation-trace__events button").wait_for()
        page.evaluate("window.__deferDetail = true")
        page.locator(".generation-trace__events button").click()
        page.wait_for_function("typeof window.__resolveDetail === 'function'")
        page.evaluate("document.dispatchEvent(new CustomEvent('transcribe:workspace-applied', {detail:{transcriptId:'other-consultation', generationTraceEnabled:true}}))")
        page.evaluate("window.__resolveDetail()")
        assert page.locator("[data-generation-trace]").evaluate("node => !node.hidden && !node.open && node.dataset.transcriptId === 'other-consultation'")
        assert page.locator("[data-generation-trace-detail]").evaluate("node => node.hidden && node.textContent === ''")
        assert "STALE CONTENT" not in detail.inner_text()
        page.evaluate("document.dispatchEvent(new CustomEvent('transcribe:workspace-applied', {detail:{transcriptId:'trace-test', generationTraceEnabled:true}}))")
        page.locator("[data-generation-trace] > summary").click()
        page.locator(".generation-trace__events button").wait_for()
        page.locator(".generation-trace__events button").click()
        page.wait_for_function("typeof window.__resolveDetail === 'function'")
        page.locator("[data-generation-trace-clear]").click()
        page.evaluate("window.__resolveDetail()")
        page.get_by_text("Some provider attempts were observed, but their trace content is unavailable.").wait_for()
        assert "STALE CONTENT" not in detail.inner_text()
        assert page.evaluate("window.__traceCalls.some(([, method]) => method === 'DELETE')")
        browser.close()
