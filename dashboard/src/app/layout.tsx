import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { BotStatusBar } from "@/components/BotStatusBar";
import { SidebarNav } from "@/components/layout/SidebarNav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "MaxTheTrader",
  description: "Prediction market bot control center",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-background text-foreground">
        <div className="min-h-screen flex">
          <aside className="w-72 min-h-screen bg-surface border-r border-card-border flex flex-col shrink-0">
            <div className="px-5 pt-6 pb-4 border-b border-card-border">
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted mb-2">
                Trading Console
              </p>
              <h1 className="text-lg font-bold text-accent tracking-tight">
                MaxTheTrader
              </h1>
              <p className="text-xs text-muted mt-0.5">Prediction Market Bot</p>
            </div>

            <SidebarNav />

            <div className="p-3 border-t border-card-border">
              <BotStatusBar />
            </div>
          </aside>

          <main className="flex-1 p-6 overflow-auto min-w-0">
            <header className="mb-6 flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.14em] text-muted">
                  Dashboard Rebuild Workspace
                </p>
                <h2 className="text-2xl font-semibold tracking-tight">
                  Interactive Trading Surface
                </h2>
              </div>
              <span className="text-xs px-2.5 py-1 rounded border border-accent/30 bg-accent/10 text-accent">
                Foundation Mode
              </span>
            </header>

            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
