import Image from "next/image";
import Link from "next/link";

const screenshots = [
  { file: "upload.png", title: "1. Upload and inspect", height: 767, text: "Upload both downloaded files above. Click Inspect collection, continue, and choose the whole collection so login and creation run before the requests that need them." },
  { file: "run-health.png", title: "2. Run twice and review", height: 827, text: "Review the destination, then click Run collection twice. Both runs should have seven successful requests. Resolve any errors before opening Candidates and choosing Auto-correlate all reused values." },
  { file: "validation.png", title: "3. Generate and validate", height: 1761, text: "Open Generate and keep one thread and one loop. Generate JMX, then enter password123 in the password field for local JMeter validation. The field clears after validation. Check the results, then download the JMX and manifest." },
] as const;

export function SampleWalkthrough() {
  return (
    <section aria-labelledby="sample-heading" className="mt-10 space-y-5 border-t border-line pt-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-accent-soft-ink">Try a complete example</p>
          <h2 id="sample-heading" className="text-xl font-semibold">Your first booking test</h2>
          <p className="text-sm leading-relaxed text-fg-muted">Download these two files and upload them above. This public demo logs in, creates a booking, reads it, updates it, searches, and deletes it. No personal account or API key is needed.</p>
        </div>
        <Link href="/guide" className="btn-ghost">Read the full user guide</Link>
      </div>
      <div className="flex flex-wrap gap-3">
        <a className="btn" href="/samples/booking-flow.postman_collection.json" download>Download sample collection</a>
        <a className="btn-secondary" href="/samples/booking-flow.postman_environment.json" download>Download sample environment</a>
      </div>
      <p className="text-sm text-fg-muted">These are Postman input files. The application creates both Newman reports for you. Running twice sends real requests to the public demo; JMeter validation sends them again. Use one thread and one loop.</p>
      <details className="card p-4">
        <summary className="cursor-pointer text-sm font-medium">Environment values and generated variables</summary>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Values already included in the sample environment</caption>
            <thead><tr className="border-b border-line"><th className="p-2">Variable</th><th className="p-2">Included value</th></tr></thead>
            <tbody>
              {[["baseUrl", "https://restful-booker.herokuapp.com"], ["username", "admin"], ["password", "password123"], ["guestFirstName", "Aarav"], ["guestLastName", "Mehta"]].map(([name, value]) => (
                <tr key={name} className="border-b border-line"><th className="p-2 font-mono font-normal">{name}</th><td className="break-all p-2 font-mono">{value}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-sm text-fg-muted">These credentials belong to the public practice API. The collection scripts obtain <code>token</code> and <code>bookingid</code> during each run; leave them unset. Never substitute a booking ID from a previous run.</p>
      </details>
      <div className="space-y-3">
        <h3 className="text-base font-semibold">Follow the screenshots</h3>
        <p className="text-sm text-fg-muted">Example screenshots from our successful test on 26 September 2026, not your current results. IDs, candidate counts, and appearance may differ. Open a step to see it.</p>
        {screenshots.map((shot) => (
          <details key={shot.file} className="card p-4">
            <summary className="cursor-pointer text-sm font-medium">{shot.title}</summary>
            <p className="my-4 text-sm leading-relaxed text-fg-muted">{shot.text}</p>
            <a href={`/walkthrough/booking/${shot.file}`} target="_blank" rel="noreferrer" className="block rounded-lg focus-visible:ring-2 focus-visible:ring-accent">
              <Image src={`/walkthrough/booking/${shot.file}`} alt={`${shot.title}: recorded Restful-Booker example`} width={1280} height={shot.height} unoptimized className="h-auto w-full rounded-lg border border-line" />
              <span className="mt-2 block text-xs text-accent-soft-ink">Open full-size screenshot in a new tab</span>
            </a>
          </details>
        ))}
      </div>
      <p className="text-sm leading-relaxed text-fg-muted">Our last JMeter replay passed 7 of 7 requests. This shared demo can be unavailable or change; a matching screenshot is not a guarantee of success. Review your own responses and extracted variables. If hosted JMeter validation is unavailable, download the plan and follow the local validation instructions in the user guide.</p>
    </section>
  );
}
