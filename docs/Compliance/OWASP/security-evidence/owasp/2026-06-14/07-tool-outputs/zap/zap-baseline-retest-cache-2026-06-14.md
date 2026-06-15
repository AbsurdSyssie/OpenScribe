# ZAP Scanning Report

ZAP by [Checkmarx](https://checkmarx.com/).


## Summary of Alerts

| Risk Level | Number of Alerts |
| --- | --- |
| High | 0 |
| Medium | 0 |
| Low | 1 |
| Informational | 7 |






## Alerts

| Name | Risk Level | Number of Instances |
| --- | --- | --- |
| Cookie No HttpOnly Flag | Low | Systemic |
| Authentication Request Identified | Informational | 1 |
| Non-Storable Content | Informational | Systemic |
| Re-examine Cache-control Directives | Informational | Systemic |
| Retrieved from Cache | Informational | 2 |
| Session Management Response Identified | Informational | 11 |
| Storable and Cacheable Content | Informational | 2 |
| User Controllable HTML Element Attribute (Potential XSS) | Informational | 1 |




## Alert Detail



### [ Cookie No HttpOnly Flag ](https://www.zaproxy.org/docs/alerts/10010/)



##### Low (Medium)

### Description

A cookie has been set without the HttpOnly flag, which means that the cookie can be accessed by JavaScript. If a malicious script can be run on this page then the cookie will be accessible and can be transmitted to another site. If this is a session cookie then session hijacking may be possible.

* URL: https://openscribe.co.uk/admin
  * Node Name: `https://openscribe.co.uk/admin`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``
* URL: https://openscribe.co.uk/api/
  * Node Name: `https://openscribe.co.uk/api/`
  * Method: `GET`
  * Parameter: `openscribe_csrf`
  * Attack: ``
  * Evidence: `Set-Cookie: openscribe_csrf`
  * Other Info: ``
* URL: https://openscribe.co.uk/transcribe
  * Node Name: `https://openscribe.co.uk/transcribe`
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

### [ Authentication Request Identified ](https://www.zaproxy.org/docs/alerts/10111/)



##### Informational (High)

### Description

The given request has been identified as an authentication request. The 'Other Info' field contains a set of key=value lines which identify any relevant fields. If the request is in a context which has an Authentication Method set to "Auto-Detect" then this rule will change the authentication to match the request identified.

* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login ()(_csrf_token,email,password)`
  * Method: `POST`
  * Parameter: `email`
  * Attack: ``
  * Evidence: `password`
  * Other Info: `userParam=email
userValue=zaproxy@example.com
passwordParam=password
referer=https://openscribe.co.uk/login
csrfToken=_csrf_token`


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

* URL: https://openscribe.co.uk/account
  * Node Name: `https://openscribe.co.uk/account`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/admin
  * Node Name: `https://openscribe.co.uk/admin`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/home
  * Node Name: `https://openscribe.co.uk/home`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/settings
  * Node Name: `https://openscribe.co.uk/settings`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``
* URL: https://openscribe.co.uk/transcribe
  * Node Name: `https://openscribe.co.uk/transcribe`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `303`
  * Other Info: ``

Instances: Systemic


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
  * Evidence: `no-store`
  * Other Info: ``
* URL: https://openscribe.co.uk/
  * Node Name: `https://openscribe.co.uk/`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: https://openscribe.co.uk/login
  * Node Name: `https://openscribe.co.uk/login`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: https://openscribe.co.uk/request-access
  * Node Name: `https://openscribe.co.uk/request-access`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: `no-store`
  * Other Info: ``
* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: `cache-control`
  * Attack: ``
  * Evidence: `max-age=14400`
  * Other Info: ``

Instances: Systemic


### Solution

For secure content, ensure the cache-control HTTP header is set with "no-cache, no-store, must-revalidate". If an asset should be cached consider setting the directives "public, max-age, immutable".

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#web-content-caching ](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#web-content-caching)
* [ https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control ](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control)
* [ https://grayduck.mn/2021/09/13/cache-control-recommendations/ ](https://grayduck.mn/2021/09/13/cache-control-recommendations/)


#### CWE Id: [ 525 ](https://cwe.mitre.org/data/definitions/525.html)


#### WASC Id: 13

#### Source ID: 3

### [ Retrieved from Cache ](https://www.zaproxy.org/docs/alerts/10050/)



##### Informational (Medium)

### Description

The content was retrieved from a shared cache. If the response data is sensitive, personal or user-specific, this may result in sensitive information being leaked. In some cases, this may even result in a user gaining complete control of the session of another user, depending on the configuration of the caching components in use in their environment. This is primarily an issue where caching servers such as "proxy" caches are configured on the local network. This configuration is typically found in corporate or educational environments, for instance.

* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `Age: 1454`
  * Other Info: `The presence of the 'Age' header indicates that a HTTP/1.1 compliant caching server is in use.`
* URL: https://openscribe.co.uk/static/vendor/lucide/1.8.0/lucide.min.js
  * Node Name: `https://openscribe.co.uk/static/vendor/lucide/1.8.0/lucide.min.js`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `Age: 2355`
  * Other Info: `The presence of the 'Age' header indicates that a HTTP/1.1 compliant caching server is in use.`


Instances: 2

### Solution

Validate that the response does not contain sensitive, personal or user-specific information. If it does, consider the use of the following HTTP response headers, to limit, or prevent the content being stored and retrieved from the cache by another user:
Cache-Control: no-cache, no-store, must-revalidate, private
Pragma: no-cache
Expires: 0
This configuration directs both HTTP 1.0 and HTTP 1.1 compliant caching servers to not store the response, and to not retrieve the response (without validation) from the cache, in response to a similar request.

### Reference


* [ https://datatracker.ietf.org/doc/html/rfc7234 ](https://datatracker.ietf.org/doc/html/rfc7234)
* [ https://datatracker.ietf.org/doc/html/rfc7231 ](https://datatracker.ietf.org/doc/html/rfc7231)
* [ https://www.rfc-editor.org/rfc/rfc9110.html ](https://www.rfc-editor.org/rfc/rfc9110.html)


#### CWE Id: [ 525 ](https://cwe.mitre.org/data/definitions/525.html)


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
* URL: https://openscribe.co.uk/account
  * Node Name: `https://openscribe.co.uk/account`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/admin
  * Node Name: `https://openscribe.co.uk/admin`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/api/
  * Node Name: `https://openscribe.co.uk/api/`
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
* URL: https://openscribe.co.uk/home
  * Node Name: `https://openscribe.co.uk/home`
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
* URL: https://openscribe.co.uk/settings
  * Node Name: `https://openscribe.co.uk/settings`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`
* URL: https://openscribe.co.uk/transcribe
  * Node Name: `https://openscribe.co.uk/transcribe`
  * Method: `GET`
  * Parameter: `openscribe_csrf_anon`
  * Attack: ``
  * Evidence: `openscribe_csrf_anon`
  * Other Info: `cookie:openscribe_csrf_anon
cookie:openscribe_csrf`


Instances: 11

### Solution

This is an informational alert rather than a vulnerability and so there is nothing to fix.

### Reference


* [ https://www.zaproxy.org/docs/desktop/addons/authentication-helper/session-mgmt-id/ ](https://www.zaproxy.org/docs/desktop/addons/authentication-helper/session-mgmt-id/)



#### Source ID: 3

### [ Storable and Cacheable Content ](https://www.zaproxy.org/docs/alerts/10049/)



##### Informational (Medium)

### Description

The response contents are storable by caching components such as proxy servers, and may be retrieved directly from the cache, rather than from the origin server by the caching servers, in response to similar requests from other users. If the response data is sensitive, personal or user-specific, this may result in sensitive information being leaked. In some cases, this may even result in a user gaining complete control of the session of another user, depending on the configuration of the caching components in use in their environment. This is primarily an issue where "shared" caching servers such as "proxy" caches are configured on the local network. This configuration is typically found in corporate or educational environments, for instance.

* URL: https://openscribe.co.uk/robots.txt
  * Node Name: `https://openscribe.co.uk/robots.txt`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `max-age=14400`
  * Other Info: ``
* URL: https://openscribe.co.uk/sitemap.xml
  * Node Name: `https://openscribe.co.uk/sitemap.xml`
  * Method: `GET`
  * Parameter: ``
  * Attack: ``
  * Evidence: `max-age=3600`
  * Other Info: ``


Instances: 2

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

### [ User Controllable HTML Element Attribute (Potential XSS) ](https://www.zaproxy.org/docs/alerts/10031/)



##### Informational (Low)

### Description

This check looks at user-supplied input in query string parameters and POST data to identify where certain HTML attribute values might be controlled. This provides hot-spot detection for XSS (cross-site scripting) that will require further review by a security analyst to determine exploitability.

* URL: https://openscribe.co.uk/forgot-password
  * Node Name: `https://openscribe.co.uk/forgot-password ()(_csrf_token,email)`
  * Method: `POST`
  * Parameter: `_csrf_token`
  * Attack: ``
  * Evidence: ``
  * Other Info: `User-controlled HTML attribute values were found. Try injecting special characters to see if XSS might be possible. The page at the following URL:

https://openscribe.co.uk/forgot-password

appears to include user input in:
a(n) [input] tag [value] attribute

The user input found was:
_csrf_token=_8Jyo64KFJM1fSJ5TIYv8MDO3FK1ocMv.02350c11ada93f06e09172fdbe0b7b689179f74369df1c63a3ff9de9c5bb5608

The user-controlled value was:
_8jyo64kfjm1fsj5tiyv8mdo3fk1ocmv.02350c11ada93f06e09172fdbe0b7b689179f74369df1c63a3ff9de9c5bb5608`


Instances: 1

### Solution

Validate all input and sanitize output it before writing to any HTML attributes.

### Reference


* [ https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html ](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)


#### CWE Id: [ 20 ](https://cwe.mitre.org/data/definitions/20.html)


#### WASC Id: 20

#### Source ID: 3


