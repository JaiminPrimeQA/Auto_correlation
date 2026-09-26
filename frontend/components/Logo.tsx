/**
 * The Baseline11 logo (wordmark and gauge), inlined so its colours follow the
 * theme: the light theme uses the brand colours as supplied, the dark theme
 * lightens the navy, blue and needle (see --logo-* in globals.css). The
 * viewBox is cropped to the artwork. Path data is copied unchanged from
 * public/brand/baseline11-logo-light.svg.
 */
export function Logo({ className }: { className?: string }) {
  const ink = { fill: "rgb(var(--logo-ink))", stroke: "rgb(var(--logo-ink))", strokeWidth: 9, strokeMiterlimit: 10 };
  const accent = { fill: "rgb(var(--logo-accent))", stroke: "rgb(var(--logo-accent))", strokeWidth: 9, strokeMiterlimit: 10 };
  const needle = { fill: "rgb(var(--logo-needle))" };
  return (
    <svg
      role="img"
      aria-label="Baseline11"
      viewBox="240 1300 10540 2800"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <linearGradient id="b11-logo-gauge" x1="1357.81" y1="2715.18" x2="9587.56" y2="2715.18" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ff0000" />
          <stop offset="0.51" stopColor="#ffd447" />
          <stop offset="1" stopColor="#4db648" />
        </linearGradient>
      </defs>
      <path style={ink} d="M283.73,4070.66V2816.75H835q191.94,0,303.79,86.3t111.83,241.27q0,105.66-58.11,181.39t-163.79,105.67v-14.09q121.51,26.41,187.56,107.43t66,204.28q0,160.27-113.59,251t-317.88,90.7Zm221.9-725.58H796.21q121.51,0,179.63-44T1034,3169q0-89.8-58.12-132.08t-179.63-42.27H505.63Zm0,547.7H827.91q125,0,182.27-43.14t57.24-140q0-95.1-58.12-140.89T827.91,3523H505.63Z" />
      <path style={ink} d="M1586.94,4070.66H1358l567.08-1253.91H2110l567.08,1253.91H2451.64l-461.41-1077.8h61.64Z" />
      <path style={ink} d="M3223,4084.75q-96.89,0-184.92-16.73t-164.66-50.2q-76.62-33.42-134.73-81l75.73-165.54q95.1,70.45,193.72,102.14t216.62,31.7q130.3,0,201.64-45.78t71.33-128.57q0-49.28-31.7-81.88t-95.1-55.48q-63.41-22.87-156.74-44-111-21.13-195.48-51.95t-140.89-73.09q-56.39-42.27-85.41-101.26t-29.06-140q0-110.94,59.88-197.24t168.18-134.73q108.32-48.42,254.48-48.42a832.2,832.2,0,0,1,171.71,17.61q83.63,17.63,154.09,50.18t121.52,80.14l-77.49,165.54q-84.53-68.68-176.11-101.26t-192-32.58q-121.52,0-192,49.31T2985.25,3169q0,51.11,29.05,84.54t89.82,56.35q60.77,22.91,155.86,45.79,109.17,22.91,194.6,52.83t145.29,71.33q59.85,41.4,90.7,98.62t30.82,136.49q0,112.71-59.88,196.36T3489.8,4039.84Q3377.95,4084.75,3223,4084.75Z" />
      <path style={ink} d="M3934.46,4070.66V2816.75h838.28v177.87H4156.36v348.7h581.16V3523H4156.36v369.83h616.38v177.88Z" />
      <path style={accent} d="M4982.29,4070.66V2816.75h227.18V3884h584.69v186.68Z" />
      <path style={accent} d="M5968.5,4070.66V2816.75s73.48,45.85,113.6,44,113.59-44,113.59-44V4070.66Z" />
      <path style={accent} d="M6468.65,4070.66V2816.75h169.07l722.05,938.67-47.55,24.65V2816.75h213.09V4070.66H7354.49L6636,3135.51l45.78-28.18v963.33Z" />
      <path style={accent} d="M7798.26,4070.66V2816.75h838.29v177.87H8020.16v348.7h581.17V3523H8020.16v369.83h616.39v177.88Z" />
      <path style={ink} d="M8907.73,4070.66V3887.5h774.88v183.16Zm273-103.9v-982.7l114.47,24.65-368.07,221.9V3028.09l348.7-211.34h132.08v1150Z" />
      <path style={ink} d="M9964.37,4070.66V3887.5h774.88v183.16Zm273-103.9v-982.7l114.47,24.65-368.06,221.9V3028.09l348.69-211.34h132.08v1150Z" />
      <path style={needle} d="M6610.64,1727.32l-3.51-1.91-514.63,868a96.43,96.43,0,1,0,69.15,41.3Z" />
      <path fill="url(#b11-logo-gauge)" d="M1358,4070.66C1345,4087,2034.05,3303,3230,2629.28c1716.5-967,4348.43-1755.43,6357.52-36.46,0,0-876.64-1003.52-2074.83-1161.61-662-87.34-1170.4-120-1934.56,48.59C5053,1595.63,4348,1852.46,3778.83,2137.63c-211,105.7-370.71,211.84-542.5,310.11C2129.69,3080.85,1358,4070.66,1358,4070.66Z" />
    </svg>
  );
}
