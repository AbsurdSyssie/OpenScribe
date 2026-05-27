# User Tutorial

## What OpenScribe Is

OpenScribe helps turn a consultation into draft clinical writing.

It does three main things:

1. Records or receives consultation audio.
2. Turns that audio into a draft transcript.
3. Uses the transcript to help draft notes, follow-ups, or other text.

OpenScribe does not replace clinical judgement. It is a drafting tool. You must read, check, edit, and approve anything before it goes into the clinical record or goes to a patient.

## Who This Is For

This tutorial is for normal users and team leaders using OpenScribe as clinicians.

If you are a team leader, read this page first. The team leader tutorial only explains the extra team-management tasks.

## Important Words

`Consultation` means one patient encounter or one work session.

`Transcript` means the text OpenScribe creates from audio.

`Template` means the set of instructions OpenScribe uses when drafting a note.

`Generated note` means draft text created by OpenScribe.

`Quick action` means a saved task, such as drafting a referral summary or follow-up message.

`EPR` means the external electronic patient record where you paste final reviewed text.

## Before You Use It With Real Patients

Check these things first:

1. You can sign in.
2. You can complete the authenticator code step if asked.
3. Your team has a speech service available.
4. Your team has a writing assistant available.
5. You know your local rule for using AI draft notes.
6. You have tried the workflow with test or training material.

If one of these is missing, stop and ask your team leader or system admin before using OpenScribe clinically.

## Basic Workflow

Every normal session follows this shape:

1. Sign in.
2. Open the consultation workspace.
3. Start a new consultation or select the correct existing one.
4. Record or upload audio.
5. Wait for the transcript.
6. Generate a draft note.
7. Read and edit the note.
8. Copy reviewed text into the EPR.
9. Check the EPR before saving.

Do not skip the review steps.

## Step 1: Sign In

Go to the OpenScribe sign-in page.

Enter your email and password. If OpenScribe asks for an authenticator code, open your authenticator app and enter the current code.

If sign-in fails, do not create a new account yourself. Ask your team leader or system admin for help.

## Step 2: Open the Consultation Workspace

After sign-in, open the consultation workspace. This is where recording, transcripts, notes, follow-ups, and quick actions live.

If you land on Home first, use the consultation or transcribe link to enter the workspace.

## Step 3: Create or Select the Right Consultation

Before recording or uploading, make sure you are in the correct consultation.

Use a new consultation for a new patient encounter. Use an existing consultation only when you are continuing the same work session.

Check the title or session details before adding audio. If you are in the wrong consultation, switch or create the correct one first.

## Step 4: Record or Upload Audio

Use live recording when you are capturing a consultation through the browser microphone.

Use upload when you already have an approved audio file for this consultation.

Before starting:

1. Confirm you are in the right consultation.
2. Confirm the microphone or upload file is correct.
3. Confirm recording is allowed under local policy.
4. Start recording or upload the file.

If you accidentally record or upload under the wrong consultation, stop. Do not continue adding more content to that consultation.

## Step 5: Wait for the Transcript

OpenScribe turns audio into transcript text. This may take time.

The transcript is not perfect. It is draft source material. It can mishear words, miss words, or misunderstand clinical terms.

Check especially:

- names
- dates
- medication names
- doses
- allergies
- diagnoses
- negatives, such as "no chest pain"
- safety-netting advice
- follow-up plans

If the transcript is clearly wrong, be extra careful when using it to generate a note.

## Step 6: Choose a Template

A template tells OpenScribe what kind of note to draft.

Examples:

- structured EMIS note
- freeform consultation summary
- referral-style summary

Choose the template that matches the document you want. If you choose the wrong template, the output may be in the wrong format or may emphasise the wrong things.

Use `Note options` beside `Create` to choose the writing model, approximate maximum length, and detail level for future generated notes. These settings save immediately. If saving fails, OpenScribe warns you; you can still create the note, but it may use the previous saved settings.

The same model, length, and detail preferences are available on Home in the `Your writing assistant preference` card.

## Step 7: Generate the Draft Note

After audio and transcript are available:

1. Select the intended template.
2. Press the generate action.
3. Wait for the note to finish.
4. Open the generated note.

The generated note is a draft. It may be incomplete, wrong, too confident, or include irrelevant text.

## Step 8: Review and Edit the Note

Read the whole note before copying anything.

Check:

- Does this belong to the right patient and consultation?
- Are key symptoms correct?
- Are important negatives included correctly?
- Are medicines and doses correct?
- Are allergies correct?
- Are examination findings correct?
- Are investigations and tasks correct?
- Is the plan clear?
- Is anything invented or over-stated?
- Is anything important missing?

Edit the note inside OpenScribe until it matches your clinical judgement.

## Structured Notes

Some templates create a structured note. Structured notes are split into sections.

Allowed EMIS section keys are:

- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

Empty sections may be missing. That is normal.

Do not put uncertain information into a section just to fill it. If OpenScribe puts something in the wrong section, move it, edit it, or remove it before copying.

## Freeform Notes

Some templates create normal text without EMIS sections.

Read the full text before copying. Make sure it is suitable for the place you plan to paste it.

## Follow-Ups and Quick Actions

Follow-ups and quick actions create more draft text from the consultation.

Use them for tasks such as:

- draft follow-up instructions
- draft referral wording
- draft admin summaries
- draft patient-facing text when local policy allows it

Always review the result. Patient-facing text needs special care because wrong advice can cause harm.

Check for:

- incorrect advice
- missing safety-netting
- wording that sounds too certain
- local policy requirements
- text that should not be sent automatically

## Copying Text Into the EPR

Copying is the moment where draft text may become part of the clinical record. Slow down here.

Before copying:

1. Read the text fully.
2. Confirm it belongs to the current patient.
3. Confirm it belongs to the current consultation.
4. Confirm you have edited errors.
5. Copy the right section into the right EPR field.
6. Read the EPR entry after pasting.
7. Save only when the EPR text is correct.

For structured notes, section copy buttons can help you copy one section at a time. They do not guarantee you pasted into the right EPR field. You still need to check.

## Deleting a Consultation

Deleting a consultation is serious.

In the MVP, deletion is immediate after confirmation. There is no undo period.

Deleting the consultation deletes the transcript root and transcript-derived children, including generated documents linked to it.

Only delete when you are sure local policy allows it and the content is no longer needed.

## Privacy

Your transcript-derived content is private to you as the owning user.

Team leaders and system admins can manage accounts, providers, templates, and metadata. They do not get to read your transcript or generated note content just because of their role.

If you think another person can see content they should not see, stop using the system and report it.

## Common Problems

### I cannot sign in

Ask your team leader or system admin. You may need account recovery or a new setup link.

### Recording is unavailable

Check that your browser has microphone permission. If the team speech service is missing, ask your team leader.

### Upload is unavailable

Check the file type and size. If the team speech service is missing, ask your team leader.

### The transcript looks wrong

Do not trust generated notes blindly. Review the transcript and edit the note carefully.

### Note generation fails

Try again once. If it repeatedly fails, ask your team leader or system admin and include the consultation/session metadata, not patient content.

### The provider or model name looks wrong

Stop and ask your team leader. Provider/model changes can affect transcript or note quality.

## When to Ask for Help

Ask for help when:

- sign-in or MFA does not work
- speech or writing services are unavailable
- provider/model names look wrong
- note generation repeatedly fails
- you think content is visible to the wrong person
- you need account recovery
- you are unsure whether deletion is appropriate
