import type { ReactNode } from "react";
import { Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration } from "react-router";
import "./styles.css";

export function meta() {
  return [
    { title: "Agent Platform" },
    { name: "description", content: "AI receptionist and customer conversation workflows for businesses." },
  ];
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head>
      <body>
        <nav className="app-nav" aria-label="Main navigation">
          <NavLink to="/" end className="app-brand"><span className="brand-mark">A</span><span>Agent Platform</span></NavLink>
          <div className="app-nav-links">
            <NavLink to="/ai-receptionist/" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Solutions</NavLink>
            <NavLink to="/onboarding" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Get started</NavLink>
            <NavLink to="/dashboard" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Workspace</NavLink>
          </div>
        </nav>
        {children}
        <ScrollRestoration /><Scripts />
      </body>
    </html>
  );
}

export default function App() { return <Outlet />; }
