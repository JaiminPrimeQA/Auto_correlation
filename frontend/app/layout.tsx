import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AuthGate } from "@/components/AuthGate";
import { NO_FLASH_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "Baseline11 Auto-Correlate",
  description: "Run a Postman collection twice and get a correlated JMeter test plan.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body className="min-h-[100dvh] antialiased">
        <AuthGate>{children}</AuthGate>
        <footer className="mx-auto max-w-6xl px-4 pb-8 pt-4 text-xs text-fg-subtle sm:px-6">
          <a className="hover:text-fg" href="https://jmeter.apache.org/usermanual/component_reference.html" target="_blank" rel="noreferrer">
            JMeter 5.6.3 reference
          </a>
        </footer>
      </body>
    </html>
  );
}
