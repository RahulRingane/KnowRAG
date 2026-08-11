import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Providers } from "@/app/providers";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "KnowRAG",
    template: "%s | KnowRAG",
  },
  description:
    "Fact-checking RAG: answers questions strictly from retrieved evidence and marks every claim with what that evidence does or does not establish.",
};

/**
 * Root document shell. Deliberately has no `<main>` and no nav — those
 * belong to `(app)/layout.tsx` (`AppShell`) so that `(auth)/*` routes
 * (login/register, WS-C) can render their own minimal, unauthenticated
 * shell without inheriting app chrome. Every route still gets exactly one
 * `<main>` landmark; it is just supplied one level down.
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
