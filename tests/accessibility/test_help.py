"""Authenticated, synthetic accessibility checks for the user Help page."""

from __future__ import annotations

import pytest

from tests.accessibility.audit import (
    STATE_HELP_DESKTOP,
    STATE_HELP_MOBILE_DRAWER,
    STATE_HELP_NARROW,
)
from tests.accessibility.conftest import login, scan
from tests.accessibility.test_pages import _assert_text_contrast, _contrast_ratios


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


HELP_STATES = (STATE_HELP_DESKTOP, STATE_HELP_NARROW, STATE_HELP_MOBILE_DRAWER)


def test_contrast_measurement_uses_nested_opaque_background(audit_browser):
    page, _blocked = audit_browser
    page.set_content(
        "<main><div style='background:#fff'><div style='background:#000'>"
        "<span id='contrast-probe' style='color:#fff'>Synthetic text</span>"
        "</div></div></main>"
    )
    ratios = _contrast_ratios(page, ["#contrast-probe"])
    assert len(ratios) == 1
    assert ratios[0] >= 20.9, "Contrast helper ignored the nearer nested opaque background"


def _is_keyboard_visible_focus(locator) -> bool:
    return locator.evaluate(
        """element => {
          const style = getComputedStyle(element);
          return element.matches(':focus-visible')
            && style.outlineStyle !== 'none'
            && parseFloat(style.outlineWidth) >= 2;
        }"""
    )


def _assert_target_sizes(page) -> None:
    targets = page.locator(
        '.workspace-sidebar a[href], .workspace-sidebar button:not(:disabled), '
        '[data-workspace-drawer-toggle], [data-sidebar-resize], .workspace-help__contents a'
    )
    measurements = targets.evaluate_all(
        "elements => elements.filter(el => el.getClientRects().length).map(el => ({"
        "label: el.getAttribute('aria-label') || el.textContent.trim(), "
        "width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}))"
    )
    undersized = [item for item in measurements if item['width'] < 24 or item['height'] < 24]
    assert not undersized, f"Help navigation has pointer targets below 24 CSS px: {undersized}"


@pytest.mark.accessibility_states({state: "executed" for state in HELP_STATES})
def test_authenticated_help_accessibility_and_navigation(
    audit_report,
    audit_browser,
    synthetic_audit_user,
    axe_source,
):
    page, blocked = audit_browser
    page.set_viewport_size({"width": 1280, "height": 800})
    login(page, synthetic_audit_user, audit_report)
    response = page.goto("/workspace/help", wait_until="domcontentloaded")
    assert response is not None and response.ok
    assert page.title() == "Help · OpenScribe"
    assert page.get_by_role("main").count() == 1
    assert page.get_by_role("article", name="Help").count() == 1
    assert page.get_by_role("navigation", name="Help topics").count() == 1
    assert page.get_by_role("heading", level=1).inner_text() == "Help"
    assert page.locator(".workspace-help__section h2").count() == 13
    assert page.locator('.workspace-sidebar a[href="/workspace/help"][aria-current="page"]').count() == 1

    # The first keyboard stop bypasses the repeated workspace navigation. Its
    # activation moves both the viewport and keyboard focus into main content.
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Tab")
    skip = page.locator(".workspace-help__skip-link")
    assert skip.evaluate("element => document.activeElement === element")
    assert skip.is_visible() and _is_keyboard_visible_focus(skip)
    page.keyboard.press("Enter")
    main = page.locator("#workspace-main")
    assert main.evaluate("element => document.activeElement === element")
    assert page.evaluate("location.hash") == "#workspace-main"

    first_topic = page.locator('.workspace-help__contents a[href="#help-orientation-title"]')
    first_topic.focus()
    page.keyboard.press("Enter")
    heading = page.locator("#help-orientation-title")
    assert heading.evaluate("element => document.activeElement === element"), "TOC activation did not move focus to its heading"
    assert _is_keyboard_visible_focus(heading)
    assert page.evaluate("location.hash") == "#help-orientation-title"
    bounds = heading.bounding_box()
    assert bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 800
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    _assert_target_sizes(page)
    _assert_text_contrast(
        page,
        [
            ".workspace-help__section p",
            ".workspace-help__settings dd",
            ".workspace-help__contents a",
            ".workspace-help__header > p:not(.settings-kicker)",
        ],
    )
    desktop_scan = scan(page, audit_report, STATE_HELP_DESKTOP, axe_source, blocked)
    assert not desktop_scan.get("violations"), "axe found a WCAG violation on desktop Help"
    active_help_link = page.locator('.workspace-sidebar a[href="/workspace/help"]')
    page.locator("#help-privacy-title").scroll_into_view_if_needed()
    active_help_link.focus()
    sidebar_bounds = page.locator("#workspace-sidebar").bounding_box()
    link_bounds = active_help_link.bounding_box()
    assert sidebar_bounds and sidebar_bounds["y"] == 0 and sidebar_bounds["height"] == 800
    assert link_bounds and link_bounds["y"] >= sidebar_bounds["y"]
    assert link_bounds["y"] + link_bounds["height"] <= 800
    assert _is_keyboard_visible_focus(active_help_link)
    resize = page.locator("[data-sidebar-resize]")
    assert resize.is_visible() and resize.get_attribute("role") == "separator"
    assert resize.get_attribute("aria-valuenow") == "288"
    resize.focus()
    assert _is_keyboard_visible_focus(resize)
    page.keyboard.press("ArrowRight")
    assert resize.get_attribute("aria-valuenow") == "304"
    page.keyboard.press("Home")
    assert resize.get_attribute("aria-valuenow") == "64"
    page.keyboard.press("ArrowRight")
    assert resize.get_attribute("aria-valuenow") == "192"
    page.keyboard.press("ArrowLeft")
    assert resize.get_attribute("aria-valuenow") == "64"
    page.keyboard.press("End")
    assert resize.get_attribute("aria-valuenow") == "384"
    page.keyboard.press("Home")
    assert resize.get_attribute("aria-valuenow") == "64"
    assert not page.get_by_role("button", name="Make sidebar narrower").is_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    page.get_by_role("button", name="Expand sidebar").click()
    assert resize.get_attribute("aria-valuenow") == "288"
    page.get_by_role("button", name="Make sidebar narrower").click()
    assert resize.get_attribute("aria-valuenow") == "272", "Pointer alternative did not narrow the sidebar without dragging"
    page.get_by_role("button", name="Make sidebar wider").click()
    assert resize.get_attribute("aria-valuenow") == "288", "Pointer alternative did not widen the sidebar without dragging"

    # A topic deeper in the long page must remain visible beneath the sticky
    # mobile header when a keyboard user follows a contents link.
    for width, height in ((320, 800), (375, 812), (740, 900)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto("/workspace/help", wait_until="domcontentloaded")
        page.locator('.workspace-help__contents a[href="#help-copy-title"]').focus()
        page.keyboard.press("Enter")
        target = page.locator("#help-copy-title")
        assert target.evaluate("element => document.activeElement === element")
        target_bounds = target.bounding_box()
        header_bottom = page.locator(".workspace-mobile-header").bounding_box()["y"] + page.locator(".workspace-mobile-header").bounding_box()["height"]
        assert target_bounds and target_bounds["y"] >= header_bottom
        assert target_bounds["y"] + target_bounds["height"] <= height
        overflow = page.evaluate("""() => ({
          width: document.documentElement.clientWidth,
          scroll: document.documentElement.scrollWidth,
          elements: [...document.querySelectorAll('body *')]
            .map(element => ({element, rect: element.getBoundingClientRect()}))
            .filter(item => item.rect.right > document.documentElement.clientWidth + 1)
            .slice(0, 5)
            .map(item => ({tag: item.element.tagName, className: item.element.className, right: item.rect.right}))
        })""")
        assert overflow["scroll"] <= overflow["width"], f"Help overflowed horizontally at {width}px: {overflow}"
        if width == 320:
            narrow_scan = scan(page, audit_report, STATE_HELP_NARROW, axe_source, blocked)
            assert not narrow_scan.get("violations"), "axe found a WCAG violation at 320 CSS px"

    # Text-spacing overrides must not hide content or force two-dimensional
    # scrolling. This is an override simulation, not browser zoom.
    page.set_viewport_size({"width": 320, "height": 800})
    page.add_style_tag(content="""
      .workspace-help, .workspace-help * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; }
      .workspace-help p { margin-bottom: 2em !important; }
    """)
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    assert page.locator("#help-privacy-title").is_visible()
    assert page.locator("#help-copy p").first.evaluate("element => getComputedStyle(element).letterSpacing") != "normal"
    diagram_spacing = page.evaluate("""() => {
      const elements = [...document.querySelectorAll('.workspace-help__map figcaption, .workspace-help__map strong, .workspace-help__map span')]
        .filter(element => element.getClientRects().length);
      const items = elements.map(element => {
        const rect = element.getBoundingClientRect();
        return {element, rect, overflows: element.scrollWidth > element.clientWidth + 1};
      });
      const overlaps = [];
      for (let left = 0; left < items.length; left += 1) {
        for (let right = left + 1; right < items.length; right += 1) {
          const a = items[left].rect, b = items[right].rect;
          if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
              && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) {
            overlaps.push([items[left].element.tagName, items[right].element.tagName]);
          }
        }
      }
      return {overflows: items.filter(item => item.overflows).map(item => item.element.tagName), overlaps};
    }""")
    assert not diagram_spacing["overflows"], f"Workspace diagram text clipped under spacing overrides: {diagram_spacing}"
    assert not diagram_spacing["overlaps"], f"Workspace diagram labels overlapped under spacing overrides: {diagram_spacing}"

    # Exercise the actual shared mobile drawer: background content becomes
    # inert, focus stays in the drawer, and Escape restores the invoking button.
    menu = page.locator("[data-workspace-drawer-toggle]")
    sidebar = page.locator("#workspace-sidebar")
    legal_footer = page.locator(".operator-legal-footer")
    sidebar.locator(".workspace-sidebar__identity > strong").evaluate(
        "element => { element.textContent = 'Synthetic Accessibility User With A Long Display Name'; }"
    )
    sidebar.locator(".workspace-sidebar__identity > small").evaluate(
        "element => { element.textContent = 'synthetic-help-accessibility-very-long-email-address@example.test'; }"
    )
    legal_footer.evaluate("element => element.setAttribute('inert', '')")
    original_inert = {
        "sidebar": sidebar.get_attribute("inert"),
        "main": page.locator("#workspace-main").get_attribute("inert"),
        "header": page.locator(".workspace-mobile-header").get_attribute("inert"),
        "legal_footer": legal_footer.get_attribute("inert"),
    }
    assert original_inert["legal_footer"] is not None, "Synthetic pre-existing legal-footer inert state was not installed"
    menu.focus()
    page.keyboard.press("Enter")
    assert menu.get_attribute("aria-expanded") == "true"
    assert sidebar.evaluate("element => element.contains(document.activeElement)")
    assert sidebar.get_attribute("inert") is None
    assert page.locator("#workspace-main").get_attribute("inert") is not None
    assert legal_footer.get_attribute("inert") is not None
    assert page.locator(".browser-storage-notice").get_attribute("inert") is not None
    identity_layout = page.evaluate(r"""() => {
      const sidebar = document.querySelector('#workspace-sidebar');
      const scrim = document.querySelector('[data-workspace-drawer-close]');
      const identity = sidebar.querySelector('.workspace-sidebar__identity');
      const name = identity.querySelector('strong');
      const email = identity.querySelector('small');
      const bounds = element => {
        const rect = element.getBoundingClientRect();
        return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
      };
      return {
        sidebar: bounds(sidebar), identity: bounds(identity), name: bounds(name), email: bounds(email),
        nameFits: name.scrollWidth <= name.clientWidth && name.scrollHeight <= name.clientHeight,
        emailUsesEllipsis: getComputedStyle(email).overflowX === 'hidden' && getComputedStyle(email).textOverflow === 'ellipsis',
        sidebarHasOpaqueBackground: (() => {
          const background = getComputedStyle(sidebar).backgroundColor;
          return background.startsWith('rgb(') || /,\s*1\)$/.test(background);
        })(),
        sidebarZ: Number.parseInt(getComputedStyle(sidebar).zIndex, 10),
        scrimZ: Number.parseInt(getComputedStyle(scrim).zIndex, 10),
      };
    }""")
    assert identity_layout["identity"]["left"] >= identity_layout["sidebar"]["left"]
    assert identity_layout["name"]["left"] >= identity_layout["sidebar"]["left"]
    assert identity_layout["name"]["right"] <= identity_layout["sidebar"]["right"]
    assert identity_layout["email"]["right"] <= identity_layout["sidebar"]["right"]
    assert identity_layout["nameFits"], "Long synthetic sidebar name was clipped instead of wrapping"
    assert identity_layout["emailUsesEllipsis"], "Long synthetic email lacks its intended ellipsis treatment"
    assert identity_layout["sidebarHasOpaqueBackground"]
    assert identity_layout["sidebarZ"] > identity_layout["scrimZ"]
    assert sidebar.locator(".workspace-sidebar__collapse").is_hidden(), "A nonfunctional desktop collapse control remains in the mobile drawer"
    locked_help_link = sidebar.locator('a[href="/workspace/help"]')
    page.evaluate("document.dispatchEvent(new Event('openscribe:recording-started'))")
    assert locked_help_link.get_attribute("tabindex") == "-1"
    for _ in range(30):
        page.keyboard.press("Tab")
        assert sidebar.evaluate("element => element.contains(document.activeElement)"), "Tab escaped the open mobile navigation drawer"
        assert not locked_help_link.evaluate("element => element === document.activeElement"), "A recording-locked link remained keyboard reachable"
    for _ in range(30):
        page.keyboard.press("Shift+Tab")
        assert sidebar.evaluate("element => element.contains(document.activeElement)"), "Shift+Tab escaped the open mobile navigation drawer"
        assert not locked_help_link.evaluate("element => element === document.activeElement"), "A recording-locked link remained keyboard reachable"
    page.evaluate("document.dispatchEvent(new Event('openscribe:recording-stopped'))")
    assert locked_help_link.get_attribute("tabindex") is None
    page.evaluate("Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})))")
    identity_contrast = _contrast_ratios(
        page,
        [".workspace-sidebar__identity > strong", ".workspace-sidebar__identity > small"],
    )
    assert len(identity_contrast) == 2 and min(identity_contrast) >= 4.5, (
        f"Visible synthetic drawer identity text contrast is below WCAG AA: {identity_contrast}"
    )
    audit_report.record_keyboard(
        "workspace_help_drawer_identity_contrast",
        "passed",
        "After drawer animation settled, measured synthetic long-name/email contrast against nested opaque backgrounds: "
        f"display name {identity_contrast[0]:.2f}:1; email {identity_contrast[1]:.2f}:1. "
        "Axe still marks these two mobile drawer nodes for manual color-contrast review.",
    )
    drawer_scan = scan(page, audit_report, STATE_HELP_MOBILE_DRAWER, axe_source, blocked)
    assert not drawer_scan.get("violations"), "axe found a WCAG violation with the mobile drawer open"
    page.keyboard.press("Escape")
    assert menu.get_attribute("aria-expanded") == "false"
    assert menu.evaluate("element => document.activeElement === element"), "Escape did not restore focus to the mobile menu button"
    assert sidebar.get_attribute("inert") is not None
    assert sidebar.get_attribute("inert") == original_inert["sidebar"]
    assert page.locator("#workspace-main").get_attribute("inert") == original_inert["main"]
    assert page.locator(".workspace-mobile-header").get_attribute("inert") == original_inert["header"]
    assert legal_footer.get_attribute("inert") == original_inert["legal_footer"]
    page.keyboard.press("Enter")
    close_button = sidebar.get_by_role("button", name="Close workspace navigation")
    assert close_button.is_visible()
    close_button.focus()
    page.keyboard.press("Enter")
    assert menu.get_attribute("aria-expanded") == "false"
    assert menu.evaluate("element => document.activeElement === element"), "Drawer close button did not restore focus to its opener"

    # Forced-colors rendering keeps the page and keyboard focus indicator.
    page.emulate_media(forced_colors="active")
    first_toc_link = page.locator(".workspace-help__contents a").first
    first_toc_link.focus()
    page.keyboard.press("Tab")
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'")
    assert page.locator("#help-privacy-title").is_visible()
    audit_report.record_keyboard(
        "workspace_help_navigation",
        "passed",
        "Synthetic keyboard checks covered skip-to-main, focused TOC activation, visible focus, mobile drawer Tab/Shift+Tab containment, Escape focus restoration, 320/375/740 CSS-pixel reflow, spacing overrides, and forced-colors focus. Actual browser zoom and screen-reader speech were not tested.",
    )
