# Local demo

The local demo gives an evaluator a working OpenScribe instance from a fresh checkout. It builds the checked-out code and keeps its data in separate Docker volumes. It does not mount the source tree or enable live reload.

The web application listens only on `127.0.0.1`. PostgreSQL, Redis, and Vault stay inside the Docker network. The application can make outbound HTTPS requests to providers that you configure.

## Requirements

- Docker Engine or Docker Desktop;
- Docker Compose with `docker compose up --wait` support;
- enough free disk space for the image and persistent volumes;
- a Deepgram API key and an OpenAI API key if you want to run the full transcription and note-generation path.

The first release supports public HTTPS providers. It does not include a host Ollama setup.

## Start the demo

From the repository root, run:

```bash
docker compose -f docker-compose.demo.yml up -d --build --wait
```

The first start builds the image, starts the services, applies database migrations, initializes local Vault, and seeds the demo. It fails if any required bootstrap step fails. A later start retries work that did not finish.

Open:

- application: `http://127.0.0.1:8080`;
- API documentation: `http://127.0.0.1:8080/docs`;
- this guide: `docs/local-demo.md`.

Print the bootstrap summary at any time:

```bash
docker compose -f docker-compose.demo.yml logs seed-demo
```

The summary lists the URL and the three accounts:

| Role | Email | Password |
| --- | --- | --- |
| System administrator | `admin@openscribe.local` | `OpenScribeLocal27` |
| Team leader | `leader@openscribe.local` | `OpenScribeLocal27` |
| Clinician | `clinician@openscribe.local` | `OpenScribeLocal27` |

These fixed credentials are safe only while the demo stays on localhost. Do not expose its port to a local network or the internet. Use synthetic content in this instance.

## What the seed contains

The clinician owns one synthetic primary-care consultation about a fictional adult with a persistent cough. The seed follows the normal content boundaries:

- `Daily Driver` is the starting Template unless the clinician chooses another;
- the team also receives `GP Note`, `GLP1 Review`, `Depression`, and
  `Dictation cleaner`;
- the team receives `Physio Referral`, `Referral letter`, and
  `Patient follow-up message` Quick Actions;
- the consultation has its own transcript retention root;
- native Presidio processes the committed synthetic transcript at the normal redaction boundary;
- the clinician alone owns the transcript-derived content;
- the team keeps its normal 30-day retention policy;
- a structured EMIS example draft shows the completed note editor;
- the example draft is editable and still requires clinician review;
- its source label is `OpenScribe synthetic example`, not the name of a real provider.

The structured draft uses the current EMIS sections. The seed does not create a provider credential, quota attempt, or usage event for the example draft.

## Follow the demo click by click

This tour starts with the finished synthetic example. It then connects real
providers and creates a new note from the same synthetic consultation.

OpenScribe keeps its normal authority rules in the demo:

- the clinician can see consultation content;
- the team leader can choose team services but cannot see their keys;
- the system administrator can add provider keys but cannot see consultation
  content.

### Part 1: inspect the finished example

You do not need an API key for this part.

#### Sign in as the clinician

1. Open `http://127.0.0.1:8080`.
2. Click **Sign in**.
3. In **Email**, enter `clinician@openscribe.local`.
4. In **Password**, enter `OpenScribeLocal27`.
5. Click **Sign in**.
6. Check that the session title says **Synthetic cough consultation**.
7. If another session opens, click **Recent consultations**, then click
   **Synthetic cough consultation**.

#### Read and edit the completed EMIS note

1. Click **Clinical Note**.
2. In the note list, click **Synthetic cough consultation note**.
3. Read the eight sections:
   **Problem**, **History**, **Family history**, **Social history**,
   **Examination**, **Comment**, **Tasks**, and **Investigations**.
4. Click in any line to edit it. Use only synthetic text.
5. Clear a line's checkbox to leave that line out of copied text.
6. Click **Copy _section name_ section** beside a heading to copy one section.
7. To copy the whole reviewed note, click **Select all**, then
   **Copy selected**.

Edits change only this generated draft. They do not change the transcript,
Working note, template, or other source material.

#### Compare the Working note

1. In the note list, click **Working note**.
2. Read the clinician's short source note.
3. Click **Synthetic cough consultation note** to return to the completed
   example.

The Working note is a separate source for generation. Editing a generated note
does not rewrite it.

#### Inspect Presidio redaction

1. Click **Transcript**.
2. Under **Consultation sources**, read the synthetic transcript.
3. Find the **PII** panel on the right.
4. Check that it says **Redaction check complete.**
5. Check that the count is greater than zero.
6. Click **Hide PII**. The marked names, date of birth, phone number, and
   address disappear.
7. Click **Show PII** to restore them.

The demo ran the normal built-in Presidio boundary during seeding. It does not
offer an original-versus-redacted generation switch.

#### Sign out

1. In the left sidebar, click **Sign out**.
2. Check that the sign-in page opens.

### Part 2: add an LLM provider

The system administrator adds credentials. The team leader will choose how the
team uses the provider in Part 4.

#### Sign in as the system administrator

1. In **Email**, enter `admin@openscribe.local`.
2. In **Password**, enter `OpenScribeLocal27`.
3. Click **Sign in**.
4. Under **Teams** in the left sidebar, click
   **OpenScribe Demo Team**.
5. Check that the page heading says **OpenScribe Demo Team**.

#### Add OpenAI

1. Click the **LLM** team tab.
2. Click **＋ Add LLM provider**.
3. On **Choose provider**, click **OpenAI**.
4. Click **Continue**.
5. In **API key**, paste your OpenAI API key.
6. Click **Check credentials and find models**.
7. Wait for **Discovered models**.
8. Check the provider, endpoint, discovery status, and model count.
9. Leave checked only the models that the team should be able to use.
   Use **Clear**, the search box, and **Select all** to manage a long list.
10. Click **Continue**.
11. On **Provider defaults**, keep or change **Provider name**.
12. Choose a **Default model** from the models you kept.
13. Leave **Available for team selection** checked.
14. Click **Save provider**.
15. Back on **Large-language-model providers**, check that the new provider
    appears.

OpenScribe writes the key to the demo's local Vault. It clears the key field
after the check and never shows the saved value.

DeepSeek follows the same path. Choose **DeepSeek** instead of **OpenAI** in
step 3 and use a DeepSeek key. Exact model names depend on the provider account,
so this guide does not prescribe one.

### Part 3: add Deepgram speech to text

The seeded consultation has no audio, so this part is optional for the first
tour. Complete it if you want to test synthetic recording or file upload later.

1. Click the **STT** team tab.
2. Click **＋ Add STT provider**.
3. On **Choose provider**, click **Deepgram**.
4. Click **Continue**.
5. Leave **Deepgram processing endpoint** set to
   **EU endpoint (recommended)**.
6. In **API key**, paste your Deepgram API key.
7. Click **Check connection**.
8. On **Connection verified**, check **Provider**, **Endpoint**,
   **Models found**, and **Status**.
9. Read any entry under **Warnings / notes**.
10. Click **Continue**.
11. On **Provider defaults**, choose a **Default model**.
12. Leave **Default language** blank to use the provider default, or enter the
    language required for your test.
13. Leave **Available for team selection** checked.
14. Click **Save provider**.
15. Back on **Speech-to-text providers**, check that Deepgram appears.

Do not choose the global Deepgram endpoint unless it suits your deployment's
data rules.

#### Sign out

1. In the admin sidebar, click **Log out**.
2. Check that the sign-in page opens.

### Part 4: let the team use the providers

#### Sign in as the team leader

1. In **Email**, enter `leader@openscribe.local`.
2. In **Password**, enter `OpenScribeLocal27`.
3. Click **Sign in**.
4. In the left sidebar, under **Team**, click **AI services**.
5. Check that the page says **Choose admin-provisioned services.
   Credentials stay private.**

#### Choose the writing assistant

1. Find **Writing assistant**.
2. Click its **Configure** button.
3. In **Provider**, choose the OpenAI or DeepSeek provider that you added.
4. Under **Allowed models**, leave checked only the models clinicians may use.
5. Beside one checked model, select **Team default**.
6. Click **Save**.
7. Check that **Writing assistant** now shows the provider name instead of
   **Not configured**.

#### Choose speech to text

Skip this section if you did not add Deepgram.

1. Find **Speech to text**.
2. Click its **Configure** button.
3. Set **Use for** to **Conversation transcription**.
4. In **Provider**, choose the Deepgram provider.
5. Check **Model**.
6. Leave **Language** blank for the provider default, or enter the language
   required for your test.
7. Click **Save**.
8. Check that the **Conversation** line shows Deepgram.

To use Deepgram for post-consultation dictation too, repeat the steps with
**Use for** set to **Post-consultation dictation**.

Do not change **De-identification** for this tour. It should say
**Built-in Native Presidio fallback**.

#### Sign out

1. In the left sidebar, click **Sign out**.
2. Check that the sign-in page opens.

### Part 5: create a fresh note with the provider

#### Return as the clinician

1. Sign in as `clinician@openscribe.local` with
   `OpenScribeLocal27`.
2. Check that **Synthetic cough consultation** opens.
3. If it does not, click **Recent consultations**, then
   **Synthetic cough consultation**.
4. Check that the top bar no longer says **Generation unavailable**.

If generation remains unavailable, return to Part 4 and check that the team
leader saved a writing assistant and a team-default model.

#### Choose the source and template

1. Click **Clinical Note**.
2. Click **Working note** and review the source text.
3. Click the **Daily Driver** template button at the top of the note panel.
4. In **Choose a template**, select **Synthetic EMIS consultation note**.

#### Generate and review

1. Click **Create**.
2. Wait while the note shows a queued or processing state.
3. When it is ready, click the new note in the note list.
4. Read all eight EMIS sections.
5. Compare it with **Synthetic cough consultation note**, the fixed seeded
   example.
6. Edit any line that needs correction.
7. Clear the checkbox beside any line that should not be copied.
8. Click **Select all**, then **Copy selected**, or copy one section with its
   copy button.

Provider output is validated before display. Every result is still a draft:
the clinician must review it before using it elsewhere.

See the [system administrator tutorial](tutorials/admin.md),
[team leader tutorial](tutorials/team-leader.md), and
[user tutorial](tutorials/user.md) for workflows beyond this guided tour.

## Stop, start, and inspect

Stop the containers while keeping all demo data:

```bash
docker compose -f docker-compose.demo.yml down
```

Start them again:

```bash
docker compose -f docker-compose.demo.yml up -d --wait
```

Rebuild after changing or updating the checked-out code:

```bash
docker compose -f docker-compose.demo.yml up -d --build --wait
```

Inspect status and logs:

```bash
docker compose -f docker-compose.demo.yml ps
docker compose -f docker-compose.demo.yml logs -f
```

Accounts, passwords, sessions, provider settings, Vault keys, transcripts, and drafts persist across ordinary stops, starts, and rebuilds. After the first successful seed, startup does not change accounts or user content.

## Reset the whole demo

Reset has no undo path. It removes the isolated demo's PostgreSQL, Redis, Vault, Vault-bootstrap, and seed-state volumes. This deletes every account, provider credential, transcript, note, and queued task in the demo.

Stop and read the command before you run it:

```bash
docker compose -f docker-compose.demo.yml down --volumes
```

Run the normal start command to build a new empty demo state and seed it again.

Do not try to reset only PostgreSQL or only Vault. Encrypted database content needs the matching Vault keys and bootstrap data.

## Update to another version

Stop the demo, update the checked-out code, then run the rebuild command. Forward database migrations run during startup, and the named volumes keep existing data.

Back up PostgreSQL, Vault, and Vault-bootstrap state together before moving between release tags. The [persistent Docker guide](docker.md) explains why they form one recovery set.

A release tag is a fixed name for one tested repository version, such as `demo-v0.1.0`. Tags do not move when `master` changes. To check out a later tagged release:

```bash
git fetch --tags
git checkout <release-tag>
docker compose -f docker-compose.demo.yml up -d --build --wait
```

The first demo tag will be published after the quick start has passed its release checks.

## Troubleshooting

If startup fails, inspect service state and recent logs:

```bash
docker compose -f docker-compose.demo.yml ps -a
docker compose -f docker-compose.demo.yml logs --tail=300
```

Fix the reported cause and run the start command again. Do not use the reset command as a routine fix: it destroys the evidence needed to diagnose the failure as well as all saved demo data.

If port `8080` is already in use, stop the other process before starting the demo. The demo keeps the fixed localhost address so that its documented URL and fixed-account safety boundary remain clear.
