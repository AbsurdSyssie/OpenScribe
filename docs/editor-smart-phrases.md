# Editor Smart Phrases

Smart phrases are personal note-editor shortcuts. They are configuration, not transcript-derived content, and are visible only to the owning normal team user.

## Behavior

- Type `/TRIGGER` in a structured or freeform note line, then press `Enter` or `Tab` to insert the expansion.
- When the Smart Phrase menu is open, its `Enter` shortcut expands the active phrase before note-line splitting runs.
- Matching is case-insensitive while typing; stored triggers are uppercase and do not include the leading slash.
- Pressing `Enter` in a note line splits it at the caret into two lines. A selected range is removed; the new line retains the original selection state and receives focus at its start. `Shift+Enter` inserts a newline within the line.
- Pressing `Backspace` at the start of a non-empty note line joins it to the preceding line and moves the caret to the join. Structured lines join only within their section; empty-line deletion keeps its existing behavior.
- Pressing `Delete` at the end of a note line joins the following line and moves the caret to the join. Structured lines join only within their section; an empty following line is removed.
- Each normal team user gets the starter `CESRF` phrase when the account is created or when the migration backfills existing users.
- Deleting a phrase is immediate. Deleted starter phrases are not recreated on login or list calls.
- Usage counters update only after a browser expansion calls the `used` endpoint.

## Editor Reordering

- Structured note lines have a drag handle and can move within or across sections.
- Freeform note lines can move within the freeform note.
- Keyboard reorder uses `Alt+ArrowUp` and `Alt+ArrowDown`; structured rows also support `Alt+ArrowLeft` and `Alt+ArrowRight` to move between sections.

## Privacy

Smart phrases must not contain transcript-derived text unless the owner deliberately stores it as their own personal configuration. They are never team-shared in this implementation, and generated documents remain owner-only transcript-derived content.
