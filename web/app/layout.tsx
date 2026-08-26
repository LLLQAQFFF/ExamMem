import type { Metadata } from "next";
import { Geist, Lora } from "next/font/google";
import "./globals.css";
import ThemeScript from "@/components/ThemeScript";
import ToastViewport from "@/components/common/ToastViewport";
import { AppShellProvider } from "@/context/AppShellContext";
import { I18nClientBridge } from "@/i18n/I18nClientBridge";

// Geist matches the public site (deeptutor.info) and stays crisp at the
// small UI sizes the composer/toolbars use, unlike the rounder Jakarta.
const fontSans = Geist({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const fontSerif = Lora({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-serif",
});

export const metadata: Metadata = {
  title: "ExamMem",
  description: "Textbook-grounded intelligent learning companion",
  icons: {
    icon: [
      { url: "/exammem-favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/exammem-favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/exammem-apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
      className={`${fontSans.variable} ${fontSerif.variable}`}
    >
      <head>
        <ThemeScript />
      </head>
      <body
        className="font-sans bg-[var(--background)] text-[var(--foreground)]"
        suppressHydrationWarning
      >
        <AppShellProvider>
          <I18nClientBridge>{children}</I18nClientBridge>
          <ToastViewport />
        </AppShellProvider>
      </body>
    </html>
  );
}
