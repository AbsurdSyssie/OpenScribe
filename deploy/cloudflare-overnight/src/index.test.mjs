import test from "node:test";
import assert from "node:assert/strict";
import worker, { expectedBackDay, isHomepageNavigation, isOfflineTime, parseClock } from "./index.mjs";

const at = (iso) => new Date(iso);
const start = parseClock("19:00");
const end = parseClock("07:00");

test("parses valid schedule clocks and rejects malformed values", () => {
  assert.equal(parseClock("19:00"), 1140);
  assert.equal(parseClock("07:00"), 420);
  for (const value of ["7:00", "24:00", "12:60", "noon"]) assert.equal(parseClock(value), null);
});

test("weekday gate follows London time and includes the overnight boundaries", () => {
  assert.equal(isOfflineTime(at("2026-11-24T18:59:00Z"), start, end), false); // Tue 18:59 GMT
  assert.equal(isOfflineTime(at("2026-11-24T19:00:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-25T06:59:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-25T07:00:00Z"), start, end), false);
  assert.equal(isOfflineTime(at("2026-07-20T17:59:00Z"), start, end), false); // Mon 18:59 BST
  assert.equal(isOfflineTime(at("2026-07-20T18:00:00Z"), start, end), true); // Mon 19:00 BST
});

test("weekend shutdown runs Friday evening through Monday 07:00 London time", () => {
  assert.equal(isOfflineTime(at("2026-11-27T18:59:00Z"), start, end), false); // Fri
  assert.equal(isOfflineTime(at("2026-11-27T19:00:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-28T12:00:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-29T12:00:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-30T06:59:00Z"), start, end), true);
  assert.equal(isOfflineTime(at("2026-11-30T07:00:00Z"), start, end), false);
});

test("reopening label is Monday on weekend and today during weekday early hours", () => {
  assert.equal(expectedBackDay(at("2026-11-27T20:00:00Z"), start, end), "Monday");
  assert.equal(expectedBackDay(at("2026-11-29T12:00:00Z"), start, end), "Monday");
  assert.equal(expectedBackDay(at("2026-11-24T06:00:00Z"), start, end), "today");
});

test("only root document navigations qualify", () => {
  const nav = new Request("https://openscribe.co.uk/", { headers: { accept: "text/html", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document" } });
  assert.equal(isHomepageNavigation(nav), true);
  assert.equal(isHomepageNavigation(new Request("https://openscribe.co.uk/app", { headers: nav.headers })), false);
  assert.equal(isHomepageNavigation(new Request("https://openscribe.co.uk/", { headers: { accept: "text/html" } })), false);
  assert.equal(isHomepageNavigation(new Request("https://openscribe.co.uk/", { method: "POST", headers: nav.headers })), false);
});

test("daytime homepage errors use the outage page and preserve HTTP status", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: at("2026-11-24T12:00:00Z") });
  const request = new Request("https://openscribe.co.uk/", {
    headers: { accept: "text/html", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document" },
  });
  let upstream = new Response("normal homepage");
  const fetchMock = t.mock.method(globalThis, "fetch", async () => {
    if (!upstream) throw new Error("Synthetic origin connection failure");
    return upstream;
  });

  assert.equal(await worker.fetch(request), upstream);
  for (const status of [500, 502, 522]) {
    upstream = new Response("Synthetic upstream error", { status });
    const response = await worker.fetch(request);
    assert.equal(response.status, status);
    assert.match(response.headers.get("cache-control"), /no-store/);
    const html = await response.text();
    assert.match(html, /OpenScribe is temporarily unavailable/);
    assert.doesNotMatch(html, /Expected back|Synthetic upstream error/);
  }

  assert.equal(await worker.fetch(new Request("https://openscribe.co.uk/api")), upstream);
  upstream = null;
  const unavailable = await worker.fetch(request);
  assert.equal(unavailable.status, 502);
  assert.match(await unavailable.text(), /OpenScribe is temporarily unavailable/);

  t.mock.timers.setTime(at("2026-11-28T12:00:00Z").getTime());
  const calls = fetchMock.mock.callCount();
  const overnight = await worker.fetch(request);
  assert.equal(overnight.status, 503);
  assert.match(await overnight.text(), /Monday at 7/);
  assert.equal(fetchMock.mock.callCount(), calls);
});
