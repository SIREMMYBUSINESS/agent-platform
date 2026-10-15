import type { LinksFunction, MetaFunction } from "@remix-run/node";
import { Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration } from "@remix-run/react";
import stylesHref from "./styles.css?url";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: stylesHref }];
export const meta: MetaFunction = () => [
  { title: "Agent Platform" },
  { name: "description", content: "Tenant workspace and receptionist onboarding for the Agent Platform." },
];

export default function App() {
  return (
    <html lang="en">
      <head><Meta /><Links /></head>
      <body>
        <nav className="app-nav" aria-label="Main navigation">
          <NavLink to="/" end className="app-brand"><span className="brand-mark">A</span><span>Agent Platform</span></NavLink>
          <div className="app-nav-links">
            <NavLink to="/dashboard" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Workspace</NavLink>
            <NavLink to="/" end className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Onboarding</NavLink>
          </div>
        </nav>
        <Outlet />
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}
