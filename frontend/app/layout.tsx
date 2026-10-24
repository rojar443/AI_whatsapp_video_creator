import type { Metadata, Viewport } from "next";
import Link from "next/link";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "ReelChat",
  description: "Create Instagram Reels and YouTube Shorts by chatting on WhatsApp.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>
          <div className="container">{children}</div>
        </main>
        <footer>
          <div className="container row spread">
            <span>© ReelChat</span>
            <span className="row">
              <Link href="/privacy">Privacy</Link>
              <Link href="/terms">Terms</Link>
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}
