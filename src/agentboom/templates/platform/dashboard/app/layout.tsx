import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "agentboom dashboard",
  description: "The agent's mini-apps, rendered from what they declare.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
