import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import HeaderNav from "@/components/HeaderNav";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Personal Health Intelligence",
  description: "Demo prototype. Synthetic data only. Not medical advice.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <AuthProvider>
          <header className="bg-teal-700 text-white">
            <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-4">
              <h1 className="text-lg font-semibold">Personal Health Intelligence</h1>
              <HeaderNav />
            </div>
          </header>
          <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">{children}</main>
          <footer className="mx-auto w-full max-w-5xl px-4 pb-8 text-xs text-slate-500">
            Demo prototype. Synthetic data only. Not medical advice.
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
