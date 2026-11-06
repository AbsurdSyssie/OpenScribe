# Telehealth crash-recovery physical walkthrough

**Environment:** test-only consultation and synthetic audio. Do **not** use real patient
content. Current implementation is a gated browser-tab and experimental desktop-window
audio capture preview, **not approved for clinical use**. Enable TELEHEALTH_API_ENABLED=true only on an
isolated test deployment, then restart the web service as usual.

Use desktop Chrome or Edge with a browser-tab Teams or WhatsApp Web call and
a separate clinician microphone. A desktop application window (such as WhatsApp
Desktop) may be selected only if the browser returns an audio track. Its playback
may include other system audio; it is not yet validated as app-isolated capture.
A native desktop capture bridge is not implemented. Use headphones to reduce
microphone echo leakage. OpenScribe must be on HTTPS (or localhost) and the
browser must allow WebCrypto, IndexedDB, the Web Locks API, microphone and
browser-tab sharing. Recording fails closed if encrypted, cross-tab recovery
coordination is unavailable.

## 1. Baseline: two sources

1. Start a **new synthetic consultation**, select Recorded upload, enable the
   telephone control, and accept the first-use guidance.
2. Press Start. Choose the **call tab** in the browser picker and tick **Share
   tab audio**, or select an application window with audio sharing enabled.
   Window selection requires an extra acknowledgement warning that unrelated
   system audio might also be recorded. Grant clinician microphone permission.
3. Speak distinctly through the local mic and the remote browser tab. The two
   indicators should independently show source activity; one silent indicator
   must not turn green merely because the other is loud.
4. Record at least 25 seconds; press Stop. Confirm both sources upload and STT
   completes with Clinician/Patient turns and plausible timestamps. If stop and
   sealing succeed but transcription submission fails, use the separate
   **Retry transcription** action; it reuses the sealed capture and does not
   upload the audio again.
5. Check usage accounting: the same elapsed consultation time can generate two
   separately billed source durations. Check against your STT provider's own
   billing rules.

Use **Download diagnostics** during or after this capture. Inspect the JSON
and confirm `picker_selected`, `microphone_ready`, `worklet_ready`,
`capture_started`, independent `vad_ready`/speech events, upload/stop/seal
states and any fixed failure codes. No diagnostic event may contain audio,
transcript text, a window title, request URL, credentials or raw error text.
The file is only created on explicit click; logs are memory-only and are lost
on reload, not sent to the server or written to the browser console.

For the experimental window path, test a synthetic WhatsApp Desktop call in a
supported Windows or macOS browser. Confirm the microphone and remote audio
separately, then create an unrelated synthetic OS sound to establish whether it
is also captured. If window audio cannot be isolated, do not approve this
platform for clinical use. Verify rejection of whole-screen selection, missing
window audio, declined window acknowledgement and tab audio unchecked.

**Pass:** no accidental audio playback/feedback; no unlabelled source merge;
recording becomes ready only after both channels finish; existing mic-only
recording works when the phone toggle is off.

## 2. Crash with encrypted audio waiting to upload

1. Begin another **synthetic** call. Let both sources work for 10–15 seconds.
2. In browser DevTools → Network, temporarily select **Offline** (or block the
   /audio-parts POST endpoint). Keep recording for at least one 10-second
   capture window so a WAV window is queued but cannot reach the server.
3. In DevTools → Application → IndexedDB inspect
   openscribe-telehealth-recovery-v1 → pending_windows. There should be
   entries containing ciphertext/IV and source metadata, **not raw WAV bytes**.
   Do not copy any browser-profile audio/key data into bug reports.
4. Abruptly close/reload the **OpenScribe tab**, explicitly accepting the
   browser's unsaved-recording warning if it appears. Restore network access,
   reopen the **same browser profile** and sign in as the **same owner**.
5. Reopen the same consultation. The receipt should show an unresolved capture
   even if **Show Telehealth button** was previously disabled in Settings.
6. Click **Recover audio and transcribe**. The UI should stop/mark the capture
   incomplete, replay saved windows using original channel/sequence IDs,
   re-check the server receipt, seal only after verifying accepted counts,
   request explicit clinician acknowledgement, then submit both recoverable
   sources for STT.

**Pass:** no automatic declaration of a complete capture; no duplicate
clinician/patient windows, even if an earlier server reply was lost. The
transcript carries an **INCOMPLETE TELEHEALTH RECORDING** warning before
redaction/note generation. Review for missing final speech.

**Expected limit:** the last AudioWorklet frames that had not yet become a
committed IndexedDB WAV window are **not** recoverable. A browser/OS crash may
also prevent the final storage transaction from committing.

## 3. Failure and discard behavior

1. Repeat with the network blocked, but choose **Discard unsubmitted audio**
   after opening the unresolved consultation. Confirm.
2. Confirm the app returns the consultation to a resolvable state, deletes
   matching locally stored ciphertext, and records the server session as
   discarded. Already submitted STT jobs must **not** be deletable through
   this action.
3. Start another recording. It should work once the unresolved capture is
   discarded, without carrying over the old phone-button state.
4. With a clean test profile, deny IndexedDB/WebCrypto/Web Locks (or use a
   browser mode that cannot persist non-extractable CryptoKeys). Recording
   should refuse to start without encrypted, cross-tab-coordinated storage.

**Pass:** there is no indefinite spinning Stop button, false completed
receipt, unexpected source duplication or unexpected release of a blocked
clinical note.

## 4. Privacy and resilience observations

- Inspect pending_windows: encrypted ciphertext only, with at most **eight**
  pending windows globally on this origin. Confirm successfully acknowledged
  windows are removed; empty captures also lose their local encryption key.
- Pending local windows expire **six hours after creation when the browser
  next opens the recovery store**. Expiry is not an automatic offline timer:
  encrypted material may remain on a dormant browser profile.
- If a browser capture track reports `muted`, Telehealth stops and marks that
  source interrupted, even if it later becomes live again. Verify that the
  incomplete warning remains and clinician acknowledgement is required before
  transcription.
- Test network loss, unexpected call-tab close, mic permission revocation,
  laptop sleep/resume, low storage and browser process termination.
- Test with a second account on the same device; it must not be able to fetch
  the first account's capture receipt or replay its server audio.
- Never collect console logs, HAR files, browser-profile exports or screenshots
  that include actual patient data. Report only synthetic test steps, browser
  version, OS, feature flags, source statuses and sanitized error codes.

## Reporting template

For each scenario record: OS + version; Chrome/Edge version; Teams Web or
WhatsApp Web; site URL scheme (HTTPS/localhost); capture duration; channel
activity; number of server-accepted windows per channel; number of locally
replayed windows; capture state before/after reload; incomplete warning present;
STT completion state; and any anonymized console/network error codes.

**Release gate:** Passing this walkthrough does **not** replace full
concurrent-worker/retention tests, provider billing checks, privacy assessment,
or a documented clinical-safety review.
