import { useState } from "react";
import { ExcavatePanel } from "./panels/ExcavatePanel";

interface Tab {
  id: string;
  label: string;
  render: () => React.ReactNode;
}

const TABS: Tab[] = [{ id: "excavate", label: "Excavate", render: () => <ExcavatePanel /> }];

export function App() {
  const [active, setActive] = useState(TABS[0].id);
  const current = TABS.find((t) => t.id === active) ?? TABS[0];

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">
          FossilRAG <span className="app__title-accent">Excavation Console</span>
        </h1>
        <p className="app__tagline">The Dinosaur Whisperer's serverless document excavator.</p>
      </header>

      <nav className="tabs" aria-label="Tools">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={t.id === active ? "tab tab--active" : "tab"}
            aria-current={t.id === active ? "page" : undefined}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="app__main">{current.render()}</main>
    </div>
  );
}
