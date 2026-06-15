# ZAP Scanning Report

ZAP by [Checkmarx](https://checkmarx.com/).


## Summary of Alerts

| Risk Level | Number of Alerts |
| --- | --- |
| High | 0 |
| Medium | 1 |
| Low | 4 |
| Informational | 5 |






## Alerts

| Name | Risk Level | Number of Instances |
| --- | --- | --- |
| Absence of Anti-CSRF Tokens | Medium | 3 |
| Cookie No HttpOnly Flag | Low | Systemic |
| Cross-Origin-Embedder-Policy Header Missing or Invalid | Low | 5 |
| Permissions Policy Header Not Set | Low | Systemic |
| Strict-Transport-Security Multiple Header Entries (Non-compliant with Spec) | Low | Systemic |
| Authentication Request Identified | Informational | 1 |
| Non-Storable Content | Informational | 5 |
| Re-examine Cache-control Directives | Informational | 5 |
| Session Management Response Identified | Informational | 8 |
| Storable and Cacheable Content | Informational | Systemic |




## Alert Detail



### [ Absence of Anti-CSRF Tokens ](https://www.zaproxy.org/docs/alerts/10202/)



##### Medium (Low)

### Description

No Anti-CSRF tokens were found in a HTML submission form.
A cross-site request forgery is an attack that involves forcing a victim to send an HTTP request to a target destination without their knowledge or intent in order to perform an action as the victim. The underlying cause is application functionality using predictable URL/form actions in a repeatable way. The nature of the attack is that CSRF exploits the trust that a web site has for a user. By contrast, cross-site scripting (XSS) exploits the trust that a user has for a web site. Like XSS, CSRF attacks are not necessarily cross-site, but they can be. Cross-site request forgery is also known as CSRF, XSRF, one-click attack, session riding, confused deputy, and sea surf.

CSRF attacks are effective in a number of situations, including:
    * The victim has an active session on the target site.
    * The victim is authenticated via HTTP auth on the target site.
    * The victim is on the same local network as the target site.

CSRF has primarily been used to perform an action against a target site using the victim's privileges, but recent techniques have been discovered to disclose information by gaining access to the response. The risk of information disclosure is dramatically increased when the target site is vulnerable to XSS, because XSS can be used as a platform for CSRF, allowing the attack to operate within the bounds of the same-origin policy.

* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/forgot-password">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "email" ].`
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/login">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "email" "password" ].`
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `<form method="post" action="/request-access">`
  * Other Info: `No known Anti-CSRF token [anticsrf, CSRFToken, __RequestVerificationToken, csrfmiddlewaretoken, authenticity_token, OWASP_CSRFTOKEN, anoncsrf, csrf_token, _csrf, _csrfSecret, __csrf_magic, CSRF, _token, _csrf_token, _csrfToken] was found in the following HTML form: [Form 1: "requested_email" "requested_name" "requested_team_name" ].`


Instances: 3

### Solution

Phase: Architecture and Design
Use a vetted library or framework that does not allow this weakness to occur or provides constructs that make this weakness easier to avoid.
For example, use anti-CSRF packages such as the OWASP CSRFGuard.

Phase: Implementation
Ensure that your application is free of cross-site scripting issues, because most CSRF defenses can be bypassed using attacker-controlled script.

Phase: Architecture and Design
Generate a unique nonce for each form, place the nonce into the form, and verify the nonce upon receipt of the form. Be sure that the nonce is not predictable (CWE-330).
Note that this can be bypassed using XSS.

Identify especially dangerous operations. When the user performs a dangerous operation, send a separate confirmation request to ensure that the user intended to perform that operation.
Note that this can be bypassed using XSS.

Use the ESAPI Session Management control.
This control includes a component for CSRF.

Do not use the GET method for any request that triggers a state change.

Phase: Implementation
Check the HTTP Referer header to see if the request originated from an expected page. This could break legitimate functionality, because users or proxies may have disabled sending the Referer for privacy reasons.

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html ](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
* [ https://cwe.mitre.org/data/definitions/352.html ](https://cwe.mitre.org/data/definitions/352.html)


#### CWE Id: [ 352 ](https://cwe.mitre.org/data/definitions/352.html)


#### WASC Id: 9

#### Source ID: 3

### [ Cookie No HttpOnly Flag ](https://www.zaproxy.org/docs/alerts/10010/)



##### Low (Medium)

### Description

A cookie has been set without the HttpOnly flag, which means that the cookie can be accessed by JavaScript. If a malicious script can be run on this page then the cookie will be accessible and can be transmitted to another site. If this is a session cookie then session hijacking may be possible.

* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``
* URL: https://openscribe.co.uk/sitemap.xml
  * Node Name: `https://openscribe.co.uk/sitemap.xml`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``

Instances: Systemic


### Solution

Ensure that the HttpOnly flag is set for all cookies.

### Reference


* [ https://owasp.org/www-community/HttpOnly ](https://owasp.org/www-community/HttpOnly)


#### CWE Id: [ 1004 ](https://cwe.mitre.org/data/definitions/1004.html)


#### WASC Id: 13

#### Source ID: 3

### [ Cross-Origin-Embedder-Policy Header Missing or Invalid ](https://www.zaproxy.org/docs/alerts/90004/)



##### Low (Medium)

### Description

Cross-Origin-Embedder-Policy header is a response header that prevents a document from loading any cross-origin resources that don't explicitly grant the document permission (using CORP or CORS).

* URL: https://openscribe.co.uk
  * Node Name: `https://openscribe.co.uk`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: `Cross-Origin-Embedder-Policy`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``


Instances: 5

### Solution

Ensure that the application/web server sets the Cross-Origin-Embedder-Policy header appropriately, and that it sets the Cross-Origin-Embedder-Policy header to 'require-corp' for documents.
If possible, ensure that the end user uses a standards-compliant and modern web browser that supports the Cross-Origin-Embedder-Policy header (https://caniuse.com/mdn-http_headers_cross-origin-embedder-policy).

### Reference


* [ https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy)


#### CWE Id: [ 693 ](https://cwe.mitre.org/data/definitions/693.html)


#### WASC Id: 14

#### Source ID: 3

### [ Permissions Policy Header Not Set ](https://www.zaproxy.org/docs/alerts/10063/)



##### Low (Medium)

### Description

Permissions Policy Header is an added layer of security that helps to restrict from unauthorized access or usage of browser/client features by web resources. This policy ensures the user privacy by limiting or specifying the features of the browsers can be used by the web resources. Permissions Policy provides a set of standard HTTP headers that allow website owners to limit which features of browsers can be used by the page such as camera, microphone, location, full screen etc.

* URL: https://openscribe.co.uk
  * Node Name: `https://openscribe.co.uk`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``

Instances: Systemic


### Solution

Ensure that your web server, application server, load balancer, etc. is configured to set the Permissions-Policy header.

### Reference


* [ https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy)
* [ https://developer.chrome.com/blog/feature-policy/ ](https://developer.chrome.com/blog/feature-policy/)
* [ https://scotthelme.co.uk/a-new-security-header-feature-policy/ ](https://scotthelme.co.uk/a-new-security-header-feature-policy/)
* [ https://w3c.github.io/webappsec-feature-policy/ ](https://w3c.github.io/webappsec-feature-policy/)
* [ https://www.smashingmagazine.com/2018/12/feature-policy/ ](https://www.smashingmagazine.com/2018/12/feature-policy/)


#### CWE Id: [ 693 ](https://cwe.mitre.org/data/definitions/693.html)


#### WASC Id: 15

#### Source ID: 3

### [ Strict-Transport-Security Multiple Header Entries (Non-compliant with Spec) ](https://www.zaproxy.org/docs/alerts/10035/)



##### Low (High)

### Description

HTTP Strict Transport Security (HSTS) headers were found, a response with multiple HSTS header entries is not compliant with the specification (RFC 6797) and only the first HSTS header will be processed others will be ignored by user agents or the HSTS policy may be incorrectly applied.
HTTP Strict Transport Security (HSTS) is a web security policy mechanism whereby a web server declares that complying user agents (such as a web browser) are to interact with it using only secure HTTPS connections (i.e. HTTP layered over TLS/SSL).

* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/sitemap.xml
  * Node Name: `https://openscribe.co.uk/sitemap.xml`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password ()(email)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login ()(email,password)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access ()(request_details,requested_email,requested_name,requested_team_name)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: ``

Instances: Systemic


### Solution

Ensure that only one component in your stack: code, web server, application server, load balancer, etc. is configured to set or add a HTTP Strict-Transport-Security (HSTS) header.

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc6797#section-8.1 ](https://datatracker.ietf.org/doc/html/rfc6797#section-8.1)


#### CWE Id: [ 319 ](https://cwe.mitre.org/data/definitions/319.html)


#### WASC Id: 15

#### Source ID: 3

### [ Authentication Request Identified ](https://www.zaproxy.org/docs/alerts/10111/)



##### Informational (High)

### Description

The given request has been identified as an authentication request. The 'Other Info' field contains a set of key=value lines which identify any relevant fields. If the request is in a context which has an Authentication Method set to "Auto-Detect" then this rule will change the authentication to match the request identified.

* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login ()(email,password)`
  * Method: `POST`
  * Parameter: `email`
  * Attack: ``
  * Evidence: `password`
  * Other Info: `userParam=email
userValue=zaproxy@example.com
passwordParam=password
referer=https://openscribe.co.uk/login`


Instances: 1

### Solution

This is an informational alert rather than a vulnerability and so there is nothing to fix.

### Reference


* [ https://www.zaproxy.org/docs/desktop/addons/authentication-helper/auth-req-id/ ](https://www.zaproxy.org/docs/desktop/addons/authentication-helper/auth-req-id/)



#### Source ID: 3

### [ Non-Storable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are not storable by caching components such as proxy servers. If the response does not contain sensitive, personal or user-specific information, it may benefit from being stored and cached, to improve performance.

* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/sitemap.xml
  * Node Name: `https://openscribe.co.uk/sitemap.xml`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password ()(email)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `403`
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login ()(email,password)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `403`
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access ()(request_details,requested_email,requested_name,requested_team_name)`
  * Method: `POST`
  * Parameter: ``
  * Attack: ``
  * Evidence: `403`
  * Other Info: ``


Instances: 5

### Solution

The content may be marked as storable by ensuring that the following conditions are satisfied:
The request method must be understood by the cache and defined as being cacheable ("GET", "HEAD", and "POST" are currently defined as cacheable)
The response status code must be understood by the cache (one of the 1XX, 2XX, 3XX, 4XX, or 5XX response classes are generally understood)
The "no-store" cache directive must not appear in the request or response header fields
For caching by "shared" caches such as "proxy" caches, the "private" response directive must not appear in the response
For caching by "shared" caches such as "proxy" caches, the "Authorization" header field must not appear in the request, unless the response explicitly allows it (using one of the "must-revalidate", "public", or "s-maxage" Cache-Control response directives)
In addition to the conditions above, at least one of the following conditions must also be satisfied by the response:
It must contain an "Expires" header field
It must contain a "max-age" response directive
For "shared" caches such as "proxy" caches, it must contain a "s-maxage" response directive
It must contain a "Cache Control Extension" that allows it to be cached
It must have a status code that is defined as cacheable by default (200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501).

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3

### [ Re-examine Cache-control Directives ](https://www.zaproxy.org/docs/alerts/10015/)



##### Informational (Low)

### Description

The cache-control header has not been set properly or is missing, allowing the browser and proxies to cache content. For static assets like css, js, or image files this might be intended, however, the resources should be reviewed to ensure that no sensitive content will be cached.

* URL: https://openscribe.co.uk
  * Node Name: `https://openscribe.co.uk`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: ``
  * Other Info: ``


Instances: 5

### Solution

For secure content, ensure the cache-control HTTP header is set with "no-cache, no-store, must-revalidate". If an asset should be cached consider setting the directives "public, max-age, immutable".

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#web-content-caching ](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#web-content-caching)
* [ https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control)
* [ https://grayduck.mn/2021/09/13/cache-control-recommendations/ ](https://grayduck.mn/2021/09/13/cache-control-recommendations/)


#### CWE Id: [ 525 ](https://cwe.mitre.org/data/definitions/525.html)


#### WASC Id: 13

#### Source ID: 3

### [ Session Management Response Identified ](https://www.zaproxy.org/docs/alerts/10112/)



##### Informational (Medium)

### Description

The given response has been identified as containing a session management token. The 'Other Info' field contains a set of header tokens that can be used in the Header Based Session Management Method. If the request is in a context which has a Session Management Method set to "Auto-Detect" then this rule will change the session management to use the tokens identified.

* URL: https://openscribe.co.uk
  * Node Name: `https://openscribe.co.uk`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/sitemap.xml
  * Node Name: `https://openscribe.co.uk/sitemap.xml`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/static/vendor/lucide/1.8.0/lucide.min.js
  * Node Name: `https://openscribe.co.uk/static/vendor/lucide/1.8.0/lucide.min.js`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `openscribe_csrf`
  * Other Info: `cookie:openscribe_csrf`


Instances: 8

### Solution

This is an informational alert rather than a vulnerability and so there is nothing to fix.

### Reference


* [ https://www.zaproxy.org/docs/desktop/addons/authentication-helper/session-mgmt-id/ ](https://www.zaproxy.org/docs/desktop/addons/authentication-helper/session-mgmt-id/)



#### Source ID: 3

### [ Storable and Cacheable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are storable by caching components such as proxy servers, and may be retrieved directly from the cache, rather than from the origin server by the caching servers, in response to similar requests from other users. If the response data is sensitive, personal or user-specific, this may result in sensitive information being leaked. In some cases, this may even result in a user gaining complete control of the session of another user, depending on the configuration of the caching components in use in their environment. This is primarily an issue where "shared" caching servers such as "proxy" caches are configured on the local network. This configuration is typically found in corporate or educational environments, for instance.

* URL: https://openscribe.co.uk
  * Node Name: `https://openscribe.co.uk`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`
* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: ``
  * Other Info: `In the absence of an explicitly specified caching lifetime directive in the response, a liberal lifetime heuristic of 1 year was assumed. This is permitted by rfc7234.`

Instances: Systemic


### Solution

Validate that the response does not contain sensitive, personal or user-specific information. If it does, consider the use of the following HTTP response headers, to limit, or prevent the content being stored and retrieved from the cache by another user:
Cache-Control: no-cache, no-store, must-revalidate, private
Pragma: no-cache
Expires: 0
This configuration directs both HTTP 1.0 and HTTP 1.1 compliant caching servers to not store the response, and to not retrieve the response (without validation) from the cache, in response to a similar request.

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html ](https://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html)


#### CWE Id: [ 524 ](https://cwe.mitre.org/data/definitions/524.html)


#### WASC Id: 13

#### Source ID: 3


