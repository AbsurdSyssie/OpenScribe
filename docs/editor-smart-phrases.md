# Editor Smart Phrases

Smart phrases are personal note-editor shortcuts. They are configuration, not transcript-derived content, and are visible only to the owning normal team user.

## Behavior

- Type `/TRIGGER` in a structured or freeform note line, then press `Enter` or `Tab` to insert the expansion.
- Matching is case-insensitive while typing; stored triggers are uppercase and do not include the leading slash.
- Each normal team user gets the starter `CESRF` phrase when the account is created or when the migration backfills existing users.
- Deleting a phrase is immediate. Deleted starter phrases are not recreated on login or list calls.
- Usage counters update only after a browser expansion calls the `used` endpoint.
- The trigger editor keeps a small visual gap between the `/` prefix and the trigger input.

## Editor Reordering

- Structured note lines have a drag handle and can move within or across sections.
- Freeform note lines can move within the freeform note.
- Keyboard reorder uses `Alt+ArrowUp` and `Alt+ArrowDown`; structured rows also support `Alt+ArrowLeft` and `Alt+ArrowRight` to move between sections.

## Privacy

Smart phrases must not contain transcript-derived text unless the owner deliberately stores it as their own personal configuration. They are never team-shared in this implementation, and generated documents remain owner-only transcript-derived content.
