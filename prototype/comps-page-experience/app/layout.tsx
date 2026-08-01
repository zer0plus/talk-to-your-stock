import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Comps Page Concepts — Prototype",
  description: "Disposable desktop concepts for TalkToYourStock",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
