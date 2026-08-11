export interface NavLink {
  href: string;
  label: string;
}

/** Primary app navigation, shared by the desktop and mobile nav. */
export const NAV_LINKS: NavLink[] = [
  { href: "/", label: "Query" },
  { href: "/documents", label: "Documents" },
  { href: "/history", label: "History" },
  { href: "/status", label: "Status" },
];
