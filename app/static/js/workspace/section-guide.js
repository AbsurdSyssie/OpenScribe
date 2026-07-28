import { createGuidedTour } from '../transcribe/tour.js?v=20260728-plain-guides';

const GUIDE_VERSION = 'section-v3';

const SECTION_STEPS = {
  account: [
    {
      target: '[data-settings-panel="account"] .settings-section__intro',
      title: 'Manage your account',
      body: 'Use this page for your name, sign-in email, and password. These changes affect only your account.',
    },
    {
      target: '[data-settings-panel="account"] .account-settings-card:nth-of-type(1)',
      title: 'Update your name',
      body: 'Your name identifies you inside OpenScribe. It does not change your sign-in email.',
    },
    {
      target: '[data-settings-panel="account"] .account-settings-card:nth-of-type(2)',
      title: 'Change your email',
      body: 'Enter your current password. If authenticator MFA is active, enter its current code. Other sessions will sign out.',
    },
    {
      target: '[data-settings-panel="account"] .account-settings-card:nth-of-type(3)',
      title: 'Change your password',
      body: 'Use at least 12 characters with uppercase, lowercase, and a number. Changing it signs out other sessions.',
    },
  ],
  preferences: [
    {
      target: '[data-settings-panel="preferences"] .settings-section__intro',
      title: 'Set your defaults',
      body: 'Preferences change how OpenScribe writes your notes. They do not change team service settings.',
    },
    {
      target: '[data-settings-panel="preferences"] .settings-card',
      title: 'Check available services',
      body: 'This card shows the speech and writing services your team leader made available.',
    },
    {
      target: '[data-settings-panel="preferences"] .setting-row--form',
      title: 'Choose length and detail',
      body: 'Set your usual note length and level of detail. You can still review and edit every draft.',
    },
    {
      target: '[data-settings-panel="preferences"] .settings-advanced',
      title: 'Choose an approved model',
      body: 'Open Advanced to choose a team-approved model, or keep the team default.',
    },
  ],
  templates: [
    {
      target: '[data-template-library] .template-library-sidebar__header',
      title: 'What templates do',
      body: 'A template tells the AI how you want it to write your note and what information should go where.',
    },
    {
      target: '[data-template-library] section[aria-labelledby="personal-template-heading"]',
      title: 'Personal templates',
      body: 'Personal templates belong to you. You can create, edit, copy, and delete them.',
    },
    {
      target: '[data-template-library] section[aria-labelledby="team-template-heading"]',
      title: 'Team templates',
      body: 'Team templates are shared with everyone in your team. You can copy one to Personal. Team leaders can also create and edit them.',
    },
    {
      target: [
        '[data-template-editor] input[name="name"]',
        '[data-template-editor] .template-preview__metadata > div:first-child',
      ],
      title: 'Template name',
      body: 'Use a clear name so people can find the right template when they start a consultation.',
    },
    {
      target: [
        '[data-template-editor] select[name="mode"]',
        '[data-template-editor] .template-preview__metadata > div:nth-child(2)',
      ],
      title: 'Choose the note type',
      body: 'Choose an EMIS sectioned note for clinical notes that will go into EMIS. Choose free text for other notes and documents.',
    },
    {
      target: [
        '[data-template-editor] select[name="mode"]',
        '[data-template-editor] .template-preview__metadata > div:nth-child(2)',
      ],
      title: 'EMIS sectioned notes',
      body: 'OpenScribe puts each part of the note under the matching EMIS section. This makes the note easier to check and enter into EMIS.',
    },
    {
      target: [
        '[data-template-editor] select[name="mode"]',
        '[data-template-editor] .template-preview__metadata > div:nth-child(2)',
      ],
      title: 'Free text notes',
      body: 'Free text writes one continuous note. Use it for anything that does not need to be split into EMIS sections.',
    },
    {
      target: [
        '[data-template-editor] input[name="description"]',
        '[data-template-editor] .template-preview__metadata .field--wide',
      ],
      title: 'Description',
      body: 'Write a short reminder of what the template is for. This helps people choose the right one.',
    },
    {
      target: [
        '[data-template-editor] textarea[name="prompt_text"]',
        '[data-template-editor] .template-preview__prompt',
      ],
      title: 'What should be written',
      body: 'Give the AI general advice about the note. Say what to include, what to leave out, how detailed it should be, and how you want it worded.',
    },
    {
      target: [
        '[data-template-editor] [data-template-sections]',
        '[data-template-editor] select[name="mode"]',
        '[data-template-editor] .template-preview__metadata > div:nth-child(2)',
      ],
      title: 'EMIS section instructions',
      body: 'For an EMIS note, tell the AI what belongs in each section. Leave a section blank when you do not need it.',
    },
    {
      target: '[data-template-editor] .action-bar',
      title: 'Save or cancel',
      body: 'Save when the template is ready. Select Cancel to leave without keeping your changes.',
    },
    {
      target: '[data-template-library] .template-library-utilities',
      title: 'Import, export, and help',
      body: 'Import adds templates from a saved file. Export saves selected templates. Help explains how to make a template. Never include patient information or passwords.',
    },
  ],
  'quick-actions': [
    {
      target: '[data-quick-action-library] .template-library-sidebar__header',
      title: 'What quick actions do',
      body: 'A quick action tells the AI to create a useful follow-up from the current consultation, such as a letter, summary, message, or task.',
    },
    {
      target: '[data-quick-action-library] section[aria-labelledby="personal-quick-action-heading"]',
      title: 'Personal quick actions',
      body: 'Personal quick actions belong to you. You can create, edit, copy, and delete them.',
    },
    {
      target: '[data-quick-action-library] section[aria-labelledby="team-quick-action-heading"]',
      title: 'Team quick actions',
      body: 'Team quick actions are shared with everyone in your team. You can copy one to Personal. Team leaders can also create and edit them.',
    },
    {
      target: [
        '[data-quick-action-editor] input[name="name"]',
        '[data-quick-action-editor] .quick-action-preview .setting-row',
      ],
      title: 'Quick action name',
      body: 'Use a clear name that says what the action makes, such as Referral letter or Patient message.',
    },
    {
      target: [
        '[data-quick-action-editor] input[name="description"]',
        '[data-quick-action-editor] .quick-action-preview .setting-row',
      ],
      title: 'Description',
      body: 'Write a short explanation of when to use the action. This helps people choose the right one.',
    },
    {
      target: [
        '[data-quick-action-editor] textarea[name="prompt_text"]',
        '[data-quick-action-editor] .quick-action-preview__text',
      ],
      title: 'Quick action text',
      body: 'Tell the AI what to make, who it is for, what to include, and how it should sound. Keep this general. The consultation supplies the patient details.',
    },
    {
      target: [
        '[data-quick-action-editor] input[name="is_active"]',
        '[data-quick-action-editor] .asset-status-pill',
      ],
      title: 'Active or inactive',
      body: 'Active actions appear in Follow Ups. Make an action inactive when you want to keep it but stop using it for now.',
    },
    {
      target: [
        '[data-quick-action-editor] .action-bar',
        '[data-quick-action-editor] .quick-action-preview > form',
      ],
      title: 'Save or cancel',
      body: 'Save when the action is ready. Select Cancel to leave without keeping your changes.',
    },
    {
      target: '[data-quick-action-library] .template-library-utilities',
      title: 'Import, export, and help',
      body: 'Import adds quick actions from a saved file. Export saves selected actions. Help explains how to make one. Never include patient information or passwords.',
    },
  ],
  'smart-phrases': [
    {
      target: '[data-smart-phrase-library] .smart-phrase-library-sidebar__header',
      title: 'What smart phrases do',
      body: 'A smart phrase inserts wording you use often. Smart phrases are personal to you.',
    },
    {
      target: '[data-smart-phrase-search]',
      title: 'Find a phrase',
      body: 'Search by its trigger or by words in the saved text.',
    },
    {
      target: '[data-smart-phrase-form] input[name="trigger"]',
      title: 'Trigger',
      body: 'The trigger is the short word you type after a slash. Keep it easy to remember.',
    },
    {
      target: '[data-smart-phrase-form] textarea[name="expansion_text"]',
      title: 'Expansion',
      body: 'The expansion is the text OpenScribe inserts. Check it before using it in a note.',
    },
    {
      target: '[data-smart-phrase-form] input[name="description"]',
      title: 'Description',
      body: 'Add a short reminder of when to use the phrase. This field is optional.',
    },
    {
      target: '[data-smart-phrase-form] .smart-phrase-editor-actions',
      title: 'Save or cancel',
      body: 'Save when the phrase is ready. Select Cancel to leave without keeping your changes.',
    },
    {
      target: '[data-smart-phrase-list]',
      title: 'Use the phrase',
      body: 'In the note editor, type a slash and the trigger. Press Enter or Tab to insert the saved wording.',
    },
    {
      target: '[data-smart-phrase-library] .smart-phrase-library-utilities',
      title: 'Import, export, and help',
      body: 'Import adds phrases from a saved file. Export saves selected phrases. Help explains how to make them. Never include patient information or passwords.',
    },
  ],
  'ai-services': [
    {
      target: '[data-settings-panel="ai-services"] .settings-section__intro',
      title: 'Choose team services',
      body: 'Team leaders choose from services provisioned by the system administrator. Credentials are not shown here.',
    },
    {
      target: '[data-settings-panel="ai-services"] .service-row:nth-of-type(1)',
      title: 'Speech services',
      body: 'Choose separate speech services for consultation recordings and post-consultation dictation when needed.',
    },
    {
      target: '[data-settings-panel="ai-services"] .service-row:nth-of-type(2)',
      title: 'Writing assistant',
      body: 'Choose the team writing service, allowed models, and team default model.',
    },
    {
      target: '[data-settings-panel="ai-services"] .service-row:nth-of-type(3)',
      title: 'De-identification',
      body: 'Choose the service that replaces identifiable text before supported external processing. Clearing it uses the built-in fallback.',
    },
    {
      target: '[data-settings-panel="ai-services"] .service-row:nth-of-type(4)',
      title: 'Clinical NLP',
      body: 'Enable this only when the team needs the configured clinical text analysis endpoint.',
    },
  ],
  'team-members': [
    {
      target: '[data-settings-panel="team-members"] .settings-section__intro',
      title: 'Manage team access',
      body: 'Team leaders can create users, change access state, send setup or recovery links, and remove users.',
    },
    {
      target: '[data-settings-panel="team-members"] details:first-of-type > summary',
      title: 'Create a team member',
      body: 'Open this form, set the role and status, and keep MFA required unless there is a clear reason not to.',
    },
    {
      target: '[data-settings-panel="team-members"] .member-list',
      title: 'Review the member list',
      body: 'Check each user’s email, role, and status before changing access.',
    },
    {
      target: [
        '[data-settings-panel="team-members"] .member-menu',
        '[data-settings-panel="team-members"] .member-list',
      ],
      title: 'Use account actions carefully',
      body: 'Suspend, reactivate, reset MFA, or send recovery links from the member menu. Deleting a user also deletes their owned transcript content.',
    },
  ],
  'account-requests': [
    {
      target: '[data-settings-panel="account-requests"] .settings-section__intro',
      title: 'Review access requests',
      body: 'This page lists people who asked to join the team.',
    },
    {
      target: '[data-settings-panel="account-requests"] .asset-list',
      title: 'Check the request',
      body: 'Confirm the requester, email, team, and request status before deciding.',
    },
    {
      target: [
        '[data-settings-panel="account-requests"] .request-actions',
        '[data-settings-panel="account-requests"] .settings-empty',
      ],
      title: 'Approve or reject',
      body: 'For approval, set a role and temporary password. For rejection, record a clear reason. No action is needed when the list is empty.',
    },
  ],
};

const LIBRARY_GUIDE_CONFIG = {
  templates: {
    shell: '[data-template-library]',
    firstItem: '[data-template-library] .template-library-row__select',
  },
  'quick-actions': {
    shell: '[data-quick-action-library]',
    firstItem: '[data-quick-action-library] .template-library-row__select',
  },
  'smart-phrases': {
    shell: '[data-smart-phrase-library]',
    firstItem: '[data-smart-phrase-library] .smart-phrase-library-row__select',
  },
};

const section = document.body?.dataset.workspaceSection || '';
const steps = SECTION_STEPS[section] || [];
const storageKey = `openscribe:tour:workspace:${section}:${GUIDE_VERSION}`;
const guideRequested = new URLSearchParams(window.location.search).get('guide') === '1';

const hasCompletedGuide = () => {
  try {
    return window.localStorage.getItem(storageKey) === 'done';
  } catch (_) {
    return false;
  }
};

const resetGuideCompletion = () => {
  try {
    window.localStorage.removeItem(storageKey);
  } catch (_) {
    // The guide still works when browser privacy settings block localStorage.
  }
};

const clearGuideQuery = () => {
  const url = new URL(window.location.href);
  if (!url.searchParams.has('guide')) return;
  url.searchParams.delete('guide');
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
};

const openFirstLibraryItemForGuide = () => {
  const config = LIBRARY_GUIDE_CONFIG[section];
  if (!config) return false;
  const shell = document.querySelector(config.shell);
  if (!shell || shell.classList.contains('has-selection')) return false;
  const firstItem = document.querySelector(config.firstItem);
  if (!firstItem?.href) return false;

  const url = new URL(firstItem.href, window.location.href);
  url.searchParams.set('guide', '1');
  window.location.assign(url.toString());
  return true;
};

if (steps.length) {
  if (guideRequested) {
    resetGuideCompletion();
    clearGuideQuery();
  }

  const shouldPrepareSelection = guideRequested || !hasCompletedGuide();
  const navigatingToSelection = shouldPrepareSelection && openFirstLibraryItemForGuide();

  if (!navigatingToSelection) {
    const tourOverlay = document.querySelector('[data-tour-overlay]');
    const guideStartButtons = [...document.querySelectorAll('[data-start-guide]')];
    const guide = createGuidedTour({
      dom: {
        guideStartButtons: [],
        tourOverlay,
        tourHighlight: tourOverlay?.querySelector('[data-tour-highlight]'),
        tourCard: tourOverlay?.querySelector('[data-tour-card]'),
        tourScrims: {
          top: tourOverlay?.querySelector('[data-tour-scrim="top"]'),
          right: tourOverlay?.querySelector('[data-tour-scrim="right"]'),
          bottom: tourOverlay?.querySelector('[data-tour-scrim="bottom"]'),
          left: tourOverlay?.querySelector('[data-tour-scrim="left"]'),
        },
        tourTitle: tourOverlay?.querySelector('[data-tour-title]'),
        tourBody: tourOverlay?.querySelector('[data-tour-body]'),
        tourProgress: tourOverlay?.querySelector('[data-tour-progress]'),
        tourBackButton: tourOverlay?.querySelector('[data-tour-back]'),
        tourNextButton: tourOverlay?.querySelector('[data-tour-next]'),
        tourCloseButtons: [...(tourOverlay?.querySelectorAll('[data-tour-close], [data-tour-close-button]') || [])],
      },
      steps,
      storageKey,
    });

    guideStartButtons.forEach((button) => {
      button.addEventListener('click', () => {
        if (openFirstLibraryItemForGuide()) return;
        guide.startTour({ force: true });
      });
    });

    guide.attach();
  }
}
