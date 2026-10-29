import { api } from "./api";
import JobsPage from "./pages/Jobs";
import Library from "./pages/Library";
import ProjectPage from "./pages/Project";
import ReviewPage from "./pages/Review";
import SettingsPage from "./pages/Settings";
import { useData, useHashRoute } from "./util";

export default function App() {
  const route = useHashRoute();
  const health = useData(() => api.health(), [], 10000);
  const [page, id] = route;

  let body;
  if (page === "p" && id) body = <ProjectPage projectId={id} />;
  else if (page === "m" && id) body = <ReviewPage mediaId={id} />;
  else if (page === "jobs") body = <JobsPage />;
  else if (page === "settings") body = <SettingsPage />;
  else body = <Library />;

  const active = (p: string) => (page ?? "") === p || (p === "" && (page === "p" || page === "m")) ? "active" : "";
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">Clip<span>Forge</span></a>
        <nav className="nav">
          <a className={active("")} href="#/">Library</a>
          <a className={active("jobs")} href="#/jobs">Jobs</a>
          <a className={active("settings")} href="#/settings">Settings</a>
        </nav>
        <div className="spacer" />
        {health.error ? (
          <span className="chip failed" title={health.error}>backend offline</span>
        ) : health.data ? (
          <span className="chip ready" title={`worker: ${health.data.worker}`}>v{health.data.version} · local</span>
        ) : null}
      </header>
      <main className="main">{body}</main>
    </div>
  );
}
