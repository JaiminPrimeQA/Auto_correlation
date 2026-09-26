# Tester guide: Postman collection to correlated JMeter plan

Open **User guide** in the application header for the same workflow while testing.

## What you provide

- A working Postman collection exported as v2.0 or v2.1 JSON.
- Its Postman environment JSON, if it contains the API address or credentials.
- Any missing values requested by the application.
- Permission to exercise the selected test APIs. The collection runs twice, and JMeter validation sends the requests again.

A hosted tester needs a browser. Docker, Newman and the backend are installed by the administrator. Local developers should follow HOW_TO_RUN_AND_TEST.md.

The collection must already use newly created IDs and tokens in later requests. The tool records and examines that working sequence; it cannot infer a successful journey from repeated authentication failures or nonexistent IDs.

## Start with the included public booking example

On the home page, find **Your first booking test**. Download the sample collection and environment, then upload both files. Expand the environment details or the three screenshot steps whenever you need guidance.

The environment already contains `baseUrl=https://restful-booker.herokuapp.com`, `username=admin`, `password=password123`, `guestFirstName=Aarav` and `guestLastName=Mehta`. These are public demo values. Scripts obtain `token` and `bookingid`; do not fill them manually.

Inspect, continue, select the whole collection and run twice. Review Run health, auto-correlate, then generate with one thread and one loop. For local JMeter validation, supply `password123` as the required `password` property. This seven-request flow creates, updates and deletes a booking. The screenshots show an earlier successful test, not your current results; the shared API may change or be unavailable.

Source files: `samples/booking-flow.postman_collection.json` and `samples/booking-flow.postman_environment.json`. Browser downloads are served from `frontend/public/samples`; the browser acceptance test verifies they match the source files.

## Example: create and reuse a Razorpay test order

1. In Postman, configure the collection with the correct Razorpay **test** key pair and Basic authentication. Check the exact variable names used by your downloaded collection.
2. Ensure the Create Order request succeeds and its post-response script saves `pm.response.json().id` into `order_id`. Later requests must use `{{order_id}}`, not the literal `{order_id}`. Stop the flow if creation fails.
3. Export the collection and environment. In the application, select those files and click **Inspect collection**. Inspection does not execute requests.
4. Fill missing values. Variables listed as set by scripts are created during execution.
5. Select the complete Orders journey or an appropriate folder, including its prerequisites. Review the target domains and unsupported features. Public HTTPS destinations are accepted; private addresses and localhost are blocked.
6. Click **Run collection twice**. The application executes two fresh Newman runs from the same starting inputs and passes their reports to analysis.
7. Check **Run health**. Resolve authentication errors and other blockers first. An empty payments list can be expected for an unpaid test order; inspect the body and business expectation.
8. In **Candidates**, inspect the producer response and consumers. The newly created order ID should be reused in the fetch/update requests. Accept supported candidates or use the broader auto-correlate action, then review its rules.
9. Open **Generate**, keep one thread and one loop, and use **Preview Draft**. Check that the Create Order response supplies the ID and the later requests use its variable.
10. Generate and download the JMX and manifest. A Generated badge confirms structural checks; it does not claim the plan has run.

## Credentials in the generated plan

By default, recognized credentials become properties such as `${__P(Authorization,)}`. They are external inputs, whereas an order ID is extracted from a response during the test.

For the application's Authorization field, supply the complete working header, for example `Basic ENCODED_KEY_PAIR` or `Bearer TOKEN`, matching the original request. Do not paste a bare key into a field expecting a complete Authorization header.

For local application validation, supply all required properties and click **Validate with JMeter**. The application rejects missing values before executing. Credentials travel through a temporary properties file and input fields clear after the attempt.

For a downloaded plan, create a private `runtime.properties` file with exactly the property names listed in the manifest:

```properties
Authorization=Basic REPLACE_WITH_YOUR_ENCODED_TEST_KEY_PAIR
```

Run in JMeter CLI:

```powershell
jmeter -n -t plan.jmx -q runtime.properties -l results.jtl
```

Or launch the GUI with the same properties, then open the JMX:

```powershell
jmeter -q runtime.properties
```

JMeter uses Java properties syntax; backslashes and newlines in manually written values need escaping. Keep the properties file private. The application handles escaping for its own validation runs.

Official reference: [JMeter command-line options](https://jmeter.apache.org/usermanual/get-started.html#options).

## What results mean

| Result | Meaning and next action |
|---|---|
| Run health blocked | Fix the API flow and capture two healthy runs before automatic correlation. |
| No candidates | No supported changing response value was proven to be reused. Do not invent extractors to increase the count. |
| Auto-correlate rules | May include unchanged values reused within one capture. Review the source, consumers and warnings. |
| Generated JMX | Structural checks passed. Supply credentials and execute it next. |
| Validated JMX | JMeter ran and met the configured sampler and extraction checks. Business correctness and performance capacity still require appropriate assertions and load testing. |
| `__NOT_FOUND__` in extractor Default Values | A failure marker used if extraction fails. It is normal configuration before execution, not a credential to enter. |
| `__NOT_FOUND__` at runtime | The producer failed, the response changed, or the extractor did not match. Inspect that response. |
| 401/403 | Check credentials, header format, permissions, expiry, and test/live mode. |

## Current boundaries

- Existing Newman report upload remains available through `POST /api/v1/analyses`; the current start page accepts collections and optional environments.
- Arbitrary JSON is not a supported collection format.
- Hosted JMeter validation is currently disabled because a production isolation worker for JMeter is not implemented. Download and validate locally.
- Postman business assertions and arbitrary scripts are not automatically translated into equivalent JMeter checks. Review these before a performance test.
- Results expire after the configured session lifetime (normally 30 minutes). Download your artifacts before expiry. In-memory analyses are also lost on backend restart.
- Recognized secrets are externalized by default. Selecting **embed static secrets** includes captured credentials in the JMX; review files before sharing.

For sharing a test result, provide the credential-free JMX, manifest, validation summary, collection/scenario name and environment name. Keep credentials out of the shared files.
