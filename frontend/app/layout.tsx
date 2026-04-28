import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Value Stream Explorer",
  description: "Retrieve, generate, and compare HCSC value streams from idea cards",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
