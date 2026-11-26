import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "SundaeVale Creamery | Accounts Payable", description: "Supplier invoice management and accounts payable review." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
