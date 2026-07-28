import { createGuidedTour } from '../transcribe/tour.js?v=20260728-shared-section-guide';

const GUIDE_VERSION = 'section-v2';

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
      title: 'Create a template',
      body: 'Select New to create a personal template. A template controls the structure and instructions for a clinical note.',
    },
    {
      target: '[data-template-library] section[aria-labelledby="personal-template-heading"]',
      title: 'Personal templates',
      body: 'You can edit, copy, and delete your personal templates. Only you can use them.',
    },
    {
      target: '[data-template-library] section[aria-labelledby="team-template-heading"]',
      title: 'Team templates',
      body: 'All team members can use team templates. Members can copy one to Personal. Team leaders can also create and edit team templates.',
    },
    {
      target: ['[data-template-editor]', '[data-template-library] .template-library-detail'],
      title: 'Review the template',
      body: 'The guide opened the first available template. Review its note mode and instructions. Save only after checking the full template.',
    },
    {
      target: '[data-template-library] .template-library-utilities',
      title: 'Import or export',
      body: 'Import adds templates from a JSON bundle. Export saves selected templates. Never put patient data or credentials in a bundle.',
    },
  ],
  'quick-actions': [
    {
      target: '[data-quick-action-library] .template-library-sidebar__header',
      title: 'Create a quick action',
      body: 'Select New to save a reusable follow-up instruction, such as a referral letter or patient message.',
    },
    {
      target: '[data-quick-action-library] section[aria-labelledby="personal-quick-action-heading"]',
      title: 'Personal quick actions',
      body: 'You can edit, copy, and delete your own quick actions. They appear in Follow Ups for your consultations.',
    },
    {
      target: '[data-quick-action-library] section[aria-labelledby="team-quick-action-heading"]',
      title: 'Team quick actions',
      body: 'Team actions are shared. Members can copy one to Personal. Team leaders can create and edit shared actions.',
    },
    {
      target: ['[data-quick-action-editor]', '[data-quick-action-library] .template-library-detail'],
      title: 'Write a clear instruction',
      body: 'The guide opened the first available quick action. State what it should produce, who it is for, and what it must include or avoid.',
    },
    {
      target: '[data-quick-action-library] .template-library-utilities',
      title: 'Move saved actions',
      body: 'Use Import and Export to move quick actions as JSON. Do not include patient data, notes, transcripts, or credentials.',
    },
  ],
  'smart-phrases': [
    {
      target: '[data-smart-phrase-library] .smart-phrase-library-sidebar__header',
      title: 'Create a smart phrase',
      body: 'Select New to save reusable wording. Smart phrases are personal to you.',
    },
    {
      target: '[data-smart-phrase-search]',
      title: 'Find a phrase',
      body: 'Search by trigger or expansion text when your library grows.',
    },
    {
      target: '[data-smart-phrase-list]',
      title: 'Use the trigger',
      body: 'In the note editor, type a slash and the trigger, then press Enter or Tab to insert the expansion.',
    },
    {
      target: ['[data-smart-phrase-form]', '[data-smart-phrase-library] .smart-phrase-library-detail'],
      title: 'Edit the expansion',
      body: 'The guide opened the first available phrase. Keep its trigger short and check the wording before using it in a clinical note.',
    },
    {
      target: '[data-smart-phrase-library] .smart-phrase-library-utilities',
      title: 'Import or export phrases',
      body: 'Bundles help you keep or move phrases. Do not put patient information or other confidential data in them.',
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

  const shouldPrepareExample = guideRequested || !hasCompletedGuide();
  const navigatingToExample = shouldPrepareExample && openFirstLibraryItemForGuide();

  if (!navigatingToExample) {
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
