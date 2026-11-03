---
target: "Saved-provider editor screenshots: large catalogs, model policy and Connection"
total_score: 17
max_score: 36
na_heuristics: 9
p0_count: 0
p1_count: 3
target_identity: "file:/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html"
target_fingerprint: "sha256:6a46887bc54197a316116cd2923b7baade9de4ba384a74a15a03458e540ced9e"
target_path: /home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin_mockup.html
timestamp: 2026-10-29T17-22-21Z
slug: app-templates-admin-mockup-html
---
# Saved provider editor: screenshot critique and mockups

Method: dual-agent (A: /root/provider_visual_critique_a; B: /root/provider_visual_critique_b).
Date: 29 October 2026.
Target: app/templates/admin_mockup.html, saved-provider Connection and Models.
Scope: supplied screenshots plus current source, not a live browser/runtime audit. Proposed images are illustrative; no product code, settings, credentials or model policy changed.

The Connection/Models split is the correct task structure, but the current Models surface turns a large provider catalog into a long form. It needs to help an administrator find, curate and review models.

Strengths:
- Existing settings are editable in a familiar modal with clear Connection and Models categories.
- Endpoint and authentication controls are grouped; the non-selected provider has an explicit switch action.
- Serif headings, quiet warm surfaces and teal actions fit the incumbent product.

Priority issues:
1. [P1] Scale: 93 checkboxes with no search or enabled-only view. Add search, selected/total counts and an enabled-only filter. Do not infer compatibility from a model name or invent metadata.
2. [P1] Save and Cancel scroll out of sight because the entire modal scrolls. Keep the header and footer visible; constrain the model list. Preserve keyboard access and short-screen support.
3. [P1] Configuration vs team assignment: the inactive provider's team-wide switch is explained in small copy beneath the list and the primary action is detached from Cancel. Put the consequence near the top and an explicit Use provider & save action in the footer. Do not introduce dormant-provider allowlists: current team model policy belongs to the selected provider.
4. [P2] Default is separated from availability. The proposed table puts enabled checkboxes and a Default radio in each model row, making the relationship visible. The default must remain enabled; zero selections cannot save. Preserve all actual saved choices on opening.
5. [P2] Conditional authentication leaves an orphan Bearer token label in the No authentication state. Hide/show the entire field, not only its input. Group authentication choices and remove task-oriented token instructions where shorter state-specific help suffices.

Minor observations:
- Active tabs need one consistent treatment and comfortable targets.
- Form groups need stronger spacing; Connection's availability checkbox is too close to Endpoint.
- Provider/model IDs remain literal identifiers; no made-up friendly capability labels.
- Avoid claiming a connection is healthy without a real result.

Persona checks:
- An administrator maintaining a large catalog cannot quickly find/review enabled choices or keep Save in view.
- An administrator switching providers must understand the team-wide consequence before applying model choices.
- A keyboard/short-screen user needs bounded list scrolling without losing dialog navigation and actions; screenshot evidence does not establish keyboard behavior.

Visual heuristic assessment (0–4; runtime recovery is not assessable from screenshots):
| Heuristic | Score | Evidence |
| --- | --- | --- |
| Status | 2 | Catalog count present, selected count absent |
| User language | 3 | Clear categories, some task-oriented auth help |
| Control | 2 | Cancel exists but moves below long catalog |
| Consistency | 2 | Action placement and tab treatment differ |
| Error prevention | 2 | Provider switch explicit but low in hierarchy |
| Recognition | 1 | Finding/reviewing choices in 93-row list |
| Efficiency | 1 | No search, filter or bulk review aid |
| Minimalist design | 2 | Plain form, excessive undifferentiated rows |
| Error recovery | n/a | Runtime errors not visible in supplied captures |
| Help | 2 | Some useful explanation, conditional auth label contradicts state |
| Total | 17/36 | Static visual assessment, not accessibility certification |

Detector: 84 findings across the whole admin template (32 warnings,52 advisories). The detector did not identify these modal-specific scale/state issues. Many color/type advisories concern intentional admin overrides or other surfaces; do not treat 84 as modal defects.
Source confirms whole-modal overflow at .provider-details-modal, with header/body/footer in the same scroll container; optional-auth sync hides only the input while addDetailsField wraps its label.

Mockups:
- Models: searchable catalog; All/Enabled filters; six illustrative checked models; in-row default; bounded list; footer with selection summary and Save models.
- Connection: name, endpoint, conditional Authentication group, availability; right-hand alternative fragment makes provider switching explicit.
- Image generation used the built-in image tool. Preview assets remain under the generated-images directory, not referenced by product code.
- Model names and selected counts are examples, not recommendations, automatic migration or capability claims.

Questions to resolve after viewing:
- Default selection: inline radio in each model row, or a separate searchable default picker?
- Initial filter: Enabled first, or All models first?
