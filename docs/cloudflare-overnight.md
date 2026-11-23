# Cloudflare overnight homepage gate

The standalone Worker in [`deploy/cloudflare-overnight/`](../deploy/cloudflare-overnight/) serves an inline, self-contained 503 page for browser document navigations to `/` during the planned shutdown. During opening hours, the same homepage navigations reach the origin first. If the origin fetch returns a 5xx response, the Worker serves a matching temporary-unavailability page with that status. If the fetch throws, the Worker serves the page with HTTP 502. The outage page has no reopening estimate and uses the same no-store policy. Other requests pass directly to the origin. The gate uses `Europe/London`, including daylight-saving changes: Monday–Thursday it is offline from 19:00 through 06:59; Friday from 19:00 and all Saturday/Sunday; it reopens Monday at 07:00. The weekend page says Monday at 7 am. Weekday pages say around 7 am.

## Current deployment

The exact route `https://openscribe.co.uk/` is attached in Cloudflare and recorded in `wrangler.toml`. The live daytime root route returned HTTP 200 in the deployment check on 23 November 2026. The overnight 503 response has not yet been checked on the live route.

The Worker requires `GET`, path `/`, `Accept: text/html`, `Sec-Fetch-Mode: navigate`, and `Sec-Fetch-Dest: document`. API, static asset, health, and application paths pass through. A browser that omits Fetch Metadata headers will also pass through. Overnight responses use HTTP 503 and `Cache-Control: no-store, no-cache, must-revalidate`; the response includes no patient or account data. The HTML has inline CSS and an inline feather mark, with the centred prototype's solid panel, teal circular mark, and slow colour motion only when reduced motion is not requested. No third-party asset request is needed.

## Initial rollout

The exact first route is `openscribe.co.uk/` (apex homepage only). Do not attach a wildcard or a hostname-wide route: clinical application routes must continue to reach the origin. Cloudflare matches the full request URL against this exact route, so homepage URLs with a query string bypass the Worker. Keep `workers_dev` and preview URLs disabled in the production config.

Before attachment:

1. Confirm the root hostname resolves through the intended Cloudflare zone and that the origin is healthy.
2. Deploy a preview version using a separate temporary Wrangler config; the checked-in config records the exact production route and disables workers.dev/preview URLs.
3. Verify a synthetic root document navigation returns the standalone 503 during an offline-window test, including `no-store`; verify a successful daytime root navigation and all non-root paths pass through unchanged, and simulate a daytime origin error to verify the outage page and error status. Check Monday morning, weekday mornings, Friday evening, Saturday, and Sunday boundaries in `Europe/London`.
4. Attach only the exact `openscribe.co.uk/` route after the schedule and smoke checks are accepted. Do not change origin routing, authentication, or application availability settings as part of this rollout.

To roll back, remove only the Worker's exact root route attachment in Cloudflare and remove that route from the checked-in Wrangler config before the next deployment. This immediately restores normal homepage delivery from the origin; the Worker code and deployment can remain available for investigation. Confirm the route is detached and that a synthetic root navigation reaches the origin. Do not delete unrelated Cloudflare routes or alter the origin as rollback steps.

The confirmed schedule is fixed in code at 19:00–07:00. The weekday/weekend policy is fixed: the weekday start applies Monday through Friday, while the weekend remains closed continuously from Friday 19:00 through Monday 07:00. Changing the policy requires updating the code, expected-reopening copy, and focused tests together. Confirm and record any schedule change before deployment.

Focused local logic checks run with:

```sh
node deploy/cloudflare-overnight/src/index.test.mjs
```

The production route is attached as described above. Repository changes do not perform future Cloudflare deployment or route mutation automatically.
