import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AuthGate } from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "Baseline11 Auto-Correlate",
  description: "Turn Newman JSON reports into auto-correlated JMeter test plans.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="min-h-screen antialiased">
        <header className="border-b border-edge bg-panel/60 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-lg font-semibold text-brand">Baseline11</span>
              <span className="text-sm text-slate-400">Auto-Correlate</span>
            </div>
            <a
              className="text-xs text-slate-400 hover:text-slate-200"
              href="https://jmeter.apache.org/usermanual/component_reference.html"
              target="_blank"
              rel="noreferrer"
            >
              JMeter 5.6.3 reference
            </a>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6">
          <AuthGate>{children}</AuthGate>
        </main>
      </body>
    </html>
  );
}
