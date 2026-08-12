import { redirect } from "next/navigation";

/**
 * `/members` became `/users` in FG-26 — a wider screen (a directory for
 * everyone, management for owner/admin) with no browser-side password path
 * left. The route is kept as a redirect because it was linked from the home
 * screen and is in people's history; a 404 there reads as a regression.
 */
export default function Page() {
  redirect("/users");
}
