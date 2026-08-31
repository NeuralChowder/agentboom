import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "agentboom dashboard",
  description: "The agent's mini-apps, rendered from what they declare.",
};

// Mobile-ready: the page scales with the device.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// Apply the stored theme before first paint so returning users don't see
// a flash of the default. Full machinery lives in app/theme.tsx +
// globals.css; the key must match STORAGE_KEY.
const THEME_BOOT_SCRIPT = `try{var t=localStorage.getItem("agentboom.theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The boot script may set data-theme before hydration.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
