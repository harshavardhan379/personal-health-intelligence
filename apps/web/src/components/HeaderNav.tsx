"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

const navItems = [
  { href: "/", label: "Dashboard" },
  { href: "/documents", label: "Documents" },
  { href: "/review", label: "Review" },
  { href: "/timeline", label: "Timeline" },
  { href: "/assistant", label: "Assistant" },
  { href: "/settings", label: "Settings" },
];

export default function HeaderNav() {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <nav className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      {navItems.map((item) => (
        <Link key={item.href} href={item.href} className="hover:underline">
          {item.label}
        </Link>
      ))}
      {user ? (
        <button
          onClick={() => {
            logout();
            router.push("/login");
          }}
          className="hover:underline"
        >
          Log out ({user.email})
        </button>
      ) : (
        <Link href="/login" className="hover:underline">
          Log in
        </Link>
      )}
    </nav>
  );
}
