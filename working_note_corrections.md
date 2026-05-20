## Working-note correction critique

Status: reviewed and applied where useful.

### 1. Template switch can save dirty Working note in wrong mode

Decision: keep.

Critique: correct blocker. `selectedWorkingNoteMode()` follows locked Working-note mode or current template mode. During template `change`, select value has already moved, so dirty unlocked content could be serialized through wrong editor mode.

Change: record rendered editor mode when note becomes dirty. Dirty Working-note saves now serialize with `dirtyNoteMode || currentRenderedNoteMode()`.

### 2. Structured Working note appears twice in structured prompts

Decision: keep, but narrow fix.

Critique: correct high-value debt. Working-note snapshot and generated-document structured context are two different prompt channels. Copying saved structured Working note into both over-weights clinician-authored context and diverges from freeform Working-note behavior.

Change: generated-document structured context is now populated only from legacy explicit `structured_context` request payloads. Saved Working notes are stored only in Working-note snapshot fields.

### 3. Empty new Working-note draft during generation

Decision: modify, not fully block.

Critique: brand-new empty unsaved draft can be safely discarded because no content exists. Saved Working note emptied in editor still blocks generation and asks user to clear first. Main debt was silent UX.

Change: empty unsaved Working-note draft discard now updates status with `Empty working-note draft ignored.` Regression hook added.

### Rejected changes

None. All suggestions were valid; third was reduced to explicit UX instead of stricter blocking to avoid needless friction for blank never-saved drafts.
