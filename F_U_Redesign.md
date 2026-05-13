## Full plan: Follow Ups tab redesign

Scope: redesign the **Follow Ups** tab in the transcribe workspace to match the screenshot, but with the adjusted hierarchy: quick actions are the main flow; free text is a small secondary flow.

Relevant files:

* `app/templates/transcribe/_workspace.html` — current workspace markup and Follow Ups tab structure. 
* `app/templates/transcribe/_head_assets.html` — transcribe workspace CSS. 
* `app/static/js/transcribe/app.js` — main transcribe workspace JS, including quick-action, follow-up, dictation, and selected-document state. 
* `app/static/js/transcribe/documents.js` — document navigation/rendering helpers for generated documents. 

---

# 1. Product goals

## Primary goal

Make the Follow Ups tab feel like a dedicated follow-up generation workspace:

```text
Pick quick action → add context → generate → review/copy selected output
```

## Secondary goal

Keep free-text generation available, but visually demote it:

```text
Anything else? Use free text to generate anything about this note.
```

It should not compete with the quick-action workflow.

## Non-goals for first pass

* No DB migration for quick-action usage counts unless the schema already supports it.
* No backend rewrite unless existing endpoints cannot support the new layout.
* No global recent follow-up history.
* No multi-output stacked history in the right panel.

---

# 2. Final layout

The Follow Ups tab becomes a three-column workspace.

```text
┌──────────────────────┬────────────────────────────────┬──────────────────────────────┐
│ Quick Actions         │ Context & Request              │ Generated Follow Up           │
│                       │                                │                              │
│ Search quick actions  │ Add context to quick action    │ Selected generated follow-up  │
│ All quick actions     │ Large context textarea         │ Copy / LLM request / delete   │
│                       │                                │ Regenerate / rating           │
│ Recent for transcript │ Generate + Clear               │                              │
│                       │                                │                              │
│                       │ Anything else?                 │                              │
│                       │ Small free-text field + button │                              │
└──────────────────────┴────────────────────────────────┴──────────────────────────────┘
```

Recommended width split:

```text
Left:   22%
Middle: 38%
Right:  40%
```

Or CSS:

```css
grid-template-columns: minmax(17rem, 0.85fr) minmax(28rem, 1.45fr) minmax(30rem, 1.6fr);
```

For narrower screens:

* Below around `1100px`: stack middle and right or allow horizontal scroll.
* Below tablet width: single-column vertical layout.

---

# 3. Left panel: Quick Actions

## Content

Header:

```text
QUICK ACTIONS
```

Search:

```text
Search quick actions...
```

Quick-action list:

Each item should render as a card:

```text
[icon]  2wwCRC
        2WW Colorectal Referral
        Popular                  [selected check]
```

Fields:

* icon determined by quick-action name/category
* title = quick action name
* description = quick action description
* selected checkmark only on current selected action
* optional `Popular`/`Most used` badge only if backed by data

## Ordering

Use all quick actions.

Ordering logic:

1. If DB/schema already has usage data:

   * most used first
   * then recently used
   * then alphabetical
2. If usage data does not exist:

   * default quick action first, if present in user preferences
   * favorite quick actions next, if present
   * then alphabetical

Do not add a DB migration in this pass just to support “most used”.

## Recent section

Header:

```text
RECENT
```

Only show follow-ups generated for the **current transcript**.

Each recent item:

```text
Referral letter        2 days ago   >
Patient follow-up      1 week ago   >
2wwCRC                 Just now     >
```

Clicking a recent item sets that follow-up as the selected output in the right panel.

No global history.

---

# 4. Middle panel: Context & Request

This is the main generation panel.

## Header

```text
CONTEXT & REQUEST
```

Right-side action:

```text
Record context
```

This uses the existing dictation functionality and targets the large context textarea.

## Primary quick-action context

Title:

```text
Add context to the quick action
```

Helper:

```text
Include key clinical details that should be reflected in the output.
```

Textarea:

* large
* approximately 160–220px high
* max length 4000
* character counter, e.g. `112 / 4000`

This maps to the existing quick-action context input:

```html
data-quick-action-context-input
```

## Primary action row

Below the large context textarea:

```text
[ Generate (Enter) ]   [ Clear ]
```

Behavior:

* `Generate (Enter)` runs the selected quick action.
* `Clear` clears the context textarea and any secondary free-text field.
* If no quick action is selected, Generate is disabled or prompts the user to select one.

## Secondary free-text section

This sits **under** the main Generate/Clear row.

Visual treatment:

* smaller
* quieter
* separated by a thin divider or extra spacing
* no large `OR` divider

Copy:

```text
Anything else?
Use free text to generate anything about this note.
```

Input:

* small textarea or input-height textarea
* 1–2 lines tall
* max length probably 1000
* placeholder:

```text
Describe what you need...
```

Inline button:

```text
[ Generate ]
```

Behavior:

* this button ignores selected quick action and generates a custom follow-up from the free-text prompt
* pressing Enter in this field can generate
* Shift+Enter inserts a newline if it is a textarea

This maps to the existing custom prompt input:

```html
data-followup-prompt-input
data-generate-followup-trigger
```

---

# 5. Dictation plan

Current dictation should keep working for the context field.

Extend it so dictation can target either:

1. quick-action context
2. secondary “Anything else?” free-text prompt

## UI

Primary:

```text
Record context
```

Secondary:

```text
Record description
```

or a small mic icon inside/next to the compact free-text field.

## JS design

Introduce a target variable:

```js
let dictationTargetField = 'context'; // 'context' | 'customPrompt'
```

When opening dictation:

```js
openDictationForField('context')
openDictationForField('customPrompt')
```

When dictation text is saved or inserted:

```js
if (dictationTargetField === 'context') {
  appendOrReplace(quickActionContextInput, dictationText);
}

if (dictationTargetField === 'customPrompt') {
  appendOrReplace(generateFollowupPromptInput, dictationText);
}
```

Default insertion should probably be append-with-spacing rather than replace, unless the field is empty.

Suggested helper:

```js
const appendTextToField = (field, text) => {
  const existing = field.value.trim();
  field.value = existing ? `${existing}\n${text.trim()}` : text.trim();
  field.dispatchEvent(new Event('input', { bubbles: true }));
};
```

Reuse the current dictation modal/controller rather than creating a second one.

---

# 6. Right panel: Generated Follow Up

The right panel shows only the **selected follow-up**.

## Header

```text
GENERATED FOLLOW UP
```

Toolbar:

```text
Copy    LLM request    Delete
```

## Output card

Card structure:

```text
READY    2025-05-13T12:09:08...

**Referral for Suspected Lower GI Cancer**

I am referring this 55-year-old patient...
```

Status states:

* queued
* processing
* ready
* failed

Ready state should show the generated text.

Processing state:

```text
Your follow-up is being written.
```

Queued state:

```text
Your follow-up is waiting to be written.
```

Failed state:

```text
The latest follow-up could not be created: [error]
```

Empty state:

```text
Select a quick action and generate a follow-up.
```

## Footer

```text
[ Regenerate ]                         Rate this output  [thumbs up] [thumbs down]
```

First pass:

* `Regenerate` can rerun the same selected quick action/custom request if enough metadata exists.
* If not enough metadata exists, regenerate can be hidden or disabled.
* Rating buttons can be visual-only unless rating persistence already exists.

---

# 7. Data behavior

## Quick-action generation

When a quick action is selected:

* hidden/select value updates
* context textarea provides extra details
* Generate runs the selected quick action using the existing quick-action endpoint/action

Existing hooks to preserve:

```html
data-quick-action-select
data-quick-action-context-input
data-run-quick-action-trigger
```

## Custom free-text generation

When secondary free-text field is used:

* prompt text is sent as custom follow-up request
* selected quick action should not be required
* selected quick action should not influence the request unless current backend already does that

Existing hooks to preserve:

```html
data-followup-prompt-input
data-generate-followup-trigger
```

## Selected follow-up

State model:

```js
selectedFollowupDocumentId
workspaceFollowupDocuments
```

Behavior:

* after generating a new follow-up, select the new document
* clicking a Recent item selects that document
* right panel renders only that selected document
* do not render all follow-up documents as stacked cards

---

# 8. Template changes

File:

```text
app/templates/transcribe/_workspace.html
```

Replace the current Follow Ups tab internals with a new structure, but keep existing data hooks.

Recommended skeleton:

```html
<div class="main-panel h-full" data-tab-panel="followups" hidden>
  <div class="followup-workspace-v2">

    <aside class="followup-panel-v2 followup-sidebar-v2">
      <div class="followup-panel-v2__heading">QUICK ACTIONS</div>

      <div class="followup-search-v2">
        <input
          type="search"
          placeholder="Search quick actions..."
          data-quick-action-search>
      </div>

      <select
        name="quick_action_id"
        class="sr-only"
        data-quick-action-select>
        ...
      </select>

      <div class="followup-action-list-v2" data-quick-action-card-list>
        ...
      </div>

      <div class="followup-recent-v2">
        <div class="followup-panel-v2__heading">RECENT</div>
        <div data-followup-recent-list>
          ...
        </div>
      </div>
    </aside>

    <section class="followup-panel-v2 followup-request-v2">
      <div class="followup-panel-v2__heading-row">
        <div class="followup-panel-v2__heading">CONTEXT & REQUEST</div>
        <button type="button" data-record-context>Record context</button>
      </div>

      <label>
        <span>Add context to the quick action</span>
        <small>Include key clinical details that should be reflected in the output.</small>
        <textarea
          maxlength="4000"
          data-quick-action-context-input></textarea>
      </label>

      <div data-context-char-count>0 / 4000</div>

      <div class="followup-primary-actions-v2">
        <button type="button" data-run-quick-action-trigger>Generate (Enter)</button>
        <button type="button" data-followup-clear>Clear</button>
      </div>

      <div class="followup-custom-request-v2">
        <div>Anything else?</div>
        <p>Use free text to generate anything about this note.</p>

        <div class="followup-custom-request-v2__row">
          <textarea
            maxlength="1000"
            placeholder="Describe what you need..."
            data-followup-prompt-input></textarea>

          <button type="button" data-generate-followup-trigger>Generate</button>
        </div>
      </div>
    </section>

    <section class="followup-panel-v2 followup-output-v2">
      <div class="followup-panel-v2__heading-row">
        <div class="followup-panel-v2__heading">GENERATED FOLLOW UP</div>
        <div class="followup-output-actions-v2">
          <button type="button" data-copy-latest-followup>Copy</button>
          <button type="button" data-followup-llm-request-toggle>LLM request</button>
          <button type="button" data-followup-delete>Delete</button>
        </div>
      </div>

      <div
        data-latest-followup-output
        data-latest-followup-id="...">
        ...
      </div>

      <div data-followup-llm-request-slot></div>
      <div data-followup-redaction-debug-slot></div>
    </section>

  </div>
</div>
```

The exact Jinja loops should reuse:

* `available_quick_actions`
* `followup_documents`
* `latest_followup_document`

---

# 9. CSS changes

File:

```text
app/templates/transcribe/_head_assets.html
```

Add a new CSS block for v2 classes. Keep existing classes until fully migrated.

Core classes:

```css
.followup-workspace-v2 {
  display: grid;
  grid-template-columns: minmax(17rem, 0.85fr) minmax(28rem, 1.45fr) minmax(30rem, 1.6fr);
  gap: 1rem;
  height: 100%;
  padding: 1rem;
  background: var(--bg);
  overflow: hidden;
}

.followup-panel-v2 {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: var(--card);
  overflow: hidden;
}

.followup-panel-v2__heading {
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  color: #54678a;
  text-transform: uppercase;
}

.followup-sidebar-v2,
.followup-request-v2,
.followup-output-v2 {
  padding: 1rem;
}
```

Quick-action cards:

```css
.followup-action-card-v2 {
  display: grid;
  grid-template-columns: 2.2rem minmax(0, 1fr) auto;
  gap: 0.75rem;
  width: 100%;
  padding: 0.85rem;
  border: 1px solid rgba(226, 222, 214, 0.95);
  border-radius: 0.65rem;
  background: white;
  text-align: left;
}

.followup-action-card-v2.is-selected {
  border-color: rgba(29, 79, 94, 0.65);
  background: rgba(224, 242, 245, 0.35);
  box-shadow: inset 0 0 0 1px rgba(29, 79, 94, 0.12);
}
```

Middle panel:

```css
.followup-context-textarea-v2 {
  min-height: 12rem;
  resize: vertical;
}

.followup-custom-request-v2 {
  margin-top: 1.1rem;
  padding-top: 1rem;
  border-top: 1px solid rgba(226, 222, 214, 0.8);
}

.followup-custom-request-v2__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.65rem;
  align-items: end;
}

.followup-custom-request-v2 textarea {
  min-height: 2.75rem;
  max-height: 5.5rem;
}
```

Right panel:

```css
.followup-output-card-v2 {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: white;
  padding: 1rem;
}
```

Responsive:

```css
@media (max-width: 1180px) {
  .followup-workspace-v2 {
    grid-template-columns: minmax(16rem, 0.8fr) minmax(24rem, 1.2fr);
  }

  .followup-output-v2 {
    grid-column: 1 / -1;
  }
}

@media (max-width: 820px) {
  .followup-workspace-v2 {
    grid-template-columns: 1fr;
    overflow-y: auto;
  }
}
```

---

# 10. JS changes

File:

```text
app/static/js/transcribe/app.js
```

## Add DOM refs

Add references:

```js
const quickActionSearchInput = document.querySelector('[data-quick-action-search]');
const quickActionCards = [...document.querySelectorAll('[data-quick-action-card]')];
const followupClearButton = document.querySelector('[data-followup-clear]');
const contextCharCount = document.querySelector('[data-context-char-count]');
const customPromptCharCount = document.querySelector('[data-custom-prompt-char-count]');
const recordContextButton = document.querySelector('[data-record-context]');
const recordCustomPromptButton = document.querySelector('[data-record-custom-prompt]');
const followupRecentItems = [...document.querySelectorAll('[data-followup-recent-item]')];
```

## Quick-action card selection

Behavior:

```js
quickActionCards.forEach((card) => {
  card.addEventListener('click', () => {
    const id = card.dataset.quickActionId || '';
    runQuickActionSelect.value = id;
    syncQuickActionCardSelection(id);
  });
});
```

```js
const syncQuickActionCardSelection = (selectedId) => {
  quickActionCards.forEach((card) => {
    const selected = card.dataset.quickActionId === selectedId;
    card.classList.toggle('is-selected', selected);
    card.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
};
```

## Search filtering

```js
quickActionSearchInput?.addEventListener('input', () => {
  const query = quickActionSearchInput.value.trim().toLowerCase();

  quickActionCards.forEach((card) => {
    const haystack = [
      card.dataset.quickActionName,
      card.dataset.quickActionDescription,
    ].join(' ').toLowerCase();

    card.hidden = query && !haystack.includes(query);
  });
});
```

## Character counters

```js
const bindCharacterCounter = (input, counter, max) => {
  const update = () => {
    if (counter) counter.textContent = `${input.value.length} / ${max}`;
  };

  input.addEventListener('input', update);
  update();
};
```

Bind:

```js
bindCharacterCounter(quickActionContextInput, contextCharCount, 4000);
bindCharacterCounter(generateFollowupPromptInput, customPromptCharCount, 1000);
```

## Clear button

```js
followupClearButton?.addEventListener('click', () => {
  if (quickActionContextInput) quickActionContextInput.value = '';
  if (generateFollowupPromptInput) generateFollowupPromptInput.value = '';

  quickActionContextInput?.dispatchEvent(new Event('input', { bubbles: true }));
  generateFollowupPromptInput?.dispatchEvent(new Event('input', { bubbles: true }));
});
```

## Enter behavior

Context textarea:

* Enter should probably generate only when user is not trying to add a newline.
* Safer option: `Cmd/Ctrl+Enter` generates.
* If you want screenshot behavior `Generate (Enter)`, then:

```js
quickActionContextInput?.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey) return;
  event.preventDefault();
  runQuickActionTrigger?.click();
});
```

Custom prompt:

```js
generateFollowupPromptInput?.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey) return;
  event.preventDefault();
  generateFollowupTrigger?.click();
});
```

## Dictation targeting

Add:

```js
let dictationTargetField = 'context';
```

Buttons:

```js
recordContextButton?.addEventListener('click', () => {
  dictationTargetField = 'context';
  openDictationModal();
});

recordCustomPromptButton?.addEventListener('click', () => {
  dictationTargetField = 'customPrompt';
  openDictationModal();
});
```

When dictation output is ready:

```js
const targetInput = dictationTargetField === 'customPrompt'
  ? generateFollowupPromptInput
  : quickActionContextInput;

appendTextToField(targetInput, dictationText);
```

Need to wire this into the existing dictation completion/save path, not create a duplicate recorder.

## Recent item selection

```js
followupRecentItems.forEach((item) => {
  item.addEventListener('click', () => {
    selectedFollowupDocumentId = item.dataset.documentId || null;
    renderSelectedFollowup();
  });
});
```

Use existing document rendering helpers where possible.

---

# 11. Backend/data check

Before coding sorting by “most used”, check whether `QuickAction` or generated documents already support usage metadata.

Potential data sources:

* `QuickAction` has `times_used` / `last_used_at`, if present.
* `GeneratedDocument` may reference quick action IDs or generator metadata.
* Existing user preferences may have `favorite_quick_action_ids` and `default_quick_action_id`.

If no usage field exists:

* skip “most used”
* do not fake a `Popular` badge
* use preference + alphabetical sorting

If usage exists:

* expose usage metadata in the transcribe workspace context
* sort server-side before rendering
* optionally label the top one or top few as `Popular` or `Most used`

---

# 12. Testing plan

## Manual UI checks

1. Open a transcript with available quick actions.
2. Follow Ups tab shows three-column layout.
3. All quick actions appear in left panel.
4. Search filters quick actions.
5. Clicking a quick-action card selects it.
6. Context textarea counter updates.
7. Primary Generate runs selected quick action.
8. Clear empties context and free-text fields.
9. “Anything else?” Generate runs custom prompt.
10. Recent shows only current transcript follow-ups.
11. Clicking Recent updates the right panel.
12. Right panel shows only selected follow-up.
13. Copy works.
14. Delete works if existing delete functionality supports follow-ups.
15. LLM request panel still opens in the right output area.
16. Dictation inserts into context.
17. Dictation inserts into custom prompt.
18. Empty states look correct.
19. Processing/queued/failed states render correctly.
20. Responsive layout does not break below desktop width.

## Regression checks

* Clinical Note tab still works.
* Transcript tab still works.
* Recording/upload controls still work.
* Existing follow-up endpoints still work.
* Generated document polling/refresh still updates selected output.
* No JS errors when no quick actions exist.
* No JS errors when no follow-ups exist.
* No JS errors when LLM provider unavailable.

## Automated tests

If there are existing UI tests, add coverage for:

* quick-action cards render
* quick-action search filters list
* selected card syncs with hidden select
* custom prompt generation still submits
* recent list contains only current transcript documents
* right panel renders one selected follow-up

---

# 13. Implementation sequence

## Phase 1 — Markup-only structure

* Replace Follow Ups tab body in `_workspace.html`.
* Preserve all existing functional `data-*` selectors.
* Render quick-action cards from `available_quick_actions`.
* Render recent from `followup_documents`.
* Render selected/latest follow-up in right panel.

Outcome: new structure visible, old behavior mostly preserved.

## Phase 2 — CSS

* Add v2 layout classes to `_head_assets.html`.
* Match screenshot styling:

  * white panels
  * soft borders
  * uppercase labels
  * rounded cards
  * teal selected states
  * compact secondary custom request section

Outcome: page looks like the intended redesign.

## Phase 3 — JS interaction

* Card selection.
* Search filtering.
* Character counters.
* Clear button.
* Enter-to-generate.
* Recent selection.
* Selected-follow-up render sync.

Outcome: new layout behaves properly.

## Phase 4 — Dictation targeting

* Refactor existing dictation insertion to support target fields.
* Add `Record context`.
* Add `Record description` or mic button for secondary field.
* Ensure dictation output appends to the right textarea.

Outcome: dictation supports both fields.

## Phase 5 — Sorting / usage polish

* Check schema.
* If usage data exists, sort by most used.
* If not, use default/favorites/alphabetical.
* Only show `Popular`/`Most used` badges when data supports them.

Outcome: quick-action order is sensible without unnecessary schema work.

## Phase 6 — Test and clean up

* Remove unused old follow-up CSS only after confirming no regressions.
* Keep legacy classes temporarily if JS or tests still reference them.
* Add/update tests.

---

# 14. Acceptance criteria

The redesign is complete when:

* The Follow Ups tab matches the three-panel layout.
* Quick actions are the primary workflow.
* Free-text generation is present but small and secondary.
* All quick actions are shown.
* Most-used sorting is used only if backed by existing data.
* Recent contains only current transcript follow-ups.
* The right panel shows only the selected follow-up.
* Dictation can target both context and free-text request.
* Existing generation, copy, LLM request, delete, and refresh flows still work.
* No backend migration is required for the first version unless the schema already supports the requested ordering.
