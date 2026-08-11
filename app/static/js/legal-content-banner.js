(() => {
  const STORAGE_KEY = "openscribe_browser_storage_notice_v1";
  const notice = document.querySelector("[data-browser-storage-notice]");
  const dismiss = document.querySelector("[data-browser-storage-dismiss]");
  if (!notice || !dismiss) return;

  const noticeVersion = notice.dataset.browserStorageNoticeVersion || "unpublished";
  const dismissalValue = `dismissed:${noticeVersion}`;
  let dismissed = false;
  try {
    dismissed = window.localStorage.getItem(STORAGE_KEY) === dismissalValue;
  } catch (_error) {
    dismissed = false;
  }
  notice.hidden = dismissed;

  dismiss.addEventListener("click", () => {
    try {
      window.localStorage.setItem(STORAGE_KEY, dismissalValue);
    } catch (_error) {
      // Storage may be blocked. Dismiss for this page without blocking use.
    }
    notice.hidden = true;
  });
})();
