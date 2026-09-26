import Link from "next/link";

export default function GuidePage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-10 text-sm leading-relaxed">
      <Link href="/" className="text-accent-soft-ink underline">Open Auto-Correlate</Link>
      <h1 className="text-3xl font-semibold">From Postman to a tested JMeter plan</h1>
      <p>Use this tool to capture a value from an API response and reuse it in later requests. For example, create an order, extract its new ID, then fetch or update that same order.</p>
      <section className="card space-y-3 p-5">
        <h2 className="text-lg font-semibold">1. Prepare a working test flow</h2>
        <p>Export a Postman collection as v2.0 or v2.1 JSON. Export its environment if it contains your API address or credentials. Arbitrary JSON and Newman reports are not collection files; existing Newman reports use the separate analysis API.</p>
        <p>The requests must already work in sequence. Postman scripts should save newly created IDs and tokens and use them in later requests. A failed login or a literal placeholder such as {'{order_id}'} cannot provide evidence for correlation.</p>
        <p>Use a test account and a test environment. Running the collection twice sends real API requests twice, including creates, updates and deletes. Testers using a hosted installation need only a browser; its administrator installs the execution tools.</p>
      </section>
      <section className="card space-y-3 p-5">
        <h2 className="text-lg font-semibold">2. Upload, supply values, and run</h2>
        <ol className="list-decimal space-y-2 pl-5">
          <li>Choose your collection and optional environment. Click <strong>Inspect collection</strong>. Inspection reads the files without executing their requests.</li>
          <li>Fill in missing variables. Credentials use hidden fields. Variables listed as set by a script are created during execution.</li>
          <li>Choose the whole collection or a folder containing the full journey, including login or creation requests needed later. Review domains and unsupported features.</li>
          <li>Click <strong>Run collection twice</strong>. Each run starts with the same supplied values in a fresh runner. Public HTTPS targets are supported; localhost and private network targets are blocked.</li>
          <li>Wait for Run health. Resolve blockers before automatic correlation. A 2xx response can still contain an application error; inspect warnings and response bodies.</li>
        </ol>
      </section>
      <section className="card space-y-3 p-5">
        <h2 className="text-lg font-semibold">3. Review the evidence and generate</h2>
        <p>In Candidates, open Evidence to check the producer response and all later consumers. High-confidence candidates use both captures. The broader auto-correlate action can also wire values reused within one capture, including unchanged values; review these rules before relying on them.</p>
        <p>Use the dependency graph and Preview Draft to check where each value comes from and where it is reused. Zero candidates can be correct when no changing response value is reused.</p>
        <p>Open Generate, keep <strong>one thread and one loop</strong> for the first check, and click Generate JMX. Download the JMX and manifest. Generated means structural checks passed; it does not mean the requests were executed successfully.</p>
      </section>
      <section className="card space-y-3 p-5">
        <h2 className="text-lg font-semibold">4. Supply credentials and validate</h2>
        <p>A property such as <code>{'${__P(Authorization,)}'}</code> requires an external value at runtime. It is separate from an ID extracted from a response. For Authorization, use the complete header: <code>Basic ENCODED_KEY_PAIR</code> or <code>Bearer TOKEN</code>, matching the successful Postman request.</p>
        <p>For local validation, enter every required credential and click Validate with JMeter. Validation sends the requests again. Fields clear after the attempt. Hosted JMeter execution currently requires an isolated worker that is not implemented; download the plan and validate locally.</p>
        <p>To run a downloaded plan, put the required properties in a private <code>runtime.properties</code> file, using the exact names listed in the manifest, then run:</p>
        <pre className="overflow-x-auto rounded bg-surface2 p-3"><code>jmeter -n -t plan.jmx -q runtime.properties -l results.jtl</code></pre>
        <p>For the JMeter GUI, launch <code>jmeter -q runtime.properties</code>, then open the plan. Do not share the credentials file. Avoid embedding static secrets in any JMX you intend to share.</p>
        <p><code>__NOT_FOUND__</code> is an extraction failure marker, not a value to enter. An extractor default is normal before execution; if it remains at runtime, check that the producer succeeded and its response matches the extractor.</p>
      </section>
      <section className="card space-y-3 p-5">
        <h2 className="text-lg font-semibold">If something fails</h2>
        <ul className="list-disc space-y-2 pl-5">
          <li><strong>401/403:</strong> check the credential value, auth type, permissions, and test/live account mode. Confirm the same request works in Postman.</li>
          <li><strong>Missing ID:</strong> check the creation response and its script. Later requests must use the ID returned by that run.</li>
          <li><strong>Empty payments list:</strong> an unpaid order may legitimately have no payments. Review the business expectation before treating an empty result as an error.</li>
          <li><strong>Execution disabled:</strong> ask the administrator to enable the runner and check its health.</li>
          <li><strong>Expired analysis:</strong> start a fresh analysis. Sessions expire after a configured lifetime, normally 30 minutes; local results are also lost on server restart.</li>
        </ul>
      </section>
      <p>JMeter execution checks HTTP sampler results and extracted variables. It does not automatically recreate every Postman script or business assertion, and passing validation is not proof of load capacity. Review business checks before using the plan for performance testing.</p>
    </main>
  );
}
