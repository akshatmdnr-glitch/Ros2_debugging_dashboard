import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Overview", end: true },
  { to: "/graph", label: "Graph", end: false },
  { to: "/tf", label: "TF", end: false },
  { to: "/incidents", label: "Incidents", end: false },
  { to: "/telemetry", label: "Telemetry", end: false },
];

export function NavBar() {
  return (
    <nav className="navbar">
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
