import type { ReactNode } from "react";
import { Form, Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration, useLocation } from "react-router";
import "./styles.css";

export function meta() {
  return [
    { title: "Agent Platform" },
    { name: "description", content: "AI receptionist and customer conversation workflows for businesses." },
  ];
}

export function Layout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const isWorkspaceRoute = ["/dashboard", "/leads", "/conversations"].some(
    (path) => location.pathname === path || location.pathname.startsWith(path + "/"),
  );
  return (
    <html lang="en">
      <head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head>
      <body>
        <nav className="app-nav" aria-label="Main navigation">
          <NavLink to="/" end className="app-brand"><span className="brand-mark">A</span><span>Agent Platform</span></NavLink>
          <div className="app-nav-links">
            {isWorkspaceRoute ? <>
              <NavLink to="/dashboard" end className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Overview</NavLink>
              <NavLink to="/leads" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Leads</NavLink>
              <NavLink to="/conversations" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Conversations</NavLink>
              <details className="account-menu">
                <summary>Account</summary>
                <div className="account-menu-panel">
                  <div className="account-menu-heading"><strong>Workspace account</strong><small>Account details are not available in this view.</small></div>
                  <button type="button" disabled title="Profile page is not available yet">Profile</button>
                  <button type="button" disabled title="Workspace switching is not available yet">Switch workspace</button>
                  <button type="button" disabled title="Account settings are not available yet">Settings</button>
                  <div className="account-menu-divider" />
                  <Form method="post" action="/dashboard">
                    <button type="submit" name="intent" value="logout">Sign out</button>
                  </Form>
                </div>
              </details>
            </> : <>
              <NavLink to="/ai-receptionist/" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Solutions</NavLink>
              <NavLink to="/onboarding" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Get started</NavLink>
              <NavLink to="/dashboard" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>Workspace</NavLink>
            </>}
          </div>
        </nav>
        {children}
        <ScrollRestoration /><Scripts />
      </body>
    </html>
  );
}

export default function App() { return <Outlet />; }
