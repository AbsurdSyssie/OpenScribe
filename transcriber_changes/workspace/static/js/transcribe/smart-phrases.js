const NOTE_INPUT_SELECTOR = '[data-structured-line-input], [data-freeform-note-input]';
const TOKEN_BOUNDARY_RE = /(^|[\s.,;!?()[\]{}"'“”‘’])\/([A-Za-z0-9_]*)$/;
const PUNCTUATION_AFTER_RE = /^[.,;:!?)]/;

function normalizedPhrase(phrase) {
  return {
    id: phrase.id,
    trigger: String(phrase.trigger || '').toUpperCase(),
    expansion_text: String(phrase.expansion_text || ''),
    description: phrase.description || null,
    last_used_at: phrase.last_used_at || null,
    times_used: Number.isFinite(Number(phrase.times_used)) ? Number(phrase.times_used) : 0,
  };
}

function phrasePreview(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 48);
}

function findSlashToken(text, caret) {
  const prefix = String(text || '').slice(0, caret);
  const match = prefix.match(TOKEN_BOUNDARY_RE);
  if (!match) return null;
  const slashIndex = caret - match[2].length - 1;
  const previousChar = slashIndex > 0 ? text[slashIndex - 1] : '';
  if (previousChar === ':') return null;
  return { start: slashIndex, end: caret, query: match[2] || '' };
}

function rankMatches(phrases, query) {
  const needle = String(query || '').toUpperCase();
  return phrases
    .filter((phrase) => !needle || phrase.trigger.includes(needle))
    .map((phrase) => ({ phrase, prefix: needle ? phrase.trigger.startsWith(needle) : true }))
    .sort((a, b) => {
      if (a.prefix !== b.prefix) return a.prefix ? -1 : 1;
      const aDate = a.phrase.last_used_at ? Date.parse(a.phrase.last_used_at) || 0 : 0;
      const bDate = b.phrase.last_used_at ? Date.parse(b.phrase.last_used_at) || 0 : 0;
      if (aDate !== bDate) return bDate - aDate;
      if (a.phrase.times_used !== b.phrase.times_used) return b.phrase.times_used - a.phrase.times_used;
      return a.phrase.trigger.localeCompare(b.phrase.trigger);
    })
    .map((item) => item.phrase);
}

function buildReplacement(input, token, expansionText) {
  const cleanExpansion = String(expansionText || '').trim();
  const value = input.value || '';
  const before = value.slice(0, token.start);
  const after = value.slice(token.end);
  const previousChar = before.slice(-1);
  const nextChar = after.slice(0, 1);
  const needsLeadingSpace = before.length > 0 && !/\s/.test(previousChar);
  const needsTrailingSpace = after.length > 0 && !/\s/.test(nextChar) && !PUNCTUATION_AFTER_RE.test(nextChar);
  const replacement = `${needsLeadingSpace ? ' ' : ''}${cleanExpansion}${needsTrailingSpace ? ' ' : ''}`;
  return { value: before + replacement + after, cursor: before.length + replacement.length };
}

function positionMenu(menu, input) {
  const rect = input.getBoundingClientRect();
  const menuHeight = Math.min(menu.scrollHeight || 180, 220);
  const below = window.innerHeight - rect.bottom >= menuHeight + 12;
  menu.style.left = `${Math.max(8, rect.left)}px`;
  menu.style.width = `${Math.min(Math.max(rect.width, 260), window.innerWidth - 16)}px`;
  menu.style.top = below ? `${rect.bottom + 6}px` : `${Math.max(8, rect.top - menuHeight - 6)}px`;
}

function createMenu() {
  const menu = document.createElement('div');
  menu.className = 'smart-phrase-menu';
  menu.setAttribute('data-smart-phrase-menu', '');
  menu.hidden = true;
  document.body.appendChild(menu);
  return menu;
}

export function attachSmartPhraseExpander({ smartPhrases, onExpanded }) {
  const phrases = (Array.isArray(smartPhrases) ? smartPhrases : [])
    .map(normalizedPhrase)
    .filter((phrase) => phrase.trigger && phrase.expansion_text);

  const menu = createMenu();
  let activeInput = null;
  let activeToken = null;
  let activeMatches = [];
  let activeIndex = 0;
  let clickingMenu = false;

  const closeMenu = () => {
    menu.hidden = true;
    menu.replaceChildren();
    activeInput = null;
    activeToken = null;
    activeMatches = [];
    activeIndex = 0;
  };

  const renderMenu = () => {
    menu.replaceChildren();
    activeMatches.slice(0, 24).forEach((phrase, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'smart-phrase-menu__item';
      item.classList.toggle('is-active', index === activeIndex);
      item.setAttribute('data-smart-phrase-index', String(index));

      const trigger = document.createElement('span');
      trigger.className = 'smart-phrase-menu__trigger';
      trigger.textContent = phrase.trigger;

      const preview = document.createElement('span');
      preview.className = 'smart-phrase-menu__preview';
      preview.textContent = phrasePreview(phrase.expansion_text);

      item.appendChild(trigger);
      item.appendChild(preview);
      menu.appendChild(item);
    });
    menu.hidden = activeMatches.length === 0;
    if (!menu.hidden && activeInput) positionMenu(menu, activeInput);
  };

  const refreshMenuForInput = (input) => {
    if (!(input instanceof HTMLTextAreaElement) || !input.matches(NOTE_INPUT_SELECTOR)) {
      closeMenu();
      return;
    }
    const token = findSlashToken(input.value, input.selectionStart ?? 0);
    if (!token) {
      closeMenu();
      return;
    }
    activeInput = input;
    activeToken = token;
    activeMatches = rankMatches(phrases, token.query);
    activeIndex = 0;
    renderMenu();
  };

  const insertActivePhrase = () => {
    if (!(activeInput instanceof HTMLTextAreaElement) || !activeToken || !activeMatches.length) return false;
    const phrase = activeMatches[activeIndex] || activeMatches[0];
    const next = buildReplacement(activeInput, activeToken, phrase.expansion_text);
    activeInput.value = next.value;
    activeInput.setSelectionRange(next.cursor, next.cursor);
    activeInput.dispatchEvent(new Event('input', { bubbles: true }));
    activeInput.focus();
    closeMenu();
    onExpanded?.({ phrase });
    return true;
  };

  document.addEventListener('input', (event) => {
    if (event.target instanceof HTMLTextAreaElement && event.target.matches(NOTE_INPUT_SELECTOR)) {
      refreshMenuForInput(event.target);
    }
  });

  document.addEventListener('keyup', (event) => {
    if (event.target instanceof HTMLTextAreaElement && event.target.matches(NOTE_INPUT_SELECTOR)) {
      if (!['ArrowUp', 'ArrowDown', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
        refreshMenuForInput(event.target);
      }
    }
  });

  document.addEventListener('selectionchange', () => {
    if (document.activeElement === activeInput) refreshMenuForInput(activeInput);
  });

  document.addEventListener('keydown', (event) => {
    if (menu.hidden) return;
    if (!(event.target instanceof HTMLTextAreaElement) || event.target !== activeInput) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeIndex = Math.min(activeMatches.length - 1, activeIndex + 1);
      renderMenu();
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeIndex = Math.max(0, activeIndex - 1);
      renderMenu();
      return;
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      insertActivePhrase();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
    }
  });

  menu.addEventListener('mousedown', () => {
    clickingMenu = true;
  });

  menu.addEventListener('click', (event) => {
    const item = event.target.closest('[data-smart-phrase-index]');
    if (!item) return;
    event.preventDefault();
    activeIndex = Number.parseInt(item.getAttribute('data-smart-phrase-index') || '0', 10) || 0;
    insertActivePhrase();
    clickingMenu = false;
  });

  document.addEventListener('focusout', (event) => {
    if (event.target === activeInput) {
      window.setTimeout(() => {
        if (!clickingMenu) closeMenu();
        clickingMenu = false;
      }, 0);
    }
  });

  document.addEventListener('mousedown', (event) => {
    if (!menu.hidden && !event.target.closest('[data-smart-phrase-menu]') && event.target !== activeInput) {
      closeMenu();
    }
  });

  window.addEventListener('resize', () => {
    if (!menu.hidden && activeInput) positionMenu(menu, activeInput);
  });
  window.addEventListener('openscribe:smart-phrases-close', closeMenu);
  return { close: closeMenu };
}
