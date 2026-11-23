const TIME_ZONE = "Europe/London";
const OFFLINE_START = 19 * 60;
const OFFLINE_END = 7 * 60;

export default {
  async fetch(request) {
    if (!isHomepageNavigation(request)) return fetch(request);

    const now = new Date();
    if (isOfflineTime(now, OFFLINE_START, OFFLINE_END)) {
      const weekend = expectedBackDay(now, OFFLINE_START, OFFLINE_END) === "Monday";
      const backTime = weekend ? "Monday at 7&nbsp;am" : "Around 7&nbsp;am";
      return serviceNotice(
        503,
        "OpenScribe is offline overnight",
        weekend ? "Please come back Monday morning." : "Please come back in the morning.",
        `<dl class="hours"><dt>Expected back</dt><dd class="hour">${backTime}</dd><dd>UK time</dd></dl>`,
      );
    }

    let response;
    try {
      response = await fetch(request);
    } catch {
      return outageNotice(502);
    }

    if (response.status < 500 || response.status >= 600) return response;
    return outageNotice(response.status);
  },
};

function outageNotice(status) {
  return serviceNotice(status, "OpenScribe is temporarily unavailable", "Please try again shortly.");
}

function serviceNotice(status, title, message, hours = "") {
  return new Response(noticeHtml(title, message, hours), {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store, no-cache, must-revalidate",
      "pragma": "no-cache",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
      "x-robots-tag": "noindex, nofollow",
    },
  });
}

export function isHomepageNavigation(request) {
  if (request.method !== "GET" || new URL(request.url).pathname !== "/") return false;
  const fetchMode = request.headers.get("sec-fetch-mode");
  const fetchDest = request.headers.get("sec-fetch-dest");
  const acceptsHtml = (request.headers.get("accept") || "").includes("text/html");
  return fetchMode === "navigate" && fetchDest === "document" && acceptsHtml;
}

export function parseClock(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  return hour < 24 && minute < 60 ? hour * 60 + minute : null;
}

export function isOfflineTime(date, start, end) {
  const local = londonParts(date);
  const current = local.hour * 60 + local.minute;
  const weekday = local.weekday;
  if (weekday === 0 || weekday === 6) return true;
  if (weekday === 5 && current >= start) return true;
  return current < end || current >= start;
}

export function expectedBackDay(date, start, end) {
  const local = londonParts(date);
  if ((local.weekday === 5 && local.hour * 60 + local.minute >= start) || local.weekday === 6 || local.weekday === 0) return "Monday";
  return local.hour * 60 + local.minute < end ? "today" : "tomorrow";
}

function londonParts(date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: TIME_ZONE,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const value = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return { weekday: ({ Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 })[value.weekday], hour: Number(value.hour), minute: Number(value.minute) };
}

function noticeHtml(title, message, hours) {
  return `<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#f8faf8">
  <title>${title}</title>
  <style>
    :root { color-scheme: light; font-family: "DM Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1A202C; background: #FAF8F5; }
    * { box-sizing: border-box; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; padding: max(24px, env(safe-area-inset-top)) 20px max(24px, env(safe-area-inset-bottom)); background: radial-gradient(ellipse at 20% 18%, #E0F2F5, transparent 50%), radial-gradient(ellipse at 78% 46%, color-mix(in srgb, #F6E8BB 38%, #FAF8F5), transparent 54%), radial-gradient(ellipse at 70% 86%, color-mix(in srgb, #DFF2E2 55%, #FAF8F5), transparent 54%), #FAF8F5; background-size: 145% 145%; background-position: 36% 28%; }
    main { width: min(100%, 720px); padding: clamp(16px, 4vw, 32px); border-radius: 24px; background: #FAF8F5; text-align: center; }
    .mark { width: 44px; height: 44px; display: grid; place-items: center; margin: 0 auto 26px; border-radius: 999px; color: white; background: radial-gradient(circle at 32% 24%, rgba(255, 255, 255, .32), transparent 28%), linear-gradient(135deg, #1D4F5E, #0E6B76); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .22), 0 10px 22px rgba(29, 79, 94, .18); }
    h1 { max-width: 16ch; margin: 0 auto 18px; font-family: Georgia, "Times New Roman", serif; font-size: clamp(2.5rem, 6vw, 4.8rem); font-weight: 500; line-height: 1.14; letter-spacing: -.045em; text-wrap: balance; }
    p { max-width: 58ch; margin: 0 auto; color: #2D3748; font-size: 1.08rem; line-height: 1.74; }
    .hours { width: min(100%, 320px); margin: 30px auto 0; padding: 22px 24px; border: 1px solid #E2DED6; border-radius: 16px; background: #E0F2F5; }
    dt { color: #1D4F5E; font-weight: 700; }
    dd { margin: 8px 0 0; }
    .hour { font-family: Georgia, "Times New Roman", serif; font-size: clamp(1.7rem, 4vw, 2.5rem); font-weight: 500; letter-spacing: -.03em; }
    @media (prefers-reduced-motion: no-preference) { body { animation: colour-shift 28s ease-in-out infinite alternate; } }
    @keyframes colour-shift { from { background-position: 22% 16%; } to { background-position: 78% 74%; } }
  </style>
</head>
<body>
  <main>
    <span class="mark" aria-hidden="true"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.24 12.24a6 6 0 0 0-8.48-8.48L3 12.5V21h8.5z"/><path d="m16 8-2 2"/><path d="M9 15 3 21"/><path d="M17 8a5 5 0 0 1-5 5"/></svg></span>
    <h1>${title}</h1>
    <p>${message}</p>
    ${hours}
  </main>
</body>
</html>`;
}
