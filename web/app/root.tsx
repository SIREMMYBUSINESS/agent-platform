import type { LinksFunction, MetaFunction } from "@remix-run/node";
import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "@remix-run/react";
import stylesHref from "./styles.css?url";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: stylesHref }];
export const meta: MetaFunction = () => [
  { title: "Business onboarding | Agent Platform" },
  { name: "description", content: "Configure a dental receptionist and outreach workflow." },
];

export default function App() {
  return (
    <html lang="en">
      <head><Meta /><Links /></head>
      <body>
        <Outlet />
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}
