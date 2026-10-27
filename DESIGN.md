---
name: OpenScribe
description: A quiet, practical visual system for clinical work and organisation administration.
colors:
  primary: "#1D4F5E"
  primary-soft: "#3D7A8C"
  primary-pale: "#E0F2F5"
  paper: "#FAF8F5"
  parchment: "#F5F1EB"
  card: "#FFFFFF"
  ink: "#1A202C"
  slate: "#2D3748"
  muted: "#718096"
  border: "#E2DED6"
  border-strong: "#D8D2C8"
  warm-accent: "#F6E8BB"
  green-accent: "#DFF2E2"
  success: "#38A169"
  warning: "#D69E2E"
  error: "#C53030"
  status-ready: "#0E6B76"
  admin-muted: "#56616F"
  admin-border-strong: "#81776B"
  admin-success: "#246B46"
  admin-warning: "#80520A"
  admin-focus: "#0B667B"
typography:
  display:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "2rem"
    lineHeight: 1
    letterSpacing: "-0.04em"
  body:
    fontFamily: "DM Sans, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "DM Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.7rem"
    fontWeight: 700
    letterSpacing: "0.09em"
rounded:
  control: "11px"
  surface: "16px"
  large-surface: "22px"
  extra-large-surface: "24px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.card}"
    rounded: "{rounded.control}"
    padding: "11px 17px"
    height: "44px"
  button-secondary:
    backgroundColor: "rgba(255, 255, 255, 0.74)"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 17px"
    height: "44px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "11px 17px"
    height: "44px"
  button-danger:
    backgroundColor: "rgba(197, 48, 48, 0.08)"
    textColor: "{colors.error}"
    rounded: "{rounded.control}"
    padding: "11px 17px"
    height: "44px"
  input-text:
    backgroundColor: "{colors.parchment}"
    textColor: "{colors.ink}"
    rounded: "10px"
    padding: "0 13px"
    height: "44px"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.slate}"
    rounded: "7px"
    padding: "0.55rem 0.7rem"
  status-chip:
    backgroundColor: "{colors.card}"
    textColor: "{colors.slate}"
    rounded: "999px"
    padding: "0.375rem 0.75rem"
  card:
    backgroundColor: "rgba(255, 255, 255, 0.94)"
    textColor: "{colors.ink}"
    rounded: "{rounded.large-surface}"
    padding: "0"
  quota-limit-selector:
    backgroundColor: "{colors.card}"
    textColor: "{colors.slate}"
    rounded: "7px"
    padding: "7px 13px"
---

# Design System: OpenScribe

## Overview

**Creative North Star: “The Quiet Study”**

OpenScribe’s visual language is calm, trustworthy and practical. It pairs paper-like warm surfaces with precise dark text and a restrained teal accent, leaving the content and the next useful action easy to find. The interface should feel considered and steady rather than like a flashy dashboard.

The product serves clinicians preparing reviewable drafts and technical administrators operating a deployment. This system records the current shared web styling and calls out the admin workspace’s deliberate accessibility overrides; those overrides are not global token replacements. Typography lists declared stacks, not a guarantee that a named font file is loaded. Maintain the product’s established terminology and target WCAG 2.2 AA without claiming certification.

**Key Characteristics:**
- Warm paper and parchment surfaces with dark, readable text.
- Deep Teal is a focused action and navigation accent.
- Restrained and softly layered: borders define cards; shadows lift menus and dialogs.
- Shared site values and admin-specific accessibility values remain distinct.

## Colors

The palette is named for its material and role: **Paper**, **Parchment**, **Ink**, and **Deep Teal**. The frontmatter values are the shared tokens from `app/static/css/tokens.css`; named status colors stay semantic.

The companion `.impeccable/design.json` includes derived tonal ramps for preview swatches. These are tooling metadata, not color scales implemented by the site.

### Primary
- **Deep Teal** (`primary`): the shared accent for primary actions, active navigation and links.
- **Soft Teal** (`primary-soft`): secondary accent for hover and supporting emphasis.
- **Pale Teal** (`primary-pale`): quiet selected, ready and focus-adjacent surfaces.

### Tertiary
- **Warm Accent** (`warm-accent`) and **Green Accent** (`green-accent`): small supporting state surfaces, not large page fills.
- **Success**, **Warning**, and **Error** (`success`, `warning`, `error`): semantic status colors.
- **Ready Teal** (`status-ready`): the deeper teal used by the shared ready badge.

### Neutral
- **Paper** (`paper`): the site canvas.
- **Parchment** (`parchment`): warm secondary surfaces and navigation background.
- **White Card** (`card`): content surfaces and form controls.
- **Ink** (`ink`) and **Slate** (`slate`): primary and supporting text.
- **Muted** (`muted`): tertiary labels and status details in shared surfaces; Scribe workspace has a scoped muted value of `#596579`.
- **Warm Border** (`border`) and **Soft Border** (`border-strong`): dividers and shared control outlines.

### Admin overrides

The admin template intentionally has separate `admin-*` values for muted text, stronger control boundaries, success, warning and focus. The admin-muted and admin-status colors are darker than their shared counterparts; the admin control border is darker to define controls, while the standard decorative border stays light. Admin focus is a solid teal outline with offset and a system-color forced-colors treatment. Keep these values scoped to admin styling unless a separately reviewed change establishes shared behavior.

## Typography

**Display Font:** Georgia, Times New Roman, serif (declared stack)
**Body Font:** DM Sans, ui-sans-serif, system-ui, sans-serif (declared stack)
**Label/Mono Font:** No shared monospaced label face is established.

**Character:** The serif display face gives headings a quiet editorial presence; the sans-serif body stack keeps controls, navigation and dense operational information direct. The source declares these stacks but does not import DM Sans or Fraunces font files, so rendering may use the system fallback. Fraunces appears in a scoped Scribe style declaration, not as the shared display face.

### Hierarchy
- **Display** (2rem, 1): shared brand text uses Georgia with tight tracking; admin h1 headings use Georgia at 2.45rem/1.05 and h2 at 1.65rem.
- **Headline** (500, 1.3–1.65rem): section and card titles, with a serif display face.
- **Title** (600–700, about 1rem): form and row labels in the body face.
- **Body** (400, 1rem, 1.5): generated Tailwind base for Scribe content; admin base body is scoped to 14px with 1.45 line height.
- **Label** (700, 0.7rem, .09em, uppercase): Scribe workspace navigation group labels; admin table labels use 11px/.04em.

## Layout

The shared Scribe workspace uses a flexible main column beside an 18rem sidebar, then changes to a mobile header and drawer below 767px (`app/static/css/workspace.css`). Content columns are bounded for reading, while editing surfaces can use the available width. Values of 8, 12, 16 and 24px recur in component gaps and padding; they are observed values, not a declared CSS spacing-token scale. Use gaps and padding to group related tasks rather than enclosing every row in a card.

The admin workspace has its own 240px navigation rail and selected-team content, reduces the rail at 1180px, and moves global navigation into a disclosure at 820px. Selected-team workspace navigation keeps Overview, Members and Quotas primary; Providers and Settings group the remaining team pages. Usage opens a dedicated standalone admin route. Within a validated team scope, its local Team/Users/Quotas navigation switches operational reporting, team-member aggregates and quota management without showing the selected-team workspace header or primary tabs. The top team picker changes Usage scope while preserving its view and range; on global pages it shows the remembered team name in a muted row, while the Usage link restores that validated per-tab context. The native range disclosure changes reporting period without a team selector. Team and global reporting show a compact five-column summary table for total tokens, audio hours, input tokens, output tokens and generations. Team and Users views expose the reporting period through a native inline disclosure and direct links; Quotas omits this control because quota periods are separate, while navigation retains the last reporting range. The bottom account disclosure contains account actions; Create Team opens a modal, and dirty forms ask before navigation. At narrow widths, a separate **Team navigation** disclosure contains selected-team workspace links and shows the current page in its summary. Member and provider-policy data reflow into labeled stacked rows on narrow screens, while wide operational data stays in named scroll regions. The team retention setting pairs its guidance with the input and Save action on desktop, then stacks the guidance above the compact controls on mobile. These are admin-specific details, not the Scribe workspace layout contract (`app/templates/admin_mockup.html`).

Admin configuration forms use padded white panels with visible labels above full-size controls. Related fields sit in compact pairs where there is room. Team selection uses the searchable top picker; team creation uses a modal with an unsaved-change guard. Global Defaults stacks its template and quick-action sections, keeping the quick-action editor collapsed until requested or opened for editing. De-ID registry editing groups connection, request/response mapping and capability controls; bearer-token fields remain blank and write-only until a new value is entered. These patterns apply to the administrative setup surfaces and do not change their existing POST or credential-handling contracts.

## Elevation & Depth

The shared system is restrained and softly layered. Tonal surface changes and thin borders carry most resting structure. Shared component buttons have a subtle teal shadow; popover menus and dialogs use larger diffuse shadows to clarify that they sit above the page. Admin quota cards stay flat, while open forms and dialogs can lift. Focus indicators are interaction affordances, not decorative elevation.

### Shadow Vocabulary
- **Button lift** (`0 12px 24px rgba(29, 79, 94, 0.18)`): shared primary button depth.
- **Menu lift** (`0 4px 16px rgba(31, 42, 53, 0.07), 0 1px 3px rgba(31, 42, 53, 0.04)`): shared popover menus.
- **Dialog lift** (`0 24px 70px rgba(26, 32, 44, 0.18)`): admin dialogs above the workspace.

## Shapes

Shared surfaces use softly curved corners: 11px small controls, 16px stat groupings, and 22–24px larger settings cards and shells. Shared `.field-input` controls use 44px height; this is separate from denser admin form sizing. The admin workspace uses 5–7px corners for many buttons and controls, and 11–12px corners for quota cards. In both areas, thin borders define card edges and control boundaries; keep decorative dividers lighter than focus and interactive outlines.

## Components

### Buttons

Buttons are quiet, clear actions with a consistent shared 44px hit area. The admin workspace has a separate compact 38px base button and visible focus outline; do not copy that compact sizing into shared controls by default.

- **Shape:** Gently curved shared controls (11px); compact admin buttons use 5px.
- **Primary:** Deep Teal fill, white text, 11px 17px padding; shared buttons lift slightly on hover/focus.
- **Secondary:** White translucent fill, Ink text and visible warm outline.
- **Ghost:** Transparent fill with Deep Teal text and a restrained border.
- **Danger:** Soft red fill, red text and border; reserve for destructive actions.
- **Focus:** The shared button component currently removes its outline on hover/focus. Admin uses a solid 3px teal outline offset 2px, with forced-colors support. Preserve this known difference; do not imply shared focus parity.

### Chips

Status badges are compact, pill-shaped labels with semantic text and a small status dot. Ready uses the deeper teal state; neutral uses a white surface and Slate text. Keep the text label; the dot alone does not communicate status.

### Cards / Containers

Cards use white or parchment surfaces and a thin warm border. The shared settings card has 22px corners with content rows padded individually; admin quota cards are flat and use smaller 11–12px corners. Use shadows for menus and dialogs, not as a default card outline.

### Inputs / Fields

Shared `.field-input` controls use a parchment surface, 10px corners, 44px height and horizontal 13px padding; focus changes the border/surface and adds a pale teal halo. Generic form fields use a separate parchment-filled, warm-bordered treatment. Admin form controls use a darker boundary for visibility and can vary from 40 to 46px by role. Keep labels adjacent to controls, preserve visible error text, and retain the admin focus outline as a scoped override.

### Navigation

Navigation is muted at rest and uses a pale teal surface with Deep Teal text for the selected destination. Section labels are small uppercase tracked sans-serif. The Scribe workspace becomes a mobile header and drawer; admin keeps its global navigation disclosure separate from the selected-team disclosure. Preserve each surface’s navigation model.

### Quota limit selector

The admin quota editor uses native grouped radio choices for **Set limit** and **Unlimited**. The selected segment fills with Deep Teal; changing the choice preserves the numeric draft, and finite zero remains distinct from Unlimited. Keep the fieldset semantics, responsive full-width segments on mobile and visible keyboard focus. This is an admin signature component, not a generic site segmented-control contract.

## Do's and Don'ts

### Do:
- **Do** use Paper, Parchment, Ink and Deep Teal as names for the existing shared values.
- **Do** use borders to define resting cards and reserve larger shadows for menus and dialogs.
- **Do** keep admin accessibility overrides scoped to admin until shared styles are separately reviewed.
- **Do** keep the Scribe mobile drawer, global Admin disclosure and selected-team mobile disclosure distinct.
- **Do** keep status labels textual and semantic, with color as supporting information.
- **Do** preserve visible focus and forced-colors behavior in the admin workspace.

### Don't:
- **Don't** turn admin-specific muted, border, status or focus values into global tokens by copying them into shared styles.
- **Don't** claim a named font is loaded; the styles declare fallback stacks without loading font assets.
- **Don't** add flashy dashboard styling; the confirmed visual language is calm, trustworthy and practical.
- **Don't** treat automated accessibility results as WCAG certification.
