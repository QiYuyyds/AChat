import { Sidebar } from "@/components/sidebar";
import { Providers } from "@/app/providers";

import "./globals.css";

export const metadata = {
  title: "Aeval Dashboard",
  description: "Agent 评测可视化台 (Aeval)",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        <Providers>
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="min-w-0 flex-1 p-6">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
