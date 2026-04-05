import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Academic Intel — Stanford UI",
  description: "Paper cards from final_report.jsonl",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-paper-bg antialiased text-slate-900">
        {children}
      </body>
    </html>
  );
}
