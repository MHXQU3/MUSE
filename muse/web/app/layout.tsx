import type { Metadata, Viewport } from "next";
import { Bodoni_Moda, Manrope } from "next/font/google";
import "./globals.css";

const display = Bodoni_Moda({ subsets: ["latin", "latin-ext"], variable: "--font-display", display: "swap",
                              style: ["normal", "italic"] });
const body = Manrope({ subsets: ["latin", "latin-ext"], variable: "--font-body", display: "swap" });

export const metadata: Metadata = {
  title: "MUSE",
  description: "Your music. Your world.",
  robots: { index: false, follow: false }, // a private site: keep it out of search engines
};

export const viewport: Viewport = { themeColor: "#1E1621", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB" className={`${display.variable} ${body.variable}`}>
      <body>{children}</body>
    </html>
  );
}
