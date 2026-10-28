"""Synthetic browser regressions for the system administrator area."""

from __future__ import annotations

import os
import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import (
    DeidentificationAdapterKind,
    LlmAdapterKind,
    ProviderFeatureType,
    ProviderUsageEvent,
    ProviderUsageEventType,
    QuotaPeriod,
    QuotaResource,
    TeamRole,
    Team,
    TeamLlmSelection,
    TeamStatus,
    TemplateScope,
    User,
    UserQuotaPolicyEvent,
    UserQuotaPolicyEventType,
    UserQuotaReasonCode,
    utcnow,
)
from tests.accessibility.audit import AXE_TAGS


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


@pytest.fixture
def synthetic_admin_context(
    db_session,
    make_team,
    make_user,
    make_stt_config,
    make_stt_selection,
    make_llm_config,
    make_llm_selection,
    make_deidentification_provider,
    make_deidentification_provider_assignment,
    make_deidentification_selection,
    make_default_template,
    make_default_quick_action,
):
    team = make_team(name="Accessibility Review Team")
    admin = make_user(
        email="admin-accessibility@example.test",
        password="synthetic-a11y-admin-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    member = make_user(
        email="member-accessibility@example.test",
        password="synthetic-a11y-member-password",
        team=team,
        team_role=TeamRole.user,
    )
    member.daily_token_limit = 1200
    member.monthly_token_limit = 24000
    member.daily_audio_seconds_limit = 7200
    member.monthly_audio_seconds_limit = 86400
    stt = make_stt_config(
        team=team,
        actor=admin,
        label="Synthetic STT provider",
        model_name="synthetic-whisper",
        available_models_json=["synthetic-whisper", "synthetic-whisper-fast"],
        has_secret=False,
    )
    make_stt_selection(
        config=stt,
        actor=admin,
        model_name_override="synthetic-whisper-fast",
        language_override="en-GB",
    )
    llm = make_llm_config(
        team=team,
        actor=admin,
        label="Synthetic LLM provider",
        model_name="synthetic-model",
        available_models_json=["synthetic-model", "synthetic-model-alt"],
        has_secret=False,
    )
    make_llm_selection(
        config=llm,
        actor=admin,
        allowed_models_json=["synthetic-model", "synthetic-model-alt"],
        model_name_override="synthetic-model-alt",
    )
    assigned_deid = make_deidentification_provider(
        actor=admin,
        label="Synthetic assigned De-ID",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid-fixture.invalid",
        detect_path="/detect",
        clinical_detection_enabled=True,
        has_secret=False,
    )
    available_deid = make_deidentification_provider(
        actor=admin,
        label="Synthetic available De-ID",
        adapter_kind=DeidentificationAdapterKind.generic_rest,
        base_url="https://deid-available-fixture.invalid",
        detect_path="/detect",
        has_secret=False,
    )
    make_deidentification_provider_assignment(
        team=team, provider=assigned_deid, actor=admin
    )
    make_deidentification_selection(team=team, provider=assigned_deid, actor=admin)
    make_default_template(actor=admin, name="Synthetic platform template")
    default_quick_action = make_default_quick_action(
        actor=admin, name="Synthetic platform quick action"
    )
    now = utcnow()
    db_session.add_all(
        ProviderUsageEvent(
            team_id=team.id,
            owner_user_id=member.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            provider_adapter="synthetic",
            model_name="synthetic-model",
            prompt_tokens=index + 1,
            completion_tokens=1,
            total_tokens=index + 2,
            created_at=now - timedelta(days=13 - index),
        )
        for index in range(14)
    )
    db_session.commit()
    return {
        "team_id": str(team.id),
        "member_id": str(member.id),
        "team_name": team.name,
        "stt_label": stt.label,
        "llm_label": llm.label,
        "deid_assigned_label": assigned_deid.label,
        "deid_available_label": available_deid.label,
        "deid_assigned_id": str(assigned_deid.id),
        "quick_action_id": str(default_quick_action.id),
        "member_email": member.email,
        "email": admin.email,
        "password": "synthetic-a11y-admin-password",
    }


def _open_admin(page, audit_report, admin) -> None:
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto("/login", wait_until="domcontentloaded")
    page.locator('form[action="/login"] input[name="email"]').fill(admin["email"])
    page.locator('form[action="/login"] input[name="password"]').fill(admin["password"])
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/login")
    ) as login_response:
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.get_by_role("button", name="Sign in", exact=True).click()
    assert page.locator('form[action="/login"]').count() == 0, (
        f"Synthetic system-admin login stayed on login page; POST status "
        f"{login_response.value.status}, redirect {login_response.value.headers.get('location')}; "
        f"visible login text: {page.locator('body').inner_text()}"
    )
    assert "openscribe_session" in {cookie["name"] for cookie in page.context.cookies()}, (
        "Same-origin sign-in redirect did not preserve the browser session cookie"
    )
    assert page.get_by_role("heading", name="Usage", exact=True).is_visible(), (
        "Validated system-admin redirect did not render the admin destination"
    )
    response = page.goto("/admin", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.get_by_role("main").is_visible()


def _scan_admin(page, report, state: str, axe_source: str, blocked: list[str]) -> dict:
    if not page.evaluate("() => Boolean(window.axe)"):
        page.add_script_tag(content=axe_source)
    audit = page.evaluate(
        """async tags => ({
          supportedRuleCounts: Object.fromEntries(tags.map(tag => [tag, window.axe.getRules([tag]).length])),
          result: await window.axe.run(document, {runOnly: {type: 'tag', values: tags}})
        })""",
        list(AXE_TAGS),
    )
    assert any(audit["supportedRuleCounts"].values()), "Local axe bundle has no requested WCAG rules"
    report.record_scan(
        state,
        audit["result"],
        external_requests=list(blocked),
        supported_rule_counts=audit["supportedRuleCounts"],
    )
    assert not blocked, "An external browser request was blocked; this admin state is incomplete"
    return audit["result"]


def _measure_horizontal_overflow(page, width: int) -> dict:
    page.set_viewport_size({"width": width, "height": 900})
    return page.evaluate("""() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll('body *')]
        .filter(element => element.getClientRects().length)
        .map(element => ({element, rect: element.getBoundingClientRect()}))
        .filter(({rect}) => rect.right > document.documentElement.clientWidth + 1)
        .slice(0, 8)
        .map(({element, rect}) => ({tag: element.tagName, className: String(element.className || ''), right: Math.round(rect.right)}))
    })""")


def _assert_no_horizontal_overflow(page, width: int) -> None:
    measurements = _measure_horizontal_overflow(page, width)
    assert measurements["scroll"] <= measurements["client"], (
        f"Admin content overflows at {width} CSS px: {measurements}"
    )


def _assert_usage_svgs_fit(page, width: int) -> None:
    page.wait_for_function("""() => [...document.querySelectorAll('.usage-chart-a11y')].every(wrapper => {
      const chart = wrapper.querySelector('.usage-echart');
      const svg = chart?.querySelector('svg');
      if (!chart || !svg) return false;
      const chartBox = chart.getBoundingClientRect();
      const svgBox = svg.getBoundingClientRect();
      return svgBox.width <= chartBox.width + 1 && svgBox.left >= chartBox.left - 1
        && svgBox.right <= chartBox.right + 1;
    })""", timeout=3_000)
    oversized = page.evaluate("""() => [...document.querySelectorAll('.usage-chart-a11y')].flatMap(wrapper => {
      const chart = wrapper.querySelector('.usage-echart');
      const svg = chart?.querySelector('svg');
      if (!chart || !svg) return [];
      const chartBox = chart.getBoundingClientRect();
      const svgBox = svg.getBoundingClientRect();
      return svgBox.width > chartBox.width + 1 || svgBox.left < chartBox.left - 1
        || svgBox.right > chartBox.right + 1
        ? [{chart: chart.id, chartWidth: Math.round(chartBox.width), svgWidth: Math.round(svgBox.width), svgRight: Math.round(svgBox.right), chartRight: Math.round(chartBox.right)}]
        : [];
    })""")
    assert not oversized, f"Rendered chart SVG exceeds its container at {width}px: {oversized}"


def _assert_keyboard_focus_indicator(locator) -> None:
    assert locator.evaluate("""el => {
      const style = getComputedStyle(el);
      const hasOutline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
      const hasShadow = style.boxShadow !== 'none';
      return el.matches(':focus-visible') && (hasOutline || hasShadow);
    }"""), "Keyboard focus is not visibly indicated"


def _capture_admin_evidence(page, filename: str) -> None:
    directory = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if not directory:
        return
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
    page.wait_for_timeout(100)
    page.screenshot(path=str(output / filename), full_page=True, animations="disabled")


def test_admin_routes_render_requested_context_and_pass_axe(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)

    # Each URL is a canonical server-rendered state. Check the heading and
    # selected context so a redirect/fallback cannot masquerade as coverage.
    global_routes = (
        ("/admin?tab=requests", "Account requests", "global_requests"),
        ("/admin?tab=system-admins", "System administrators", "global_system_admins"),
        ("/admin?tab=global-defaults", "Global defaults", "global_defaults"),
        ("/admin?tab=deid-providers", "De-ID providers", "global_deid_providers"),
        ("/admin?tab=audit", "Security audit", "global_audit"),
        ("/admin?tab=legal", "Legal content", "global_legal"),
        ("/admin/usage?view=team&range=30d", "Usage", "global_usage"),
    )
    for path, heading, state in global_routes:
        response = page.goto(path, wait_until="domcontentloaded")
        assert response is not None and response.status == 200, f"Failed route {path}"
        assert page.get_by_role("heading", name=heading, exact=False).first.is_visible(), path
        result = _scan_admin(page, audit_report, state, axe_source, blocked)
        assert not result.get("violations"), f"axe violations for {state}: {result.get('violations')}"


def test_admin_global_forms_and_team_deid_reflow_with_synthetic_content(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    """Check dense list-first admin views and keyboard-open creation forms."""
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    cases = (
        ("system-admins", "/admin?tab=system-admins", "System administrators", "global-records-page", "create-system-admin-disclosure"),
        ("global-defaults", "/admin?tab=global-defaults", "Global defaults", "global-defaults-page"),
        (
            "global-deid",
            f"/admin?tab=deid-providers&deidentification_provider_id={admin['deid_assigned_id']}",
            "De-ID providers",
            "deid-registry-layout",
        ),
        (
            "team-deid",
            f"/admin?team_id={admin['team_id']}&team_tab=deidentification",
            "De-identification providers",
            "team-deid-lists",
        ),
    )
    widths = (1440, 1024, 768, 390, 320)
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    density_metrics = {"pages": {}, "defaults": {}}
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    for case in cases:
        state, path, heading, layout_class = case[:4]
        response = page.goto(path, wait_until="domcontentloaded")
        assert response is not None and response.status == 200, path
        assert page.get_by_role("heading", name=heading, exact=False).first.is_visible(), path
        layout = page.locator(f".{layout_class}")
        assert layout.is_visible(), f"Expected populated admin layout missing: {state}"

        if state == "global-defaults":
            default_sections = layout.locator(":scope > .global-defaults-section")
            template_row = default_sections.nth(0).locator(".inspection-row").filter(
                has_text="Synthetic platform template"
            )
            quick_action_row = default_sections.nth(1).locator(".inspection-row").filter(
                has_text="Synthetic platform quick action"
            )
            assert template_row.is_visible() and template_row.get_by_role("link", name="Edit").is_visible()
            assert quick_action_row.is_visible() and quick_action_row.get_by_role("link", name="Edit").is_visible()
            quick_action_disclosure = page.locator("#default-quick-action-disclosure")
            assert not quick_action_disclosure.get_attribute("open"), "Create Quick Action should start collapsed"
            assert quick_action_disclosure.locator(":scope > summary").inner_text().strip() == "Create quick action"
            assert not quick_action_disclosure.locator("form").is_visible()
        elif state == "system-admins":
            assert layout.locator(".global-admin-list").get_by_text(
                admin["email"], exact=True
            ).is_visible()
        elif state == "global-deid":
            assert page.get_by_text(admin["deid_assigned_label"], exact=True).is_visible()
            assert page.get_by_role("heading", name="Connection", exact=True).is_visible()
            assert page.get_by_role("heading", name="Request and response mapping", exact=True).is_visible()
            token = page.locator('form.deid-provider-form input[name="bearer_token"]')
            assert token.is_visible() and token.input_value() == "" and token.get_attribute("type") == "password"
            assert page.get_by_label("Clinical NLP capable", exact=True).is_visible()
        elif state == "team-deid":
            assert page.get_by_role("heading", name="Assigned providers", exact=True).is_visible()
            assert page.get_by_role("heading", name="Available global providers", exact=True).is_visible()
            deid_lists = layout.locator(":scope > .global-admin-panel")
            assert deid_lists.nth(0).get_by_text(admin["deid_assigned_label"], exact=True).is_visible()
            assert deid_lists.nth(1).get_by_text(admin["deid_available_label"], exact=True).is_visible()

        create_disclosure = None
        if state == "system-admins":
            create_disclosure = page.locator(f"#{case[4]}")
            assert create_disclosure.is_visible()
            assert not create_disclosure.get_attribute("open"), f"{state} create form should start collapsed"
            assert not create_disclosure.locator("form").is_visible()
        elif state == "global-defaults":
            create_disclosure = quick_action_disclosure

        for width in widths:
            page.set_viewport_size({"width": width, "height": 900})
            page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
            _assert_no_horizontal_overflow(page, width)
            if width == 390 and state in {"system-admins", "global-defaults", "global-deid"}:
                density_metrics["pages"][state] = page.evaluate(
                    """() => {
                      const selectors = ['.global-records-page', '.global-defaults-page', '.deid-registry-layout'];
                      const content = [...document.querySelectorAll(selectors.join(','))]
                        .filter(el => el.getClientRects().length)
                        .reduce((bottom, el) => Math.max(bottom, el.getBoundingClientRect().bottom + scrollY), 0);
                      const footer = document.querySelector('.operator-legal-footer')?.getBoundingClientRect();
                      return {contentBottom: Math.round(content), footerGap: footer ? Math.round(footer.top - content) : null};
                    }"""
                )
                if state == "global-defaults":
                    density_metrics["defaults"]["closedContentBottom390"] = page.evaluate(
                        """() => Math.round(document.querySelector('.global-defaults-page').getBoundingClientRect().bottom + scrollY)"""
                    )

            # Every visible form control has a programmatic label, and long
            # text/number values remain inside their input rather than clipping.
            visible_forms = page.locator(
                ".global-admin-form, .deid-provider-form, .global-teams-layout form, "
                ".global-system-admin-layout form"
            )
            for form in visible_forms.all():
                if not form.is_visible():
                    continue
                unnamed = form.locator("input:not([type=hidden]), select, textarea").evaluate_all(
                    """elements => elements.filter(el => {
                      if (el.type === 'submit' || el.type === 'button') return false;
                      return !el.labels?.length && !el.getAttribute('aria-label')
                        && !el.getAttribute('aria-labelledby');
                    }).map(el => ({tag: el.tagName, name: el.name || '', type: el.type || ''}))"""
                )
                assert not unnamed, f"Unlabeled controls in {state} at {width}px: {unnamed}"
                clipped = form.locator("input:not([type=hidden]), select, textarea").evaluate_all(
                    """elements => elements.filter(el => {
                      if (!el.getClientRects().length) return false;
                      if (el.type === 'checkbox' || el.type === 'radio') return false;
                      const box = el.getBoundingClientRect();
                      return box.width < 120 || box.right > document.documentElement.clientWidth + 1;
                    }).map(el => ({name: el.name || '', width: Math.round(el.getBoundingClientRect().width)}))"""
                )
                assert not clipped, f"Form controls are too narrow/offscreen in {state} at {width}px: {clipped}"

            if screenshot_dir:
                page.screenshot(
                    path=str(Path(screenshot_dir) / f"admin-{state}-{width}.png"),
                    full_page=True,
                    animations="disabled",
                )
                if state == "global-defaults" and width == 1440:
                    layout.screenshot(
                        path=str(Path(screenshot_dir) / "admin-global-defaults-content-1440.png"),
                        animations="disabled",
                    )
            if state == "global-defaults":
                for row in (template_row, quick_action_row):
                    assert row.get_by_role("button", name="Duplicate").is_visible()
                    assert row.get_by_role("button", name="Delete").is_visible()
                    row_box = row.bounding_box()
                    assert row_box and row_box["x"] >= 0 and row_box["x"] + row_box["width"] <= width
                    if width <= 390:
                        identity_box = row.locator("span").first.bounding_box()
                        actions_box = row.locator(".quick-actions").bounding_box()
                        assert identity_box and actions_box
                        assert identity_box["x"] + identity_box["width"] <= actions_box["x"] + 1, (
                            f"Default asset identity overlaps actions on mobile at {width}px"
                        )
                        assert actions_box["x"] + actions_box["width"] <= width
                if width >= 1100:
                    templates_card = layout.locator(":scope > .global-defaults-section").first
                    card_box = templates_card.bounding_box()
                    row_box = template_row.bounding_box()
                    assert card_box and row_box
                    assert card_box["height"] <= 300 and row_box["y"] - card_box["y"] <= 220, (
                        "The Templates card should fit its content instead of stretching to the Quick actions form"
                    )
            if state in {"system-admins", "global-defaults"} and width in (1440, 390):
                assert create_disclosure is not None
                summary = create_disclosure.locator(":scope > summary")
                summary.focus()
                page.keyboard.press("Enter")
                assert create_disclosure.get_attribute("open") is not None
                creation_form = create_disclosure.locator("form.global-admin-form")
                assert creation_form.is_visible()
                unnamed = creation_form.locator("input:not([type=hidden]), select, textarea").evaluate_all(
                    """elements => elements.filter(el => !el.labels?.length
                      && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
                      .map(el => ({name: el.name || '', type: el.type || ''}))"""
                )
                assert not unnamed, f"Unlabeled opened {state} fields: {unnamed}"
                narrow_fields = creation_form.locator("input:not([type=hidden]), select, textarea").evaluate_all(
                    """elements => elements.filter(el => el.type !== 'checkbox' && el.type !== 'radio'
                      && el.getClientRects().length && el.getBoundingClientRect().width < 120)
                      .map(el => ({name: el.name || '', width: Math.round(el.getBoundingClientRect().width)}))"""
                )
                assert not narrow_fields, f"Opened {state} form fields are too narrow: {narrow_fields}"
                if state == "system-admins":
                    password = creation_form.locator('input[name="temporary_password"]')
                    assert password.is_visible() and password.input_value() == ""
                    assert password.get_attribute("required") is not None
                    show = page.get_by_role("button", name="Show temporary password")
                    show.focus()
                    page.keyboard.press("Enter")
                    assert password.get_attribute("type") == "text" and password.input_value() == ""
                    hide = page.get_by_role("button", name="Hide temporary password")
                    hide.focus()
                    page.keyboard.press("Enter")
                    assert password.get_attribute("type") == "password" and password.input_value() == ""
                if screenshot_dir:
                    page.screenshot(
                        path=str(Path(screenshot_dir) / f"admin-{state}-create-open-{width}.png"),
                        full_page=True,
                        animations="disabled",
                    )
                if state == "global-defaults" and width == 390:
                    density_metrics["defaults"]["openContentBottom390"] = page.evaluate(
                        """() => Math.round(document.querySelector('.global-defaults-page').getBoundingClientRect().bottom + scrollY)"""
                    )
                summary.focus()
                page.keyboard.press("Enter")
                assert not create_disclosure.get_attribute("open")
                if width == 390:
                    gap = page.evaluate("""() => {
                      const content = [...document.querySelectorAll('.global-records-page, .global-defaults-page')]
                        .filter(el => el.getClientRects().length)
                        .reduce((bottom, el) => Math.max(bottom, el.getBoundingClientRect().bottom), 0);
                      const footer = document.querySelector('.operator-legal-footer')?.getBoundingClientRect();
                      return footer ? Math.round(footer.top - content) : null;
                    }""")
                    assert gap is not None and gap <= 240, (
                        f"Unexpected mobile blank gap before admin legal footer for {state}: {gap}px"
                    )
            if width in (1440, 320):
                result = _scan_admin(page, audit_report, f"{state}_{width}", axe_source, blocked)
                assert not result.get("violations"), (
                    f"axe violations for {state} at {width}px: {result.get('violations')}"
                )

    # The edit state should reveal the same Quick Action form with its
    # synthetic values, while the create state remains closed by default.
    edit_path = f"/admin?tab=global-defaults&default_quick_action_id={admin['quick_action_id']}"
    page.goto(edit_path, wait_until="domcontentloaded")
    edit_disclosure = page.locator("#default-quick-action-disclosure")
    assert edit_disclosure.get_attribute("open") is not None
    assert edit_disclosure.locator(":scope > summary").inner_text().strip() == "Edit quick action"
    assert edit_disclosure.locator('input[name="name"]').input_value() == "Synthetic platform quick action"
    assert edit_disclosure.locator('textarea[name="prompt_text"]').input_value()
    if screenshot_dir:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        page.screenshot(
            path=str(Path(screenshot_dir) / "admin-global-defaults-edit-open-1440.png"),
            full_page=True,
            animations="disabled",
        )
        Path(screenshot_dir, "density-metrics.json").write_text(
            json.dumps(density_metrics, indent=2) + "\n", encoding="utf-8"
        )


def test_admin_global_defaults_and_team_deid_empty_states_are_clear(
    audit_report, audit_browser, make_team, make_user, axe_source
):
    page, blocked = audit_browser
    team = make_team(name="Empty De-ID Review Team")
    admin = make_user(
        email="empty-admin-accessibility@example.test",
        password="synthetic-empty-admin-password",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    browser_admin = {"email": admin.email, "password": "synthetic-empty-admin-password"}
    _open_admin(page, audit_report, browser_admin)

    for width in (1440, 320):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("/admin?tab=global-defaults", wait_until="domcontentloaded")
        assert page.get_by_text("No default templates yet.", exact=True).is_visible()
        assert page.get_by_text("No default quick actions yet.", exact=True).is_visible()
        result = _scan_admin(page, audit_report, f"global_defaults_empty_{width}", axe_source, blocked)
        assert not result.get("violations"), f"axe violations for empty defaults: {result.get('violations')}"

        page.goto(f"/admin?team_id={team.id}&team_tab=deidentification", wait_until="domcontentloaded")
        assert page.get_by_text("No global providers attached.", exact=True).is_visible()
        assert page.get_by_text("No global providers available.", exact=True).is_visible()
        result = _scan_admin(page, audit_report, f"team_deid_empty_{width}", axe_source, blocked)
        assert not result.get("violations"), f"axe violations for empty team De-ID lists: {result.get('violations')}"


def test_team_usage_views_keep_scope_ranges_and_quota_navigation(
    audit_report,
    audit_browser,
    synthetic_admin_context,
    make_team,
    make_user,
    db_session,
    axe_source,
):
    """Team Usage tabs preserve aggregate scope and usable navigation without JS routing."""
    page, blocked = audit_browser
    admin = synthetic_admin_context
    other_team = make_team(name="Other synthetic usage team")
    other_member = make_user(
        email="other-usage-member@example.test",
        password="synthetic-other-member-password",
        team=other_team,
        team_role=TeamRole.user,
    )
    db_session.add(
        ProviderUsageEvent(
            team_id=other_team.id,
            owner_user_id=other_member.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed,
            provider_adapter="synthetic",
            model_name="other-synthetic-model",
            prompt_tokens=9001,
            completion_tokens=99,
            total_tokens=9100,
            created_at=utcnow() - timedelta(days=2),
        )
    )
    db_session.commit()
    _open_admin(page, audit_report, admin)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(getattr(error, "stack", str(error))))
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    global_path = "/admin/usage?range=90d"
    response = page.goto(global_path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.get_by_role("heading", name="Usage", exact=True).is_visible()
    assert page.locator("nav[aria-label='Team usage views']").count() == 0
    assert page.locator("[data-team-navigation], .team-summary").count() == 0
    assert page.locator("nav[aria-label='Team settings']").count() == 0
    assert page.locator("nav.tabs").count() == 0
    assert page.get_by_text(admin["member_email"], exact=True).count() == 0
    assert page.get_by_text(other_member.email, exact=True).count() == 0
    global_summary = page.get_by_role("region", name="Usage summary table")
    assert global_summary.get_by_role("table").get_by_role("columnheader").all_inner_texts() == [
        "Total tokens", "Audio hours", "Input tokens", "Output tokens", "Generations"
    ]
    assert "all teams" in global_summary.get_by_role("table").locator("caption").inner_text()
    assert page.locator(".usage-intro select[name='team_id']").count() == 0
    assert page.get_by_role("button", name="Apply scope").count() == 0
    global_range = page.locator("details.usage-range-disclosure")
    assert global_range.count() == 1 and not global_range.get_attribute("open")
    assert global_range.locator("summary").inner_text().replace("\n", " ").strip() == "Reporting range Last 90 days"
    global_options = global_range.locator("a.usage-range-option")
    assert global_options.count() == 4
    assert global_options.nth(0).get_attribute("href") == "/admin/usage?view=team&range=30d"
    assert global_range.locator("a[aria-current='page']").get_attribute("href") == (
        "/admin/usage?view=team&range=90d"
    )
    assert global_range.locator("a[href='/admin/usage?view=team&range=1y']").count() == 1
    assert global_range.locator("a[href='/admin/usage?view=team&range=all']").count() == 1
    assert page.locator("form.usage-range-scope-form, select[name='range']").count() == 0
    assert page.locator(
        f".sidebar-team-link[href='/admin/usage?team_id={admin['team_id']}&view=team&range=90d']"
    ).count() == 1

    widths = (1440, 1024, 768, 390, 320)
    usage_density_metrics = {}
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        _assert_no_horizontal_overflow(page, width)
        _assert_usage_svgs_fit(page, width)
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"global-usage-{width}.png"),
                full_page=True,
                animations="disabled",
            )
    for width in (1440, 320):
        page.set_viewport_size({"width": width, "height": 900})
        result = _scan_admin(page, audit_report, f"global_usage_views_{width}", axe_source, blocked)
        assert not result.get("violations"), f"axe violations in global Usage: {result.get('violations')}"

    # The disclosure is an ordinary native details control with direct GET
    # links. It remains keyboard operable, dismisses on Escape/outside click,
    # and keeps its popup within narrow viewports.
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 720})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        disclosure = page.locator("details.usage-range-disclosure")
        summary = disclosure.locator("summary")
        summary.focus()
        page.keyboard.press("Enter")
        assert disclosure.get_attribute("open") is not None
        menu = disclosure.locator("nav.usage-range-menu")
        assert menu.is_visible()
        bounds = menu.bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        if width == 1440:
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 720, (
                f"Desktop reporting range menu exceeds the viewport: {bounds}"
            )
        assert menu.locator("a.usage-range-option").count() == 4
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"global-range-menu-open-{width}.png"),
                full_page=False,
                animations="disabled",
            )
        page.keyboard.press("Escape")
        assert disclosure.get_attribute("open") is None
        assert summary.evaluate("el => document.activeElement === el")
        summary.click()
        page.locator("h1").click(position={"x": 5, "y": 5})
        assert disclosure.get_attribute("open") is None
        if width == 1440 and screenshot_dir:
            page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
            page.screenshot(
                path=str(Path(screenshot_dir) / "global-range-menu-closed-1440.png"),
                full_page=False,
                animations="disabled",
            )

    team_path = f"/admin/usage?team_id={admin['team_id']}&view=team&range=90d"
    response = page.goto(team_path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.locator("[aria-label='Usage workspace']").count() == 1
    assert page.locator("[data-team-navigation], .team-summary, nav[aria-label='Team settings'], nav.tabs").count() == 0
    team_summary = page.get_by_role("region", name="Usage summary table")
    team_table = team_summary.get_by_role("table")
    assert team_table.get_by_role("columnheader").all_inner_texts() == [
        "Total tokens", "Audio hours", "Input tokens", "Output tokens", "Generations"
    ]
    assert team_table.locator("caption").inner_text() == (
        "Usage summary for " + admin["team_name"] + " · Last 90 days"
    )
    assert team_table.locator("tbody tr").first.locator("td").all_inner_texts() == [
        "119", "0.00 h", "105", "14", "14"
    ]
    assert team_summary.get_attribute("tabindex") == "0"
    assert page.locator(".usage-intro select[name='team_id']").count() == 0
    assert page.get_by_role("button", name="Apply scope").count() == 0
    team_range = page.locator("details.usage-range-disclosure")
    assert team_range.count() == 1
    assert team_range.locator("a[aria-current='page']").get_attribute("href") == (
        f"/admin/usage?view=team&team_id={admin['team_id']}&range=90d"
    )
    assert team_range.locator(
        f"a[href='/admin/usage?view=team&team_id={admin['team_id']}&range=30d']"
    ).count() == 1
    assert page.locator("form.usage-range-scope-form, select[name='range']").count() == 0
    usage_nav = page.get_by_role("navigation", name="Team usage views")
    team_link = usage_nav.get_by_role("link", name="Team", exact=True)
    users_link = usage_nav.get_by_role("link", name="Users", exact=True)
    quotas_link = usage_nav.get_by_role("link", name="Quotas", exact=True)
    assert team_link.get_attribute("aria-current") == "page"
    assert "range=90d" in team_link.get_attribute("href")
    assert "view=users" in users_link.get_attribute("href")
    assert "range=90d" in users_link.get_attribute("href")
    assert "view=quotas" in quotas_link.get_attribute("href")
    assert "range=90d" in quotas_link.get_attribute("href")
    assert page.locator(".usage-chart").count() == 4
    assert page.get_by_role("heading", name="Operational health · last 15 minutes").is_visible()
    assert page.get_by_role("heading", name="Recent failures and retries").is_visible()
    assert page.get_by_role("heading", name="Recent split failures").is_visible()
    assert page.get_by_role("heading", name="User usage breakdown").count() == 0
    assert page.locator(".usage-user-table").count() == 0
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        _assert_no_horizontal_overflow(page, width)
        _assert_usage_svgs_fit(page, width)
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"team-usage-{width}.png"),
                full_page=True,
                animations="disabled",
            )
            if width in (1440, 390, 320):
                team_range.locator("summary").click()
                page.screenshot(
                    path=str(Path(screenshot_dir) / f"team-range-menu-open-{width}.png"),
                    full_page=False,
                    animations="disabled",
                )
                team_range.locator("summary").click()
            if width in (1440, 390, 320):
                team_summary.screenshot(
                    path=str(Path(screenshot_dir) / f"team-usage-summary-{width}.png"),
                    animations="disabled",
                )
            if width == 1440:
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
                page.screenshot(
                    path=str(Path(screenshot_dir) / "team-usage-top-1440.png"),
                    full_page=False,
                    animations="disabled",
                )
                page.set_viewport_size({"width": width, "height": 900})
            if width == 390:
                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
                page.screenshot(
                    path=str(Path(screenshot_dir) / "team-usage-top-390.png"),
                    full_page=False,
                    animations="disabled",
                )
                page.set_viewport_size({"width": width, "height": 900})
    for width in (1440, 320):
        page.set_viewport_size({"width": width, "height": 900})
        result = _scan_admin(page, audit_report, f"team_usage_views_{width}", axe_source, blocked)
        assert not result.get("violations"), f"axe violations in Team Usage: {result.get('violations')}"

    # Native details and inputs keep the charts operable without pointer-only
    # interaction; toggling a named series must update the rendered chart.
    chart_controls = page.locator("details.usage-chart-controls").first
    if chart_controls.count() == 0:
        chart_controls = page.locator("details").filter(has_text="Keyboard chart controls").first
    chart_controls.locator("summary").click()
    first_chart = page.locator('[data-usage-chart-scope="team"][data-usage-chart="input"]')
    assert first_chart.is_visible()
    series_toggle = chart_controls.locator('input[type="checkbox"]').first
    assert series_toggle.is_visible()
    series_toggle.focus()
    series_toggle.press("Space")
    assert series_toggle.is_checked() is False
    assert first_chart.get_attribute("aria-describedby")

    users_href = users_link.get_attribute("href")
    response = page.goto(users_href, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert "view=users" in page.url and "range=90d" in page.url
    assert page.get_by_role("heading", name="User usage", exact=True).is_visible()
    assert page.get_by_role("heading", name="User usage breakdown").is_visible()
    assert page.get_by_role("navigation", name="Team usage views").get_by_role(
        "link", name="Users", exact=True
    ).get_attribute("aria-current") == "page"
    user_region = page.get_by_role("region", name="Scrollable user usage table")
    user_table = user_region.get_by_role("table")
    assert user_table.is_visible()
    assert user_region.get_attribute("tabindex") == "0"
    assert user_table.get_by_text(admin["member_email"], exact=True).count() == 2
    assert user_table.get_by_text(other_member.email, exact=True).count() == 0
    assert page.locator(".usage-chart").count() == 0
    assert page.locator(".usage-intro select[name='team_id']").count() == 0
    assert page.get_by_role("button", name="Apply scope").count() == 0
    assert page.get_by_role("heading", name="Operational health · last 15 minutes").count() == 0
    assert page.get_by_role("heading", name="Recent failures and retries").count() == 0
    if screenshot_dir:
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 720})
            page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
            page.locator("details.usage-range-disclosure summary").click()
            users_menu = page.locator("details.usage-range-disclosure nav.usage-range-menu")
            users_bounds = users_menu.bounding_box()
            assert users_bounds and users_bounds["x"] >= 0 and users_bounds["x"] + users_bounds["width"] <= width
            page.screenshot(
                path=str(Path(screenshot_dir) / f"users-range-menu-open-{width}.png"),
                full_page=width in (390, 320),
                animations="disabled",
            )
            last_range_option = users_menu.locator("a.usage-range-option").last
            last_range_option.scroll_into_view_if_needed()
            assert last_range_option.is_visible(), f"The final range option is unreachable at {width}px"
            _assert_no_horizontal_overflow(page, width)
            page.locator("details.usage-range-disclosure summary").click()
        page.set_viewport_size({"width": 1440, "height": 900})
    users_range = page.locator("details.usage-range-disclosure")
    assert users_range.locator("a[aria-current='page']").get_attribute("href") == (
        f"/admin/usage?view=users&team_id={admin['team_id']}&range=90d"
    )
    users_range.locator("summary").click()
    assert users_range.locator(
        f"a[href='/admin/usage?view=users&team_id={admin['team_id']}&range=1y']"
    ).count() == 1
    with page.expect_navigation(wait_until="domcontentloaded"):
        users_range.get_by_role("link", name="Last year").click()
    assert "view=users" in page.url and "range=1y" in page.url
    assert page.locator("details.usage-range-disclosure a[aria-current='page']").get_attribute("href") == (
        f"/admin/usage?view=users&team_id={admin['team_id']}&range=1y"
    )

    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        _assert_no_horizontal_overflow(page, width)
        if width == 390:
            gap = page.evaluate("""() => {
              const content = document.querySelector('.usage-page')?.getBoundingClientRect();
              const footer = document.querySelector('.operator-legal-footer')?.getBoundingClientRect();
              return content && footer ? Math.round(footer.top - content.bottom) : null;
            }""")
            usage_density_metrics["users390FooterGapPx"] = gap
            assert gap is not None and gap <= 240, (
                f"Unexpected blank gap before the admin footer on the mobile Users view: {gap}px"
            )
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"team-users-{width}.png"),
                full_page=True,
                animations="disabled",
            )
            if width == 1440:
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
                page.screenshot(
                    path=str(Path(screenshot_dir) / "team-users-top-1440.png"),
                    full_page=False,
                    animations="disabled",
                )
                page.set_viewport_size({"width": width, "height": 900})
    for width in (1440, 320):
        page.set_viewport_size({"width": width, "height": 900})
        result = _scan_admin(page, audit_report, f"team_usage_users_{width}", axe_source, blocked)
        assert not result.get("violations"), f"axe violations in Team Users view: {result.get('violations')}"

    # Quotas reuses the same range-aware view links, without inheriting the
    # selected user or dropping the validated team context.
    page.get_by_role("navigation", name="Team usage views").get_by_role(
        "link", name="Quotas", exact=True
    ).click()
    assert "view=quotas" in page.url and "range=1y" in page.url
    assert "member_id" not in page.url
    assert page.locator('[data-panel="quotas"]').is_visible()
    assert page.locator(".usage-intro select[name='team_id']").count() == 0
    assert page.get_by_role("button", name="Apply scope").count() == 0
    assert page.locator("details.usage-range-disclosure").count() == 0
    if screenshot_dir:
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
            page.screenshot(
                path=str(Path(screenshot_dir) / f"quotas-no-range-menu-{width}.png"),
                full_page=False,
                animations="disabled",
            )
    quota_nav = page.get_by_role("navigation", name="Team usage views")
    assert quota_nav.get_by_role("link", name="Quotas", exact=True).get_attribute("aria-current") == "page"
    assert "range=1y" in quota_nav.get_by_role("link", name="Team", exact=True).get_attribute("href")
    assert "view=users" in quota_nav.get_by_role("link", name="Users", exact=True).get_attribute("href")
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        _assert_no_horizontal_overflow(page, width)
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"team-quotas-{width}.png"),
                full_page=True,
                animations="disabled",
            )
    selected_quotas_path = (
        f"/admin/usage?team_id={admin['team_id']}&view=quotas"
        f"&member_id={admin['member_id']}&range=90d"
    )
    response = page.goto(selected_quotas_path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.locator("[data-panel='quotas']").is_visible()
    assert page.locator("[aria-label='Usage workspace']").count() == 1
    assert page.locator("[data-team-navigation], .team-summary, nav[aria-label='Team settings'], nav.tabs").count() == 0
    assert page.locator(".usage-chart, .usage-user-table").count() == 0
    assert page.get_by_role("heading", name=admin["member_email"]).is_visible()
    assert page.locator(".usage-intro select[name='team_id']").count() == 0
    assert page.get_by_role("button", name="Apply scope").count() == 0
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        _assert_no_horizontal_overflow(page, width)
        if screenshot_dir:
            page.screenshot(
                path=str(Path(screenshot_dir) / f"team-quotas-member-{width}.png"),
                full_page=True,
                animations="disabled",
            )
    response = page.go_back(wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert "view=quotas" in page.url and "range=1y" in page.url
    response = page.go_back(wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert "view=users" in page.url and "range=1y" in page.url
    assert page.get_by_role("navigation", name="Team usage views").get_by_role(
        "link", name="Users", exact=True
    ).get_attribute("aria-current") == "page"
    quota_context_path = (
        f"/admin/usage?team_id={admin['team_id']}&view=quotas&member_id={admin['member_id']}"
        "&quota_edit=limits&range=90d&reference=11111111-1111-4111-8111-111111111111"
        "&monitor_since=2026-10-20T12%3A00"
    )
    team = db_session.get(Team, UUID(admin["team_id"]))
    member = db_session.get(User, UUID(admin["member_id"]))
    selection = db_session.scalar(
        select(TeamLlmSelection).where(TeamLlmSelection.team_id == UUID(admin["team_id"]))
    )
    assert team is not None and member is not None and selection is not None and selection.config is not None
    model = "publishers/google/models/gemini-quota-ui-test"
    selection.config.adapter_kind = LlmAdapterKind.gemini_enterprise
    selection.config.model_name = model
    selection.config.available_models_json = [model]
    selection.allowed_models_json = [model]
    selection.model_name_override = model
    member.daily_token_limit = 10_000
    member.monthly_token_limit = 30_000
    team.default_daily_token_limit = 10_000
    team.default_monthly_token_limit = 30_000
    db_session.commit()

    quota_edit_path = (
        f"/admin/usage?team_id={admin['team_id']}&view=quotas&member_id={admin['member_id']}"
        "&quota_edit=limits&range=90d"
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    response = page.goto(quota_edit_path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    daily_warning = page.locator("#quota-token-warning-daily")
    assert daily_warning.is_visible()
    assert f"{model} can reserve up to 30,000 tokens for output" in daily_warning.inner_text()
    assert "for output in one generation or check" in daily_warning.inner_text()
    assert "plus variable input tokens" in daily_warning.inner_text()
    daily_limit = page.locator("#quota-daily_token_limit")
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        _assert_no_horizontal_overflow(page, width)
        if screenshot_dir and width in (1440, 390):
            page.screenshot(
                path=str(Path(screenshot_dir) / f"quota-reservation-warning-{width}.png"),
                full_page=True,
                animations="disabled",
            )
    page.locator('input[name="daily_token_unlimited"][value="true"]').check()
    assert not daily_warning.is_visible()
    page.locator('input[name="daily_token_unlimited"][value=""]').check()
    assert daily_warning.is_visible()
    daily_limit.fill("30001")
    assert not daily_warning.is_visible()
    daily_limit.fill("10000")
    assert daily_warning.is_visible()
    assert "for output in one generation or check" in daily_warning.inner_text()

    response = page.goto(quota_context_path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expected_team_scope_link = f"/admin/usage?team_id={other_team.id}&view=quotas&range=90d"
    scoped_team_link = page.locator(f".sidebar-team-link[href='{expected_team_scope_link}']")
    assert scoped_team_link.count() == 1
    all_teams_scope_link = page.locator(".sidebar-team-link[href='/admin/usage?view=team&range=90d']")
    assert all_teams_scope_link.count() == 1
    page.locator("#admin-team-switcher summary").click()
    with page.expect_navigation(wait_until="domcontentloaded"):
        scoped_team_link.click()
    assert f"team_id={other_team.id}" in page.url and "view=quotas" in page.url and "range=90d" in page.url
    assert "member_id" not in page.url and "quota_edit" not in page.url
    assert "reference" not in page.url and "monitor_since" not in page.url
    assert page.locator("#quota-panel").count() == 0
    page.locator("#admin-team-switcher summary").click()
    assert other_team.name in page.locator(".sidebar-team-link[aria-current='page']").inner_text()
    if page_errors:
        lines = page.content().splitlines()
        current_url = page.url
        excerpts = {}
        for error in page_errors:
            match = re.search(rf"{re.escape(current_url)}:(\d+):\d+", error)
            if match:
                line = int(match.group(1))
                excerpts[line] = "\n".join(lines[max(0, line - 3):line + 2])
        assert not page_errors, f"Runtime JavaScript errors on standalone Usage routes: {page_errors}; current-page excerpts: {excerpts}"
    if screenshot_dir:
        Path(screenshot_dir, "usage-density-metrics.json").write_text(
            json.dumps(usage_density_metrics, indent=2) + "\n", encoding="utf-8"
        )


def test_team_navigation_disclosures_retain_routes_and_keyboard_state(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    path = f"/admin?team_id={admin['team_id']}&team_tab=overview"
    response = page.goto(path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    nav = page.get_by_role("navigation", name="Team settings")
    mobile_tabs = page.locator('nav[aria-label="Team settings"]')
    direct_tabs = {"overview", "members"}
    for tab in direct_tabs:
        link = nav.locator(f'a[data-tab="{tab}"]')
        assert link.count() == 1
        assert link.get_attribute("href") == f"/admin?team_id={admin['team_id']}&team_tab={tab}"
    assert nav.locator('a[data-tab="overview"]').get_attribute("aria-current") == "page"

    provider_group, settings_group = nav.locator("details[data-team-tab-disclosure]").all()
    assert provider_group.locator("summary").inner_text().startswith("Providers")
    assert settings_group.locator("summary").inner_text().startswith("Settings")
    expected_groups = {
        "provider-policy": provider_group,
        "stt": provider_group,
        "llm": provider_group,
        "deidentification": provider_group,
        "defaults": settings_group,
        "security": settings_group,
        "danger": settings_group,
    }
    for tab, group in expected_groups.items():
        link = group.locator(f'a[data-tab="{tab}"]')
        assert link.count() == 1
        assert link.get_attribute("href") == f"/admin?team_id={admin['team_id']}&team_tab={tab}"

    defaults = page.goto(
        f"/admin?team_id={admin['team_id']}&team_tab=defaults", wait_until="domcontentloaded"
    )
    assert defaults is not None and defaults.status == 200
    nav = page.get_by_role("navigation", name="Team settings")
    settings_group = nav.locator("details[data-team-tab-disclosure]").nth(1)
    current = settings_group.locator('a[data-tab="defaults"]')
    assert current.get_attribute("aria-current") == "page"
    assert "Defaults" in settings_group.locator("summary").inner_text()

    # Escape dismisses an expanded navigation group and returns focus to the
    # native summary; clicking elsewhere dismisses without stealing focus.
    provider_group = nav.locator("details[data-team-tab-disclosure]").first
    provider_summary = provider_group.locator("summary")
    if not provider_group.get_attribute("open"):
        provider_summary.click()
    assert provider_group.get_attribute("open") is not None
    page.keyboard.press("Escape")
    page.wait_for_function("el => !el.open && document.activeElement === el.querySelector('summary')", arg=provider_group.element_handle())
    assert provider_group.get_attribute("open") is None
    assert provider_summary.evaluate("el => document.activeElement === el")

    provider_summary.click()
    assert provider_group.get_attribute("open") is not None
    target = page.get_by_role("button", name="Team actions")
    target.click()
    assert provider_group.get_attribute("open") is None
    assert target.evaluate("el => document.activeElement === el")

    result = _scan_admin(page, audit_report, "team_nav_disclosures", axe_source, blocked)
    assert not result.get("violations"), f"axe violations for team navigation: {result.get('violations')}"

    # At mobile widths the same eleven destinations sit inside one native
    # disclosure. Verify both the collapsed state and the complete route set,
    # including nested Providers/Settings links.
    page.set_viewport_size({"width": 390, "height": 844})
    destinations = (
        ("overview", "Overview", None),
        ("members", "Members", None),
        ("provider-policy", "Provider policy", "Providers"),
        ("stt", "STT", "Providers"),
        ("llm", "LLM", "Providers"),
        ("deidentification", "De-identification", "Providers"),
        ("defaults", "Defaults", "Settings"),
        ("security", "Security", "Settings"),
        ("danger", "Danger zone", "Settings"),
    )
    mobile_menu = page.locator("[data-team-navigation]")
    mobile_summary = mobile_menu.locator("summary.team-mobile-navigation-summary")
    assert mobile_menu.count() == 1
    assert mobile_summary.is_visible()
    assert "Team navigation" in mobile_summary.inner_text()
    assert "Team navigation Defaults" in mobile_summary.aria_snapshot()
    assert not mobile_menu.evaluate("el => el.open")
    assert not nav.is_visible(), "Team destinations should stay collapsed until requested"
    # The server-rendered native disclosure starts open so all links remain
    # available before the responsive script runs or when JavaScript is absent.
    markup_response = page.request.get(
        f"/admin?team_id={admin['team_id']}&team_tab=defaults"
    )
    assert markup_response.status == 200
    markup = markup_response.text()
    assert '<details class="team-mobile-navigation" data-team-navigation open>' in markup
    for tab, _label, _parent_group in destinations:
        assert f'team_tab={tab}"' in markup

    mobile_summary.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("el => el.open", arg=mobile_menu.element_handle())
    assert nav.is_visible()
    result = _scan_admin(page, audit_report, "team_mobile_navigation_open", axe_source, blocked)
    assert not result.get("violations"), f"axe violations for mobile team navigation: {result.get('violations')}"
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if screenshot_dir:
        page.screenshot(
            path=str(Path(screenshot_dir) / "team-navigation-mobile-open-390.png"),
            full_page=False,
            animations="disabled",
        )
    page.keyboard.press("Escape")
    page.wait_for_function(
        "el => !el.open && document.activeElement === el.querySelector('summary')",
        arg=mobile_menu.element_handle(),
    )
    assert not nav.is_visible()
    assert mobile_summary.evaluate("el => document.activeElement === el")
    if screenshot_dir:
        page.screenshot(
            path=str(Path(screenshot_dir) / "team-navigation-mobile-closed-390.png"),
            full_page=False,
            animations="disabled",
        )

    # Route through each visible destination, reopening its group after each
    # server navigation. This proves menu links retain canonical URL values.
    for tab, label, parent_group in destinations:
        if not mobile_menu.evaluate("el => el.open"):
            mobile_summary.click()
        link = mobile_tabs.locator(f'a[data-tab="{tab}"]')
        if parent_group:
            group_index = 0 if parent_group == "Providers" else 1
            group = mobile_tabs.locator("details[data-team-tab-disclosure]").nth(group_index)
            if not group.evaluate("el => el.open"):
                group.locator("summary").click()
        assert link.is_visible(), f"{label} is not reachable inside the mobile menu"
        assert link.get_attribute("href") == f"/admin?team_id={admin['team_id']}&team_tab={tab}"
        link.click()
        page.wait_for_url(f"**team_tab={tab}")
        selected = mobile_tabs.locator(f'a[data-tab="{tab}"]')
        assert selected.get_attribute("aria-current") == "page"
        assert page.locator(".team-mobile-navigation-current").inner_text().strip() == label

    # A nested group closes first on Escape, then its containing menu. An
    # outside pointer dismisses the menu without moving focus from the target.
    page.set_viewport_size({"width": 390, "height": 844})
    if not mobile_menu.evaluate("el => el.open"):
        mobile_summary.click()
    settings_group = mobile_tabs.locator("details[data-team-tab-disclosure]").nth(1)
    settings_group.locator("summary").click()
    assert settings_group.evaluate("el => el.open")
    page.keyboard.press("Escape")
    page.wait_for_function("el => !el.open", arg=settings_group.element_handle())
    assert mobile_menu.evaluate("el => el.open"), "Escape should close the innermost group first"
    page.keyboard.press("Escape")
    page.wait_for_function(
        "el => !el.open && document.activeElement === el.querySelector('summary')",
        arg=mobile_menu.element_handle(),
    )
    target = page.get_by_role("button", name="Team actions")
    mobile_summary.click()
    target.click()
    assert not mobile_menu.evaluate("el => el.open")
    assert target.evaluate("el => document.activeElement === el")

    # Crossing the breakpoint while focus is inside an open nested group must
    # close hidden content and place focus on the now-visible outer summary.
    mobile_summary.click()
    settings_group.locator("summary").click()
    settings_group.locator("summary").focus()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.set_viewport_size({"width": 768, "height": 900})
    page.wait_for_function(
        "details => !details.open && !details.querySelector('details[data-team-tab-disclosure]').open",
        arg=mobile_menu.element_handle(),
    )
    assert mobile_summary.evaluate("el => document.activeElement === el")

    # Resizing must not strand the menu open or hide desktop links; returning
    # to mobile restores the intentional collapsed disclosure state.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_function(
        "window.matchMedia('(min-width: 821px)').matches && document.querySelector('[data-team-navigation]').open"
    )
    assert nav.is_visible()
    assert not mobile_summary.is_visible()
    page.set_viewport_size({"width": 768, "height": 900})
    page.wait_for_function(
        "!window.matchMedia('(min-width: 821px)').matches && !document.querySelector('[data-team-navigation]').open"
    )
    assert mobile_summary.is_visible()
    assert not mobile_menu.evaluate("el => el.open")
    assert not nav.is_visible()


def test_admin_refinement_responsive_screenshots_and_no_page_overflow(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    states = (
        ("overview", "overview"),
        ("defaults", "defaults"),
        ("members", "members"),
        ("provider-policy", "provider-policy"),
    )
    viewports = (1440, 768, 390, 320)
    for tab, state in states:
        response = page.goto(
            f"/admin?team_id={admin['team_id']}&team_tab={tab}", wait_until="domcontentloaded"
        )
        assert response is not None and response.status == 200
        assert page.locator(f'[data-panel="{tab}"]').is_visible()
        for width in viewports:
            page.set_viewport_size({"width": width, "height": 1000})
            page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
            if width <= 390:
                _assert_no_horizontal_overflow(page, width)
            if tab == "members" and width <= 390:
                assert page.locator(".member-cell[data-label]").count() >= 4
                section_heading = page.locator('[data-panel="members"] .section-intro h2')
                intro_paragraph = page.locator('[data-panel="members"] .section-intro p')
                assert section_heading.is_visible() and intro_paragraph.is_visible()
                team_menu = page.locator("[data-team-navigation]")
                page.wait_for_function("el => !el.open", arg=team_menu.element_handle())
                assert not page.locator('nav[aria-label="Team settings"]').is_visible(), (
                    "Team links should be hidden while the mobile disclosure is collapsed"
                )
                heading_layout = page.evaluate("""() => {
                  const summary = document.querySelector('[data-team-navigation] > summary').getBoundingClientRect();
                  const heading = document.querySelector('[data-panel="members"] .section-intro h2').getBoundingClientRect();
                  const paragraph = document.querySelector('[data-panel="members"] .section-intro p').getBoundingClientRect();
                  return {scrollY: window.scrollY, summaryBottom: summary.bottom, headingTop: heading.top,
                    headingBottom: heading.bottom, paragraphTop: paragraph.top, paragraphBottom: paragraph.bottom};
                }""")
                assert heading_layout["scrollY"] == 0
                assert heading_layout["headingTop"] >= heading_layout["summaryBottom"] - 1, (
                    f"Members intro is obscured by Team navigation: {heading_layout}"
                )
                assert heading_layout["paragraphTop"] >= heading_layout["headingBottom"] - 1, (
                    f"Members intro text overlaps its heading: {heading_layout}"
                )
                row = page.locator(".member-row[data-member-name]").first
                assert page.locator(".member-table").evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (
                    "Member table still needs horizontal scrolling at a mobile width"
                )
                assert row.locator('.member-cell[data-label="Email"]').evaluate("""el => {
                  const identity = el.parentElement.querySelector('.member-identity').getBoundingClientRect();
                  return el.getBoundingClientRect().top >= identity.bottom - 1;
                }"""), "Member fields did not reflow into stacked rows"
                if width == 390:
                    action = row.get_by_role("button", name=f"Actions for {admin['member_email']}")
                    action.scroll_into_view_if_needed()
                    page.wait_for_timeout(50)
                    action.click()
                    menu = row.locator(".member-menu")
                    assert menu.is_visible()
                    page.wait_for_function(
                        "el => !el.hidden && el.getBoundingClientRect().width > 0",
                        arg=menu.element_handle(),
                    )
                    bounds = menu.bounding_box()
                    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
                    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
                    if screenshot_dir:
                        page.screenshot(
                            path=str(Path(screenshot_dir) / "sidebar-member-actions-open-mobile-390.png"),
                            full_page=False,
                            animations="disabled",
                        )
                    page.keyboard.press("Escape")
                    assert menu.is_hidden()
            if tab == "provider-policy" and width <= 390:
                assert page.locator(".policy-cell[data-label]").count() > 0
                stt_row = page.locator('[data-policy-row="conversation"]')
                assert admin["stt_label"] in stt_row.inner_text()
                assert stt_row.locator('[data-policy-provider-select]').input_value()
                assert stt_row.locator('[data-policy-model-select]').input_value() == "synthetic-whisper-fast"
                assert stt_row.get_by_role("button", name="Save").is_visible()
                llm_row = page.locator('[data-policy-row="writing-assistant"]')
                assert admin["llm_label"] in llm_row.inner_text()
                provider_select = llm_row.locator('[data-policy-provider-select]')
                model_select = llm_row.locator('[data-policy-model-select]')
                assert provider_select.input_value()
                assert model_select.input_value() == "synthetic-model-alt"
                assert llm_row.get_by_role("button", name="Save").is_visible()
                assert page.locator("[data-provider-policy-table]").evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (
                    "Provider policy table still needs horizontal scrolling at a mobile width"
                )
                assert page.locator(".policy-cell[data-label]").evaluate_all("""cells => cells.every(cell => {
                  const label = getComputedStyle(cell, '::before').content.replaceAll('"', '');
                  return label === cell.dataset.label && cell.scrollWidth <= cell.clientWidth + 1;
                })"""), "Provider policy fields lack visible labels or clipped their values"
            if tab == "defaults":
                for field in page.locator(".defaults-quota-resource-grid input[type=number]").all():
                    measurement = field.evaluate("""input => {
                      const style = getComputedStyle(input);
                      const canvas = document.createElement('canvas');
                      const context = canvas.getContext('2d');
                      context.font = style.font;
                      const textWidth = context.measureText(input.value).width;
                      const available = input.getBoundingClientRect().width
                        - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 2;
                      return {fits: textWidth <= available + 1, textWidth, available, width: input.getBoundingClientRect().width, value: input.value, font: style.font};
                    }""")
                    assert measurement["fits"], f"Quota default value is clipped at {width}px: {field.get_attribute('name')} {measurement}"
                retention_form = page.locator("#retention-default form.defaults-retention-form")
                copy = retention_form.locator(".retention-setting-copy")
                control = retention_form.locator(".retention-setting-control")
                days = retention_form.locator("#team-retention-days")
                save = retention_form.get_by_role("button", name="Save retention default")
                assert days.is_visible() and days.get_attribute("required") is not None and save.is_visible()
                assert days.get_attribute("min") == "1" and days.get_attribute("max") == "90"
                assert days.evaluate("el => el.labels.length === 1 && el.labels[0].htmlFor === el.id")
                days_box, save_box = days.bounding_box(), save.bounding_box()
                assert days_box["height"] >= 44 and save_box["height"] >= 44
                if width >= 821:
                    copy_box, control_box = copy.bounding_box(), control.bounding_box()
                    assert copy_box["x"] + copy_box["width"] <= control_box["x"] + 1, (
                        f"Retention copy and controls should form one compact desktop row at {width}px"
                    )
                    assert save_box["x"] >= days_box["x"] + days_box["width"] - 1
                    if width == 1440 and os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR"):
                        retention_form.screenshot(
                            path=str(Path(os.environ["OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR"]) / "retention-setting-row-1440.png"),
                            animations="disabled",
                        )
                else:
                    copy_box = copy.bounding_box()
                    assert copy_box["y"] + copy_box["height"] <= days_box["y"] + 1, (
                        f"Retention instructions should precede the mobile control row at {width}px"
                    )
                    assert days_box["x"] + days_box["width"] <= save_box["x"] + 1
                    assert save_box["x"] + save_box["width"] <= width
                    if width == 320:
                        assert retention_form.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                        if os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR"):
                            retention_form.screenshot(
                                path=str(Path(os.environ["OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR"]) / "retention-setting-row-320.png"),
                                animations="disabled",
                            )
                if width <= 820:
                    team_menu = page.locator("[data-team-navigation]")
                    team_summary = team_menu.locator("summary.team-mobile-navigation-summary")
                    assert team_summary.is_visible() and not team_menu.evaluate("el => el.open")
                    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
                    if screenshot_dir:
                        page.screenshot(
                            path=str(Path(screenshot_dir) / f"team-navigation-closed-{width}.png"),
                            full_page=False,
                            animations="disabled",
                        )
                    team_summary.click()
                    assert team_menu.evaluate("el => el.open")
                    if screenshot_dir:
                        page.screenshot(
                            path=str(Path(screenshot_dir) / f"team-navigation-open-{width}.png"),
                            full_page=False,
                            animations="disabled",
                        )
                    page.keyboard.press("Escape")
                    assert not team_menu.evaluate("el => el.open")
            _capture_admin_evidence(page, f"refinement-{tab}-{width}.png")
            if (tab, width) in {("defaults", 768), ("members", 390), ("members", 320)}:
                screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
                if screenshot_dir:
                    page.screenshot(
                        path=str(Path(screenshot_dir) / f"refinement-{tab}-{width}-viewport.png"),
                        full_page=False,
                        animations="disabled",
                    )

    # A nested selected page exposes its parent group state in the nav.
    response = page.goto(
        f"/admin?team_id={admin['team_id']}&team_tab=defaults", wait_until="domcontentloaded"
    )
    assert response is not None and response.status == 200
    settings = page.locator('nav[aria-label="Team settings"] details[data-team-tab-disclosure]').nth(1)
    for width in viewports:
        page.set_viewport_size({"width": width, "height": 1000})
        if width <= 820:
            team_menu = page.locator("[data-team-navigation]")
            page.wait_for_function("el => !el.open", arg=team_menu.element_handle())
            assert team_menu.locator("summary.team-mobile-navigation-summary").inner_text().endswith("Defaults")
            assert not page.locator('nav[aria-label="Team settings"]').is_visible()
        else:
            team_menu = page.locator("[data-team-navigation]")
            page.wait_for_function("el => el.open", arg=team_menu.element_handle())
            assert "Defaults" in settings.locator("summary").inner_text()
            assert settings.locator('a[aria-current="page"]').text_content().strip() == "Defaults"
        _capture_admin_evidence(page, f"refinement-settings-defaults-{width}.png")

    # A short mobile viewport forces a member menu to choose the available
    # direction and remain operable even when the last row sits near an edge.
    page.set_viewport_size({"width": 390, "height": 300})
    page.goto(
        f"/admin?team_id={admin['team_id']}&team_tab=members", wait_until="domcontentloaded"
    )
    row = page.locator(".member-row[data-member-name]").last
    row.scroll_into_view_if_needed()
    action = row.get_by_role("button", name=f"Actions for {admin['member_email']}")
    action.click()
    menu = row.locator(".member-menu")
    assert menu.is_visible()
    menu_bounds = menu.bounding_box()
    assert menu_bounds and menu_bounds["x"] >= 0 and menu_bounds["y"] >= 0
    assert menu_bounds["x"] + menu_bounds["width"] <= 390
    assert menu_bounds["y"] + menu_bounds["height"] <= 300
    last_action = menu.locator("a, button").last
    assert last_action.is_visible()
    last_bounds = last_action.bounding_box()
    assert last_bounds and last_bounds["y"] + last_bounds["height"] <= 300
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if screenshot_dir:
        page.screenshot(
            path=str(Path(screenshot_dir) / "refinement-member-menu-short-mobile.png"),
            full_page=False,
            animations="disabled",
        )
    menu.evaluate("el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll', {bubbles:true})); }")
    assert menu.is_visible() and action.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert menu.is_hidden() and action.get_attribute("aria-expanded") == "false"
    assert action.evaluate("el => document.activeElement === el")

    warning = page.locator("aside.flash.warning .setup-warning-details")
    assert warning.count() == 1
    page.set_viewport_size({"width": 320, "height": 900})
    summary_text = warning.locator("summary").inner_text()
    assert "item" in summary_text and "attention" in summary_text
    assert warning.locator("summary").evaluate("""summary => {
      const title = summary.querySelector('strong').getBoundingClientRect();
      const count = summary.querySelector('span').getBoundingClientRect();
      return title.right <= count.left + 1 || count.right <= title.left + 1
        || title.bottom <= count.top + 1 || count.bottom <= title.top + 1;
    }"""), "Legal setup warning title and item count overlap at 320px"
    assert page.get_by_role("link", name="Review legal content").get_attribute("href") == "/admin?tab=legal"
    warning.locator("summary").click()
    assert warning.get_attribute("open") is not None
    assert warning.locator("li").count() > 0

    result = _scan_admin(page, audit_report, "team_refinement_defaults", axe_source, blocked)
    assert not result.get("violations"), f"axe violations for refined Defaults: {result.get('violations')}"

    team_routes = (
        ("overview", "Overview", "team_overview"),
        ("members", "Members", "team_members"),
        ("provider-policy", "Provider policy", "team_provider_policy"),
        ("stt", "Speech-to-text providers", "team_stt"),
        ("llm", "Large-language-model providers", "team_llm"),
        ("deidentification", "De-identification providers", "team_deidentification"),
        ("defaults", "Defaults", "team_defaults"),
        ("security", "Security", "team_security"),
        ("danger", "Danger zone", "team_danger"),
    )
    for tab, heading, state in team_routes:
        path = f"/admin?team_id={admin['team_id']}&team_tab={tab}"
        response = page.goto(path, wait_until="domcontentloaded")
        assert response is not None and response.status == 200, path
        assert page.locator("#team-title").inner_text() == admin["team_name"], path
        panel = page.locator(f'[data-panel="{tab}"]')
        assert panel.is_visible(), f"Requested panel not active: {tab}"
        assert panel.get_by_role("heading", name=heading, exact=False).first.is_visible(), path
        result = _scan_admin(page, audit_report, state, axe_source, blocked)
        assert not result.get("violations"), f"axe violations for {state}: {result.get('violations')}"


def test_quota_editor_invalid_draft_mobile_reflow_and_member_disclosure(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    path = (
        f"/admin/usage?team_id={admin['team_id']}&view=quotas"
        f"&member_id={admin['member_id']}&quota_edit=limits"
    )
    response = page.goto(path, wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    editor = page.locator("[data-quota-limits-editor]")
    assert editor.is_visible()
    assert page.get_by_role("heading", name=admin["member_email"]).is_visible()
    assert editor.locator("[data-quota-dirty-badge]").is_hidden()
    _capture_admin_evidence(page, "quotas-editor-desktop.png")
    result = _scan_admin(page, audit_report, "quota_edit", axe_source, blocked)
    assert not result.get("violations"), f"axe violations in quota editor: {result.get('violations')}"
    page.set_viewport_size({"width": 320, "height": 900})
    _capture_admin_evidence(page, "quotas-editor-mobile.png")

    # The long quota tables stay inside named keyboard-scrollable regions on
    # narrow screens, including their empty-state headers.
    page.goto(
        f"/admin/usage?team_id={admin['team_id']}&view=quotas&member_id={admin['member_id']}",
        wait_until="domcontentloaded",
    )
    page.set_viewport_size({"width": 320, "height": 900})
    for label, region_name in (
        ("Allowances", "Active quota allowances"),
        ("Quota history", "Quota policy history"),
    ):
        disclosure = page.locator("details.quota-disclosure").filter(has_text=label)
        disclosure.locator("summary").click()
        region = page.get_by_role("region", name=region_name)
        assert region.is_visible()
        assert region.get_attribute("tabindex") == "0"
        disclosure.locator("summary").focus()
        page.keyboard.press("Tab")
        assert region.evaluate("el => document.activeElement === el"), f"Keyboard Tab did not reach {region_name}"
        _assert_keyboard_focus_indicator(region)
        table = region.get_by_role("table")
        assert table.get_by_role("columnheader").count() == 7
        assert table.get_by_role("row").count() >= 2
        assert region.evaluate("el => el.scrollWidth > el.clientWidth")
    _capture_admin_evidence(page, "quota-panel-mobile.png")

    # Submit an intentionally invalid limit through the real browser route.
    # form.submit() bypasses HTML min validation but preserves session and
    # CSRF fields; server validation must return the draft without saving it.
    page.goto(path, wait_until="domcontentloaded")
    editor = page.locator("[data-quota-limits-editor]")
    page.locator('[name="daily_token_unlimited"][value=""]').check()
    page.locator('[name="daily_token_limit"]').fill("-1")
    page.locator('[name="reason_code"]').select_option("policy_change")
    page.locator('[name="reason"]').fill("Synthetic invalid draft")
    with page.expect_navigation(wait_until="domcontentloaded") as invalid_navigation:
        editor.evaluate("form => form.submit()")
    assert invalid_navigation.value.status == 422
    editor = page.locator("[data-quota-limits-editor]")
    assert editor.is_visible()
    assert page.locator('[name="daily_token_limit"]').input_value() == "-1"
    assert page.locator('[name="reason"]').input_value() == "Synthetic invalid draft"
    assert page.locator("[data-quota-dirty-badge]").is_visible()
    result = _scan_admin(page, audit_report, "quota_invalid_draft", axe_source, blocked)
    assert not result.get("violations"), f"axe violations in invalid quota draft: {result.get('violations')}"

    # Text-spacing overrides model WCAG 1.4.12 without changing the page's
    # account or quota data. They must not create two-dimensional scrolling.
    page.set_viewport_size({"width": 320, "height": 900})
    page.add_style_tag(content="""
      body, body * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; }
      p { margin-bottom: 2em !important; }
    """)
    # Member action disclosure communicates its state to assistive technology.
    page.goto(f"/admin?team_id={admin['team_id']}&team_tab=members", wait_until="domcontentloaded")
    action = page.get_by_role("button", name=f"Actions for {admin['member_email']}")
    action.focus()
    assert action.evaluate("el => document.activeElement === el")
    page.wait_for_timeout(100)  # let any focus-triggered mobile scroll settle before activation
    action.evaluate("el => { el.dataset.testClickCount = '0'; el.addEventListener('click', () => { el.dataset.testClickCount = String(Number(el.dataset.testClickCount) + 1); }, {once:true}); }")
    page.keyboard.press("Enter")
    try:
        page.wait_for_function(
            "el => el.getAttribute('aria-expanded') === 'true'", arg=action.element_handle(), timeout=2_000
        )
    except Exception as error:
        state = action.evaluate("el => ({expanded: el.getAttribute('aria-expanded'), clickCount: el.dataset.testClickCount, focused: document.activeElement === el, scrollY: window.scrollY})")
        raise AssertionError(f"Keyboard activation did not open member actions: {state}") from error
    assert action.get_attribute("aria-expanded") == "true"
    assert action.get_attribute("data-test-click-count") == "1", "Enter did not activate the member action button"
    assert action.get_attribute("aria-controls")
    bounds = action.bounding_box()
    assert bounds and bounds["width"] >= 24 and bounds["height"] >= 24
    _assert_keyboard_focus_indicator(action)
    _assert_no_horizontal_overflow(page, 320)


def test_admin_wide_grids_reflow_at_320_and_survive_text_spacing(
    audit_report, audit_browser, synthetic_admin_context
):
    page, _blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    overflow_results = {}
    team_routes = ("members", "provider-policy", "usage")
    for tab in team_routes:
        page.goto(f"/admin?team_id={admin['team_id']}&team_tab={tab}", wait_until="domcontentloaded")
        overflow_results[tab] = _measure_horizontal_overflow(page, 320)

    spacing_override = """
      body, body * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; }
      p { margin-bottom: 2em !important; }
    """
    for tab in ("overview", "quotas", "defaults"):
        page.goto(f"/admin?team_id={admin['team_id']}&team_tab={tab}", wait_until="domcontentloaded")
        page.set_viewport_size({"width": 320, "height": 900})
        page.add_style_tag(content=spacing_override)
        overflow_results[f"{tab}_text_spacing"] = _measure_horizontal_overflow(page, 320)
        assert page.get_by_role("main").is_visible()
    failures = {state: result for state, result in overflow_results.items() if result["scroll"] > result["client"]}
    assert not failures, f"Admin content overflowed at 320 CSS px: {failures}"


def test_provider_dialog_accessible_names_focus_keyboard_and_step_two_labels(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)

    for kind, tab, opener_id, dialog_id, title_id, step_selector in (
        ("stt", "stt", "add-stt-provider-button", "stt-wizard", "stt-wizard-title", "[data-wizard-step='2']"),
        ("llm", "llm", "add-llm-provider-button", "llm-wizard", "llm-wizard-title", "[data-llm-wizard-step='2']"),
    ):
        page.goto(f"/admin?team_id={admin['team_id']}&team_tab={tab}", wait_until="domcontentloaded")
        opener = page.locator(f"#{opener_id}")
        opener.focus()
        page.keyboard.press("Enter")
        dialog = page.locator(f"#{dialog_id} [role='dialog']")
        assert dialog.is_visible()
        assert dialog.get_attribute("aria-modal") == "true"
        assert dialog.get_attribute("aria-labelledby") == title_id
        assert page.locator(f"#{title_id}").is_visible()
        assert dialog.evaluate("el => el.contains(document.activeElement)"), f"{kind} focus did not enter dialog"
        assert page.evaluate("id => [...document.body.children].filter(el => el.id !== id).every(el => el.inert)", dialog_id), (
            f"{kind} modal did not make background content inert"
        )
        _assert_keyboard_focus_indicator(page.locator(":focus"))
        result = _scan_admin(page, audit_report, f"{kind}_wizard_open", axe_source, blocked)
        assert not result.get("violations"), f"axe violations in {kind} dialog: {result.get('violations')}"
        for _ in range(30):
            page.keyboard.press("Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)"), f"{kind} focus escaped dialog"

        # Step 1 → step 2 is local navigation only. Do not proceed to provider
        # inspection, which would submit credential-bearing provider data.
        page.get_by_role("button", name="Continue").click()
        step_two = page.locator(step_selector)
        assert step_two.is_visible()
        unnamed = step_two.locator("input:not([type=hidden]), select, textarea").evaluate_all("""els => els.filter(el => {
          const id = el.id;
          const labels = [...el.labels || []];
          return !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')
            && !labels.length && !(id && document.querySelector(`label[for="${CSS.escape(id)}"]`));
        }).map(el => ({tag: el.tagName, type: el.type || '', name: el.name || ''}))""")
        assert not unnamed, f"{kind} step-two fields need accessible labels: {unnamed}"

        page.keyboard.press("Escape")
        assert page.locator(f"#{dialog_id}").is_hidden()
        page.wait_for_function(
            "id => document.activeElement === document.getElementById(id)", arg=opener_id, timeout=5_000
        )
        assert opener.evaluate("el => document.activeElement === el"), f"{kind} focus did not return to opener"


def test_admin_mobile_navigation_and_usage_controls_are_accessible(
    audit_report, audit_browser, synthetic_admin_context, axe_source
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    _open_admin(page, audit_report, admin)
    page.goto("/admin/usage?range=30d", wait_until="domcontentloaded")
    page.set_viewport_size({"width": 320, "height": 800})
    nav_toggle = page.locator("#admin-mobile-nav-toggle")
    assert nav_toggle.is_visible()
    nav_toggle.click()
    page.wait_for_function(
        "() => document.getElementById('admin-mobile-nav-toggle')?.getAttribute('aria-expanded') === 'true'",
        timeout=5_000,
    )
    assert nav_toggle.get_attribute("aria-expanded") == "true"
    nav = page.get_by_role("navigation", name="Administration")
    assert nav.is_visible()
    account = page.locator("#admin-account-disclosure")
    if account.count() and not account.evaluate("el => el.open"):
        account.locator("summary").click()
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if screenshot_dir:
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        page.screenshot(
            path=str(Path(screenshot_dir) / "sidebar-account-open-mobile-320.png"),
            full_page=False,
            animations="disabled",
        )
    logout = page.get_by_role("button", name="Log out")
    assert logout.is_visible()
    toggle_bounds = nav_toggle.bounding_box()
    assert toggle_bounds and toggle_bounds["width"] >= 24 and toggle_bounds["height"] >= 24
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => !document.getElementById('admin-account-disclosure')?.open",
        timeout=5_000,
    )
    assert nav_toggle.get_attribute("aria-expanded") == "true"
    assert account.locator("summary").evaluate("el => document.activeElement === el")
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => document.getElementById('admin-mobile-nav-toggle')?.getAttribute('aria-expanded') === 'false'",
        timeout=5_000,
    )
    assert nav_toggle.get_attribute("aria-expanded") == "false"
    assert nav_toggle.evaluate("el => document.activeElement === el")

    # The native disclosure and ordinary GET links work with keyboard input.
    page.goto("/admin/usage?range=30d", wait_until="domcontentloaded")
    range_details = page.locator("details.usage-range-disclosure")
    range_summary = range_details.locator("summary.usage-range-summary")
    assert range_summary.is_visible()
    assert "Last 30 days" in range_summary.inner_text()
    range_summary.focus()
    page.keyboard.press("Enter")
    assert range_details.get_attribute("open") is not None
    options = range_details.locator("a.usage-range-option")
    assert options.count() == 4
    assert options.locator("[aria-current='page']").count() == 0
    assert range_details.locator("a[aria-current='page']").get_attribute("href").endswith("range=30d")
    page.keyboard.press("Tab")
    assert options.nth(0).evaluate("el => document.activeElement === el")
    page.keyboard.press("Tab")
    assert options.nth(1).evaluate("el => document.activeElement === el")
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.keyboard.press("Enter")
    assert "range=90d" in page.url
    assert page.locator("details.usage-range-disclosure a[aria-current='page']").get_attribute("href").endswith("range=90d")
    page.locator("details.usage-range-disclosure summary").focus()
    page.keyboard.press("Space")
    assert page.locator("details.usage-range-disclosure").get_attribute("open") is not None
    page.keyboard.press("Escape")
    assert page.locator("details.usage-range-disclosure").get_attribute("open") is None
    assert page.locator("details.usage-range-disclosure summary").evaluate(
        "el => document.activeElement === el"
    )
    page.locator("details.usage-range-disclosure summary").click()
    page.locator("h1").click(position={"x": 5, "y": 5})
    assert page.locator("details.usage-range-disclosure").get_attribute("open") is None
    _capture_admin_evidence(page, "usage-global-mobile.png")
    result = _scan_admin(page, audit_report, "global_usage", axe_source, blocked)

    # The accessible data view must retain the full daily series and previous
    # period; chart controls themselves must support keyboard zoom/series state.
    for scope, usage_path in (
        ("global", "/admin/usage?range=90d"),
        ("team", f"/admin/usage?team_id={admin['team_id']}&view=team&range=90d"),
    ):
        page.goto(usage_path, wait_until="domcontentloaded")
        table = page.locator(f"#usage-chart-table-{scope}")
        assert table.count() == 1
        disclosure = table.locator("xpath=ancestor::details[1]")
        assert disclosure.count() == 1
        if not disclosure.get_attribute("open"):
            disclosure.locator("summary").click()
        assert table.get_by_role("row").count() > 10
        assert table.get_by_role("columnheader").count() >= 3
        assert table.locator("caption").count() == 1
        headers = " ".join(table.get_by_role("columnheader").all_inner_texts()).lower()
        assert "current" in headers and "previous" in headers
        assert page.locator(f"#usage-chart-summary-{scope}").is_visible()
        charts = page.locator(f"[data-usage-chart-scope='{scope}'][data-usage-chart]")
        assert charts.count() == 4
        for chart in charts.all():
            assert chart.get_attribute("aria-describedby") == f"usage-chart-summary-{scope}"

        page.locator(".usage-chart-controls").evaluate_all("els => els.forEach(el => { el.open = true; })")
        chart_controls = page.locator(
            ".usage-chart-controls input[type='range'], .usage-chart-controls input[type='checkbox']"
        )
        assert chart_controls.count() > 0
        for control in chart_controls.all():
            assert control.is_visible()
            assert control.evaluate("el => el.labels.length > 0 || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')")
            control.focus()
            control_id = control.get_attribute("id")
            assert control_id
            chart_id = (
                control_id.replace("usage-chart-start-", "usage-chart-")
                .replace("usage-chart-end-", "usage-chart-")
                .replace("usage-chart-series-", "usage-chart-")
                .removesuffix("-current")
                .removesuffix("-previous")
            )
            state_key = "dataZoom" if control.get_attribute("type") == "range" else "legend"
            before_option = page.evaluate("""({id, stateKey}) => {
              const instance = window.echarts.getInstanceByDom(document.getElementById(id));
              return JSON.stringify(instance.getOption()[stateKey]);
            }""", {"id": chart_id, "stateKey": state_key})
            restore_series = False
            if control.get_attribute("type") == "range":
                before = int(control.input_value())
                maximum = int(control.get_attribute("max"))
                page.keyboard.press("ArrowLeft" if before == maximum else "ArrowRight")
                assert int(control.input_value()) != before, f"Keyboard did not operate chart control {control.get_attribute('id')}"
            elif control.get_attribute("type") == "checkbox":
                before_checked = control.is_checked()
                page.keyboard.press("Space")
                assert control.is_checked() != before_checked, f"Keyboard did not operate chart series control {control.get_attribute('id')}"
                restore_series = True
            after_option = page.evaluate("""({id, stateKey}) => {
              const instance = window.echarts.getInstanceByDom(document.getElementById(id));
              return JSON.stringify(instance.getOption()[stateKey]);
            }""", {"id": chart_id, "stateKey": state_key})
            assert after_option != before_option, f"Input did not update chart {chart_id} state {state_key}"
            chart = page.locator(f"#{chart_id}")
            assert chart.get_attribute("aria-describedby") == f"usage-chart-summary-{scope}"
            chart_label = chart.get_attribute("aria-label") or ""
            assert 0 < len(chart_label) <= 300, f"Chart label is missing or too verbose: {chart_id}"
            if restore_series:
                page.keyboard.press("Space")
    assert not result.get("violations"), f"axe violations in global usage: {result.get('violations')}"
    _assert_no_horizontal_overflow(page, 320)


@pytest.mark.accessibility_audit
def test_usage_table_sorter_handles_typed_values_missing_ties_and_keyboard(
    audit_report, audit_browser
):
    """The shared table sorter compares raw typed keys and keeps ties stable."""
    page, _blocked = audit_browser
    page.set_content("""
      <table id="typed" data-sortable-table><thead><tr>
        <th data-sort-type="text">Name</th><th data-sort-type="number">Count</th>
        <th data-sort-type="date">When</th><th>Action</th>
      </tr></thead><tbody>
        <tr id="tie-b"><td data-sort-value="tie">Tie B</td><td data-sort-value="2">2</td><td data-sort-value="2026-10-02">02 Oct</td><td><button>Open B</button></td></tr>
        <tr id="tie-a"><td data-sort-value="tie">Tie A</td><td data-sort-value="1">1</td><td data-sort-value="2026-10-01">01 Oct</td><td><button>Open A</button></td></tr>
        <tr id="ten"><td data-sort-value="Name 10">Name 10</td><td data-sort-value="10">10</td><td data-sort-value="2026-10-10">10 Oct</td><td><button>Open 10</button></td></tr>
        <tr id="two"><td data-sort-value="Name 2">Name 2</td><td data-sort-value="2">2</td><td data-sort-value="2026-10-03">03 Oct</td><td><button>Open 2</button></td></tr>
        <tr id="formatted-thousand"><td data-sort-value="Name 1000">Name 1000</td><td data-sort-value="1000">1,000</td><td data-sort-value="2026-10-20">20 Oct</td><td><button>Open 1000</button></td></tr>
        <tr id="missing"><td data-sort-value="—">—</td><td data-sort-value="">—</td><td data-sort-value="">—</td><td><button>Open missing</button></td></tr>
      </tbody></table>
      <table id="resources" data-sortable-table><thead><tr><th data-sort-type="resource-number" title="Sorts by resource, then amount">Amount</th><th>Action</th></tr></thead><tbody>
        <tr id="token-12"><td data-sort-group="tokens" data-sort-value="12">12 tokens</td><td><button>Revoke 12</button></td></tr>
        <tr id="audio-5"><td data-sort-group="audio_seconds" data-sort-value="5">5 audio seconds</td><td><button>Revoke audio</button></td></tr>
        <tr id="token-3"><td data-sort-group="tokens" data-sort-value="3">3 tokens</td><td><button>Revoke 3</button></td></tr>
      </tbody></table>
      <table id="single" data-sortable-table><thead><tr><th data-sort-type="number">Count</th></tr></thead><tbody><tr><td data-sort-value="1">1</td></tr></tbody></table>
    """)
    page.add_script_tag(path=str(Path("app/static/js/admin-usage-table-sort.js").resolve()))

    typed = page.locator("#typed")
    assert typed.locator("thead button.usage-table-sort-button").count() == 3
    assert typed.locator("thead tr th").nth(3).locator("button").count() == 0
    assert page.locator("#single button.usage-table-sort-button").count() == 0
    count_header = typed.locator("thead th").nth(1)
    count_button = count_header.locator("button")
    count_button.focus()
    page.keyboard.press("Enter")
    assert count_header.get_attribute("aria-sort") == "ascending"
    assert typed.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "tie-a", "tie-b", "two", "ten", "formatted-thousand", "missing"
    ]
    assert page.evaluate("el => document.activeElement === el", count_button.element_handle())

    # Sorting a different column establishes the current tie order; a later
    # count sort must retain that order for equal raw counts.
    name_header = typed.locator("thead th").nth(0)
    name_header.locator("button").click()
    assert name_header.get_attribute("aria-sort") == "ascending"
    assert typed.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "two", "ten", "formatted-thousand", "tie-a", "tie-b", "missing"
    ]
    count_button.focus()
    page.keyboard.press("Space")
    assert count_header.get_attribute("aria-sort") == "ascending"
    assert typed.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "tie-a", "two", "tie-b", "ten", "formatted-thousand", "missing"
    ]
    assert typed.locator("thead th[aria-sort]").count() == 1
    assert "Activate to sort descending" in count_button.get_attribute("aria-label")
    page.keyboard.press("Space")
    assert count_header.get_attribute("aria-sort") == "descending"
    assert typed.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "formatted-thousand", "ten", "two", "tie-b", "tie-a", "missing"
    ]

    resource_table = page.locator("#resources")
    amount_header = resource_table.locator("thead th").first
    amount_button = amount_header.locator("button")
    amount_button.click()
    assert resource_table.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "audio-5", "token-3", "token-12"
    ]
    amount_button.click()
    assert amount_header.get_attribute("aria-sort") == "descending"
    assert resource_table.locator("tbody tr").evaluate_all("rows => rows.map(row => row.id)") == [
        "audio-5", "token-12", "token-3"
    ]
    assert resource_table.locator("tbody tr").nth(1).get_by_role("button", name="Revoke 12").count() == 1


@pytest.mark.accessibility_audit
def test_rendered_usage_and_quota_tables_sort_without_reformatting_or_losing_actions(
    audit_report,
    audit_browser,
    synthetic_admin_context,
    make_team,
    make_user,
    db_session,
):
    """Real report tables sort by their raw values and keep quota actions with rows."""
    page, _blocked = audit_browser
    admin = synthetic_admin_context
    screenshot_dir = Path(os.environ.get(
        "OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR", "output/admin-usage-sort-2026-10-25"
    ))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    team = make_team(name="Other synthetic usage team")
    other_member = make_user(
        email="other-usage-member@example.test", password="synthetic-other-member-password",
        team=team, team_role=TeamRole.user,
    )
    now = utcnow()
    for index in range(2):
        db_session.add(ProviderUsageEvent(
            team_id=team.id, owner_user_id=other_member.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed, provider_adapter="synthetic",
            model_name="other-synthetic-model", prompt_tokens=900 + index,
            completion_tokens=90, total_tokens=1000 + index,
            created_at=now - timedelta(days=2 - index),
        ))
    selected_team = db_session.get(Team, UUID(admin["team_id"]))
    second_member = make_user(
        email="second-usage-member@example.test", password="synthetic-second-member-password",
        team=selected_team, team_role=TeamRole.user,
    )
    for index in range(2):
        db_session.add(ProviderUsageEvent(
            team_id=selected_team.id, owner_user_id=second_member.id,
            feature_type=ProviderFeatureType.llm_generation,
            event_type=ProviderUsageEventType.completed, provider_adapter="synthetic",
            model_name="synthetic-model", prompt_tokens=2, completion_tokens=1, total_tokens=3,
            created_at=now - timedelta(days=3 - index),
        ))
    quota_member = db_session.get(User, UUID(admin["member_id"]))
    quota_actor = db_session.scalar(select(User).where(User.email == admin["email"]))
    # The identity lookups above use fixture-owned IDs/emails; no real account
    # data is involved. Build three active grants to exercise grouped amounts.
    grant_ids = {}
    for resource, period, amount, key in (
        (QuotaResource.tokens, QuotaPeriod.monthly, 3, "tokens-3"),
        (QuotaResource.tokens, QuotaPeriod.daily, 12, "tokens-12"),
        (QuotaResource.audio_seconds, QuotaPeriod.daily, 5, "audio-5"),
    ):
        grant = UserQuotaPolicyEvent(
            operation_id=uuid4(), target_user_id=quota_member.id,
            actor_user_id=quota_actor.id, actor_user_id_snapshot=quota_actor.id,
            event_type=UserQuotaPolicyEventType.grant, resource=resource, period=period,
            reason_code=UserQuotaReasonCode.temporary_allowance, reason=f"synthetic {key}",
            amount=amount, effective_at=now - timedelta(hours=1), created_at=now - timedelta(hours=1),
        )
        db_session.add(grant)
        grant_ids[key] = grant
    db_session.commit()

    _open_admin(page, audit_report, admin)
    # Without the sorter script, server order remains readable and no
    # JavaScript-only controls are injected.
    page.route("**/admin-usage-table-sort.js**", lambda route: route.abort())
    response = page.goto("/admin/usage?range=90d", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.locator(".usage-summary-table button.usage-table-sort-button").count() == 0
    team_table = page.locator("table[data-sortable-table]").filter(
        has=page.locator("thead th", has_text="Team")
    ).first
    assert team_table.count() == 1
    assert team_table.locator("thead button.usage-table-sort-button").count() == 0
    baseline_names = team_table.locator("tbody tr td:first-child").all_inner_texts()
    assert baseline_names[0].startswith("Other synthetic usage team")
    page.unroute("**/admin-usage-table-sort.js**")

    response = page.goto("/admin/usage?range=90d", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    team_table = page.locator("table[data-sortable-table]").filter(
        has=page.locator("thead th", has_text="Team")
    ).first
    assert team_table.locator("thead button.usage-table-sort-button").count() > 0
    generated_header = team_table.locator("thead th").filter(has_text="Generated")
    generated_button = generated_header.locator("button")
    generated_button.focus()
    page.keyboard.press("Enter")
    assert generated_header.get_attribute("aria-sort") == "ascending"
    team_counts = team_table.locator("tbody tr td:nth-child(2)").evaluate_all(
        "cells => cells.map(cell => Number(cell.dataset.sortValue))"
    )
    assert team_counts == sorted(team_counts)
    assert team_counts[0] < team_counts[-1], "Synthetic teams need distinct counts to verify numeric ordering"
    team_names_ascending = team_table.locator("tbody tr td:first-child").all_inner_texts()
    assert "Accessibility Review Team" in team_names_ascending[-1]
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshot_dir / "usage-teams-sorted-1440.png"), full_page=False)
    team_table.screenshot(path=str(screenshot_dir / "usage-teams-sorted-table.png"))
    page.keyboard.press("Space")
    assert generated_header.get_attribute("aria-sort") == "descending"
    assert team_table.locator("tbody tr td:nth-child(2)").evaluate_all(
        "cells => cells.map(cell => Number(cell.dataset.sortValue))"
    ) == list(reversed(team_counts))

    # Chart rows are server-rendered in bucket order. Date sorting uses the
    # ISO data key, not the visible label, and is reversible by keyboard.
    chart_disclosure = page.locator("details").filter(has_text="View usage data table").first
    chart_disclosure.locator("summary").click()
    chart_table = page.locator("#usage-chart-table-global")
    date_header = chart_table.locator("thead th").first
    chart_dates = chart_table.locator("tbody tr th[scope=row]").evaluate_all(
        "cells => cells.map(cell => cell.dataset.sortValue)"
    )
    assert chart_dates == sorted(chart_dates)
    date_button = date_header.locator("button")
    date_button.focus()
    page.keyboard.press("Space")
    assert date_header.get_attribute("aria-sort") == "ascending"
    assert chart_table.locator("tbody tr th[scope=row]").first.get_attribute("data-sort-value") == chart_dates[0]
    page.keyboard.press("Enter")
    assert date_header.get_attribute("aria-sort") == "descending"
    assert chart_table.locator("tbody tr th[scope=row]").evaluate_all(
        "cells => cells.map(cell => cell.dataset.sortValue)"
    ) == list(reversed(chart_dates))
    assert generated_header.get_attribute("aria-sort") == "descending", (
        "Sorting one table must not clear another table's active sort state"
    )

    # Two real users make numeric 2-versus-14 ordering distinguishable from
    # lexical text ordering. Input/output cells sort on the documented raw
    # input-token key, although both values are displayed together.
    response = page.goto(
        f"/admin/usage?team_id={admin['team_id']}&view=users&range=90d", wait_until="domcontentloaded"
    )
    assert response is not None and response.status == 200
    user_table = page.locator("table.usage-user-table")
    user_generated = user_table.locator("thead th").filter(has_text="Generated")
    user_generated.locator("button").click()
    assert user_generated.get_attribute("aria-sort") == "ascending"
    assert user_table.locator("tbody tr td:nth-child(2)").evaluate_all(
        "cells => cells.map(cell => Number(cell.dataset.sortValue))"
    ) == [2, 14]
    input_output = user_table.locator("thead th").filter(has_text="Input / output")
    input_output.locator("button").click()
    assert input_output.get_attribute("aria-sort") == "ascending"
    assert "second-usage-member@example.test" in user_table.locator("tbody tr").first.inner_text()
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshot_dir / "usage-users-sorted-1440.png"), full_page=False)
    user_table.screenshot(path=str(screenshot_dir / "usage-users-sorted-table-1440.png"))
    for width in (1440, 768, 390, 320):
        page.set_viewport_size({"width": width, "height": 844})
        _assert_no_horizontal_overflow(page, width)
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshot_dir / "usage-users-sorted-390.png"), full_page=False)
    user_table.screenshot(path=str(screenshot_dir / "usage-users-sorted-table.png"))

    response = page.goto(
        f"/admin/usage?team_id={admin['team_id']}&view=quotas&member_id={admin['member_id']}&range=90d",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    allowances_disclosure = page.locator("details.quota-disclosure").filter(has_text="Allowances")
    allowances_disclosure.locator("summary").click()
    allowances = page.get_by_role("region", name="Active quota allowances").get_by_role("table")
    amount_header = allowances.locator("thead th").filter(has_text="Amount")
    amount_button = amount_header.locator("button")
    amount_button.click()
    rows = allowances.locator("tbody tr")
    assert rows.evaluate_all(
        "rows => rows.map(row => [row.cells[2].dataset.sortGroup, Number(row.cells[2].dataset.sortValue)])"
    ) == [["audio_seconds", 5], ["tokens", 3], ["tokens", 12]]
    for row in rows.all():
        amount = row.locator("td").nth(2).get_attribute("data-sort-value")
        action = row.locator("form.quota-revoke-form").get_attribute("action")
        expected_key = "audio-5" if row.locator("td").nth(2).get_attribute("data-sort-group") == "audio_seconds" else f"tokens-{amount}"
        assert action.endswith(f"/{grant_ids[expected_key].id}/revoke")
    assert allowances.locator("thead th").last.locator("button.usage-table-sort-button").count() == 0
    amount_button.click()
    assert rows.evaluate_all(
        "rows => rows.map(row => [row.cells[2].dataset.sortGroup, Number(row.cells[2].dataset.sortValue)])"
    ) == [["audio_seconds", 5], ["tokens", 12], ["tokens", 3]]
    page.set_viewport_size({"width": 1440, "height": 1000})
    allowances.screenshot(path=str(screenshot_dir / "quota-allowances-sorted-table-1440.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    allowances.screenshot(path=str(screenshot_dir / "quota-allowances-sorted-table-390.png"))
    history = page.get_by_role("region", name="Quota policy history").get_by_role("table")
    page.locator("details.quota-disclosure").filter(has_text="Quota history").locator("summary").click()
    history_reason = history.locator("thead th").filter(has_text="Reason")
    history_reason.locator("button").click()
    assert history_reason.get_attribute("aria-sort") == "ascending"
    assert page.locator("form.quota-revoke-form").count() == 3

    for width in (1440, 768, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        _assert_no_horizontal_overflow(page, width)
        if width == 1440:
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(
                path=str(screenshot_dir / "quotas-sorted-1440.png"),
                full_page=False, animations="disabled",
            )
        elif width == 390:
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(
                path=str(screenshot_dir / "quotas-sorted-390.png"),
                full_page=False, animations="disabled",
            )


def test_admin_team_picker_orders_searches_and_restores_per_tab_context(
    audit_report, audit_browser, synthetic_admin_context, make_team
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    suspended_name = (
        "Suspended Verification — Community Neurodevelopmental and Specialist "
        "Outpatient Service with a Full Long Team Name for Wrapping Checks"
    )
    suspended = make_team(name=suspended_name, status=TeamStatus.suspended)
    active_id = admin["team_id"]

    _open_admin(page, audit_report, admin)
    response = page.goto(f"/admin/usage?team_id={active_id}&view=users&range=90d", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert page.get_by_role("heading", name="Usage", exact=False).first.is_visible()
    page.set_viewport_size({"width": 1440, "height": 900})

    picker = page.locator("#admin-team-switcher")
    picker_summary = picker.locator("summary")
    assert picker_summary.get_attribute("aria-label") == "Choose team: Accessibility Review Team"
    picker_summary.focus()
    page.keyboard.press("Enter")
    assert picker.get_attribute("open") is not None
    options = page.locator("[data-team-option][data-team-id]")
    assert options.count() == 2
    assert [options.nth(index).get_attribute("data-team-status") for index in range(2)] == ["active", "suspended"]
    assert "Suspended" in options.nth(1).inner_text()
    long_name = options.nth(1).locator(".sidebar-team-name")
    assert long_name.inner_text() == suspended.name
    assert long_name.evaluate("el => el.scrollHeight > 22"), (
        "Long team name should wrap instead of disappearing in the picker"
    )
    option_bounds = options.nth(1).bounding_box()
    assert option_bounds and option_bounds["x"] >= 0 and option_bounds["x"] + option_bounds["width"] <= 1440
    search = page.locator("input[data-team-search]")
    search.fill("Suspended Verification")
    assert options.nth(0).is_hidden()
    assert options.nth(1).is_visible()
    search.fill("")
    _capture_admin_evidence(page, "sidebar-picker-open-desktop.png")

    # Capturing evidence scrolls the page, which intentionally closes the
    # picker. Reopen it before choosing the suspended team.
    if picker.get_attribute("open") is None:
        picker_summary.click()
    options.nth(1).click()
    page.wait_for_timeout(250)
    assert page.url.endswith(f"/admin/usage?team_id={suspended.id}&view=users&range=90d"), (
        "Team picker did not navigate; unsaved dialog visible="
        f"{page.locator('#admin-unsaved-modal').is_visible()}; POST form field differences="
        f"{page.evaluate('''() => [...document.querySelectorAll('form[method=post]')].map(form => ({
          id: form.id, action: new URL(form.action).pathname,
          dirtyOnLoad: form.hasAttribute('data-admin-dirty-on-load'),
          changed: [...form.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')]
            .filter(control => !control.disabled && (control.type === 'checkbox' || control.type === 'radio'
              ? control.checked !== control.defaultChecked
              : control.value !== control.defaultValue))
            .map(control => ({name: control.name, type: control.type}))
        }))''')}"
    )
    assert page.url.endswith(f"/admin/usage?team_id={suspended.id}&view=users&range=90d")
    selected_name = picker_summary.locator("strong")
    assert selected_name.evaluate("el => getComputedStyle(el).textOverflow") == "ellipsis"
    assert selected_name.evaluate("el => el.scrollWidth > el.clientWidth")

    storage = page.evaluate("""() => {
      const prefix = "openscribe.admin.navigation.v1:";
      return Object.entries(sessionStorage)
        .filter(([key]) => key.startsWith(prefix))
        .map(([key, raw]) => ({key, value: JSON.parse(raw)}));
    }""")
    assert len(storage) == 1
    assert set(storage[0]["value"]) == {"teamId", "section", "range"}
    assert storage[0]["value"]["teamId"] == str(suspended.id)
    assert storage[0]["value"]["range"] == "90d"
    assert storage[0]["value"]["section"] in {"usage:team", "usage:users", "usage:quotas"}

    # Global administration stays unscoped while the sidebar shows the
    # remembered team and points Usage back to its validated per-tab context.
    page.goto("/admin?tab=system-admins", wait_until="domcontentloaded")
    assert page.locator("#admin-team-return-link").count() == 0
    remembered_name = page.locator("#admin-team-scope-name")
    page.wait_for_function("el => el.textContent !== 'All teams'", arg=remembered_name.element_handle())
    assert remembered_name.inner_text() == suspended.name
    first_usage_href = page.locator("#admin-usage-nav-link").get_attribute("href")
    assert first_usage_href == f"/admin/usage?team_id={suspended.id}&view=users&range=90d"
    assert page.locator("#admin-team-registry").evaluate(
        "template => template.content.querySelectorAll('[data-team-id]').length"
    ) == 2
    _capture_admin_evidence(page, "sidebar-global-remembered-team-desktop.png")

    # A fresh browser tab has an independent navigation hint.
    second = page.context.new_page()
    second.goto(f"/admin?team_id={active_id}&team_tab=overview", wait_until="domcontentloaded")
    second.goto("/admin?tab=system-admins", wait_until="domcontentloaded")
    assert second.locator("#admin-team-return-link").count() == 0
    second_scope_name = second.locator("#admin-team-scope-name")
    second.wait_for_function("el => el.textContent !== 'All teams'", arg=second_scope_name.element_handle())
    assert second_scope_name.inner_text() != suspended.name
    second_usage_href = second.locator("#admin-usage-nav-link").get_attribute("href")
    assert second_usage_href == f"/admin/usage?team_id={active_id}&view=team&range=30d"
    assert page.locator("#admin-usage-nav-link").get_attribute("href") == first_usage_href
    second.close()

    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("#admin-usage-nav-link").click()
    assert page.url.endswith(first_usage_href)
    restored_picker_summary = page.locator("#admin-team-switcher summary")
    assert restored_picker_summary.get_attribute("aria-label") == f"Choose team: {suspended.name}"
    assert restored_picker_summary.is_visible()

    # On a short desktop viewport, the fixed picker overlaps the sticky team
    # tabs. Verify its painted hit target remains above the sticky navigation.
    page.goto(f"/admin?team_id={active_id}&team_tab=members", wait_until="domcontentloaded")
    page.set_viewport_size({"width": 1440, "height": 400})
    page.locator("#admin-team-switcher summary").click()
    panel = page.locator("#admin-team-switcher-panel")
    page.wait_for_function("el => el.classList.contains('is-positioned')", arg=panel.element_handle())
    popup_geometry = page.evaluate("""() => {
      const panel = document.querySelector('#admin-team-switcher-panel');
      const tabs = document.querySelector('nav.tabs');
      const p = panel.getBoundingClientRect(), t = tabs.getBoundingClientRect();
      const x1 = Math.max(p.left,t.left), x2 = Math.min(p.right,t.right);
      const y1 = Math.max(p.top,t.top), y2 = Math.min(p.bottom,t.bottom);
      const x=(x1+x2)/2, y=(y1+y2)/2;
      return {left:p.left,top:p.top,right:p.right,bottom:p.bottom,width:innerWidth,height:innerHeight,
        overlap:(x2-x1)*(y2-y1), hit:document.elementFromPoint(x,y)};
    }""")
    assert popup_geometry["left"] >= 0 and popup_geometry["right"] <= popup_geometry["width"], popup_geometry
    assert popup_geometry["top"] >= 0 and popup_geometry["bottom"] <= popup_geometry["height"], popup_geometry
    assert popup_geometry["overlap"] > 0, popup_geometry
    assert page.evaluate("""() => {
      const panel = document.querySelector('#admin-team-switcher-panel');
      const tabs = document.querySelector('nav.tabs');
      const p=panel.getBoundingClientRect(), t=tabs.getBoundingClientRect();
      const x=(Math.max(p.left,t.left)+Math.min(p.right,t.right))/2;
      const y=(Math.max(p.top,t.top)+Math.min(p.bottom,t.bottom))/2;
      return panel.contains(document.elementFromPoint(x,y));
    }"""), "Picker popup should paint above overlapping sticky tabs"
    if screenshot_dir:
        page.screenshot(
            path=str(Path(screenshot_dir) / "sidebar-picker-over-sticky-tabs-1440x400.png"),
            full_page=False,
            animations="disabled",
        )
    page.locator("#admin-team-switcher summary").click()

    page.goto(f"/admin/usage?team_id={suspended.id}&view=users&range=90d", wait_until="domcontentloaded")
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("#admin-team-switcher summary").click()
        page.locator("[data-all-teams-option]").click()
    assert page.url.endswith("/admin/usage?view=team&range=90d")
    assert page.evaluate("""() => Object.keys(sessionStorage)
      .filter(key => key.startsWith("openscribe.admin.navigation.v1:"))""") == []

    page.set_viewport_size({"width": 390, "height": 700})
    nav_toggle = page.locator("#admin-mobile-nav-toggle")
    nav_toggle.focus()
    page.keyboard.press("Enter")
    assert nav_toggle.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert nav_toggle.get_attribute("aria-expanded") == "false"
    assert nav_toggle.evaluate("el => document.activeElement === el")

    page.goto(f"/admin/usage?team_id={suspended.id}&view=users&range=90d", wait_until="domcontentloaded")
    picker = page.locator("#admin-team-switcher")
    picker.locator("summary").click()
    assert picker.get_attribute("open") is not None
    page.locator("main").click(position={"x": 4, "y": 4})
    assert picker.get_attribute("open") is None

    for width in (390, 320):
        page.set_viewport_size({"width": width, "height": 760})
        _assert_no_horizontal_overflow(page, width)
        picker = page.locator("#admin-team-switcher")
        if not picker.evaluate("el => el.open"):
            picker.locator("summary").click()
        assert picker.locator("#admin-team-switcher-panel").is_visible()
        assert picker.locator("summary").get_attribute("aria-label").startswith("Choose team:")
        assert picker.locator(
            f"[data-team-option][data-team-id='{suspended.id}'] .sidebar-team-name"
        ).inner_text() == suspended.name
        _assert_no_horizontal_overflow(page, width)
        if width == 390:
            _capture_admin_evidence(page, "sidebar-picker-open-mobile-390.png")
        picker.locator("summary").click()

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto("/admin?tab=system-admins", wait_until="domcontentloaded")
    account = page.locator("#admin-account-disclosure")
    account.locator("summary").click()
    account_panel = account.locator(".account-disclosure-panel")
    page.wait_for_function(
        "el => el.classList.contains('is-positioned') && el.getBoundingClientRect().height > 0",
        arg=account_panel.element_handle(),
    )
    account_geometry = page.evaluate("""() => {
      const summary = document.querySelector('#admin-account-disclosure > summary').getBoundingClientRect();
      const panel = document.querySelector('#admin-account-disclosure .account-disclosure-panel').getBoundingClientRect();
      return {summaryTop: summary.top, panelBottom: panel.bottom, left: panel.left, right: panel.right,
        width: innerWidth, position: getComputedStyle(document.querySelector('#admin-account-disclosure .account-disclosure-panel')).position};
    }""")
    assert account_geometry["position"] == "fixed"
    assert account_geometry["panelBottom"] <= account_geometry["summaryTop"] + 2, account_geometry
    assert account_geometry["left"] >= 0 and account_geometry["right"] <= account_geometry["width"], account_geometry
    if screenshot_dir:
        page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
        page.screenshot(
            path=str(Path(screenshot_dir) / "sidebar-account-open-desktop-1440.png"),
            full_page=False,
            animations="disabled",
        )
    logout_form = account.locator('form[action="/logout"]')
    assert logout_form.get_attribute("method").lower() == "post"
    with page.expect_navigation(wait_until="domcontentloaded"):
        logout_form.get_by_role("button", name="Log out", exact=True).click()
    remaining_keys = page.evaluate("""() => Object.keys(sessionStorage)
      .filter(key => key.startsWith("openscribe.admin.navigation.v1:"))""")
    assert remaining_keys == []
    assert not blocked


def test_admin_sidebar_unsaved_guard_and_team_creation_validation_preserve_drafts(
    audit_report, audit_browser, synthetic_admin_context, make_team
):
    page, blocked = audit_browser
    admin = synthetic_admin_context
    other = make_team(name="Second Draft Guard Team")
    _open_admin(page, audit_report, admin)
    page.goto(f"/admin?team_id={admin['team_id']}&team_tab=defaults", wait_until="domcontentloaded")

    retention = page.locator("#team-retention-days")
    retention.fill("47")
    picker = page.locator("#admin-team-switcher")
    if not picker.evaluate("el => el.open"):
        picker.locator("summary").click()
    page.locator(f"[data-team-option][data-team-id='{other.id}']").click()
    guard = page.locator("#admin-unsaved-modal")
    assert guard.is_visible()
    assert page.get_by_role("button", name="Stay", exact=True).is_visible()
    screenshot_dir = os.environ.get("OPENSCRIBE_ACCESSIBILITY_SCREENSHOT_DIR")
    if screenshot_dir:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.screenshot(
            path=str(Path(screenshot_dir) / "sidebar-unsaved-dialog-desktop-1440.png"),
            full_page=False,
            animations="disabled",
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=str(Path(screenshot_dir) / "sidebar-unsaved-dialog-mobile-390.png"),
            full_page=False,
            animations="disabled",
        )
        page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="Stay", exact=True).click()
    assert page.url.endswith(f"team_id={admin['team_id']}&team_tab=defaults")
    assert retention.input_value() == "47"

    if not picker.evaluate("el => el.open"):
        picker.locator("summary").click()
    page.locator(f"[data-team-option][data-team-id='{other.id}']").click()
    page.get_by_role("button", name="Discard changes", exact=True).click()
    page.wait_for_url(re.compile(rf"/admin\?team_id={other.id}&team_tab=defaults"))

    page.goto(
        f"/admin/usage?view=team&range=30d&create_team=1&team_id={other.id}",
        wait_until="domcontentloaded",
    )
    modal = page.locator("#admin-create-team-modal")
    assert modal.is_visible()
    modal.locator("#admin-create-team-name").fill("Accessibility Review Team")
    modal.locator("#admin-create-team-status").select_option("suspended")
    modal.locator("#admin-create-team-retention").fill("47")
    with page.expect_navigation(wait_until="domcontentloaded"):
        modal.get_by_role("button", name="Create team", exact=True).click()
    assert page.locator("#admin-create-team-modal").is_visible()
    assert page.locator("#admin-create-team-name").input_value() == "Accessibility Review Team"
    assert page.locator("#admin-create-team-status").input_value() == "suspended"
    assert page.locator("#admin-create-team-retention").input_value() == "47"
    _capture_admin_evidence(page, "sidebar-create-validation-desktop.png")
    page.set_viewport_size({"width": 390, "height": 760})
    _capture_admin_evidence(page, "sidebar-create-validation-mobile-390.png")
    persisted = page.evaluate("""() => JSON.stringify(Object.entries(sessionStorage)
      .filter(([key]) => key.startsWith("openscribe.admin.navigation.v1:"))
      .map(([, value]) => JSON.parse(value)))""")
    assert "Accessibility Review Team" not in persisted
    assert all(set(value) == {"teamId", "section", "range"} for value in json.loads(persisted))
    assert not blocked
