# HAR import: browser recording to correlated JMX

Upload a browser HAR, strip everything that is not a real API call, review what
is left, and get a correlated JMeter plan.

## Decisions

- One HAR is enough; a second recording of the same flow is optional and uses
  the existing two-run engine.
- Raw HARs are accepted up to `B11_MAX_HAR_BYTES` (200 MB). The plan's file limit
  applies to the cleaned size: the selected requests and their responses.
- The app's own hosts are kept, trackers are always removed, and other
  third-party calls are listed unticked.
- Failed attempts are listed unticked with the reason. Replaying a wrong
  password under load can lock the test account.

## Flow

The upload is stateless, like the Postman flow, so a recording with credentials
in it is never stored between steps.

1. `POST /analyses/har/inspect` reads the file(s) and returns the review list.
2. The user adjusts the ticks.
3. `POST /analyses/har` receives the same file(s) plus `selected` (entry indexes
   of the first file), and creates a normal analysis. A free use is only taken
   here. The UI then runs auto-correlate and opens Generate.

`har_import.to_report` produces a Newman-shaped report, so the normalizer,
correlation engine, auto-correlator and JMX builder are all reused unchanged.

## Classification order

1. **Removed:** `OPTIONS`, static resource types, static extensions, `/assets/`
   and `/static/` paths, GET/HEAD returning image, font, audio, video, CSS or JS,
   tracker hosts, `/cdn-cgi/` paths, and hops of a redirect chain.
2. **Unticked:** other third-party hosts, then HTTP 4xx/5xx, then 2xx bodies with
   a false success flag such as `isLoginSuccessful: false`.
3. **Kept:** everything else, which includes first-party HTML pages because a
   page is often what creates the session.

First-party means the same site name as the recorded pages, so
`nowwalletapi.testewallet.net` matches `demo.testewallet.com`.

## Redirects

Every generated sampler follows redirects, so replaying each hop would double the
calls. The first hop is kept and takes the final hop's response, because that is
what JMeter's extractors see at run time.

## Second recording

The review list belongs to the first file. A second-file entry is used when its
method, host and path match a selected entry, unless it was removed or is a
failed attempt. The existing aligner reports any remaining mismatch.

## Credentials

`Code`, `pin`, `otp` and similar names under login-like paths are externalized
like `password`, so they become `${__P(name,)}` in the plan.

## Run-ready plans

Plans built from a recording (`BuildOptions.browser_recording`) add: run-time login names, live dates in URLs,
JSONPath assertions on recorded success flags, a stop on failed login, a guard before a required property's first
use, a kept `User-Agent`, cookies cleared each iteration, and result listeners. The stop on failed login is a JSR223
assertion because JMeter runs post-processors before assertions, so a Result Status Action Handler can't see them.

## Warnings shown to the user

- Requests with no recorded response body cannot supply correlated values.
- No cookies in the recording: JMeter's Cookie Manager handles them at run time.
- Captcha, MFA or OTP steps cannot be replayed automatically.
