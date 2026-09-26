# Live Postman collection browser acceptance — 26 September 2026

## Result

The local collection-to-JMeter journey passed against the public Restful-Booker demo API after fixing a real correlation defect found during the first attempt. This verifies this collection and the checks described below; it does not certify arbitrary collections or the hosted deployment.

## What was exercised

- Browser: installed Microsoft Edge, automated with Playwright.
- Application: fresh frontend on port 3011 and backend on port 8011, using the Docker Newman runner.
- Input: `samples/booking-flow.postman_collection.json` and `samples/booking-flow.postman_environment.json`.
- Live target: <https://restful-booker.herokuapp.com>, using its public demo credentials.
- Browser actions: upload both files, inspect, review, run twice, inspect health, auto-correlate, generate JMX, supply the runtime password, validate with JMeter, download JMX and manifest.
- Journey: login → create booking → get booking → update with PUT → update with PATCH → search → delete booking.

## Defect found and fixed

The first attempt successfully executed both Newman runs, but JMeter passed only 3 of 7 requests. A short numeric booking ID was filtered out as a common numeric value. The generated plan retained the recorded booking ID, which the capture had already deleted.

The correlation engines now recognize short numeric identifiers when the source field and URL resource agree, for example `bookingid: 43` consumed in `/booking/43`. Matching values and request ordering are still required. An unrelated count or `/status/43` does not qualify under this exception.

Six regression cases cover one-, two-, and three-digit identifiers, unrelated values/resources, and the two-run candidate detector. All failed cases were reproduced before the implementation change.

## Successful retest evidence

| Check | Observed result |
| --- | --- |
| Collection inspection | 7 requests; no missing variables, blocked domains or unsupported-feature warnings |
| Baseline Newman run | 7 successful HTTP responses; zero assertion/business errors |
| Comparison Newman run | 7 successful HTTP responses; zero assertion/business errors |
| Run alignment | 100%; 7 matched; none missing or reordered |
| Automatic rules | 5: bookingid, token, checkin, lastname, firstname |
| Substitutions | 12 downstream occurrences |
| JMeter | Version 5.6.3; 7 passed, 0 failed, 0 assertion failures |
| Extractors | All 5 variables extracted; none missing |
| Browser JavaScript errors | None |
| Credential handling | Password supplied at validation; absent from downloaded JMX |
| Backend regression suite | 563 passed, 3 skipped; two dependency deprecation warnings |
| Static checks | Ruff passed; mypy passed across 78 source files |
| Live browser test | 1 passed |

JMeter responses: login **200**, create **200**, get **200**, PUT **200**, PATCH **200**, search **200**, delete **201**. The delete response is the demo API's observed successful response.

The three two-run candidates and five accepted rules are different counts because “auto-correlate all reused values” also includes unchanged values that were observed in an earlier response and reused later.

JMeter reported an empty-keystore warning, but HTTPS requests and the replay completed successfully. The successful run deleted its created booking. The initial failed replay may have left a booking in this disposable public demo; unrelated demo records were not deleted.

## Artifacts

- `.smoke/browser-booking/evidence.json`: inspection, run health, correlation and real JMeter results.
- `.smoke/browser-booking/booking-flow-restful-booker-validated.jmx`: successful generated plan.
- `.smoke/browser-booking/booking-flow-restful-booker-validated-manifest.json`: dependency and substitution details.
- `.smoke/browser-booking/01-upload.png` through `05-jmeter-validation.png`: browser screenshots.
- `.smoke/browser-booking-before-fix/`: initial failure evidence.

These local `.smoke` artifacts are ignored by Git. Use the **validated** JMX above; the folder may also contain the first attempt's generated JMX.

## Repeat the test

With the updated app running, Docker available, and JMeter configured on the local backend:

```powershell
cd D:\Auto_correlation\frontend
$env:E2E_LIVE_BOOKING = '1'
$env:E2E_BASE_URL = 'http://localhost:3000'
npm run e2e -- e2e/booking-live.spec.ts
```

The test is opt-in because it makes real requests and creates, updates and deletes demo bookings. Without `E2E_LIVE_BOOKING=1`, it is skipped. Restart an already-running backend to load the correlation fix.

## Limits

This was one thread and one loop, not a load test. Passing HTTP responses and extraction checks do not reproduce every Postman business assertion in JMeter. Review the generated rules and add application-specific assertions before load testing. The hosted worker, shared-storage and real AWS/identity-provider acceptance work remains as described in `2026-09-26-functional-audit.md`.
