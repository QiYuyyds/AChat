"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/format";

const NAV = [
  { href: "/", label: "总览" },
  { href: "/suites", label: "Suites" },
  { href: "/datasets", label: "Datasets" },
  { href: "/tasks", label: "Task 库" },
  { href: "/compare", label: "A/B 对比" },
  { href: "/settings", label: "Settings" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex w-48 shrink-0 flex-col gap-6 border-r border-border p-4">
      <div>
        <div className="text-lg font-bold tracking-tight">Aeval</div>
        <div className="text-xs text-muted-foreground">Agent 评测台</div>
      </div>
      <nav className="flex flex-col gap-1">
        {NAV.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-lg px-3 py-2 text-sm transition",
                active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted"
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
