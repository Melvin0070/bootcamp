import { useState } from "react";
import { ChatPanel } from "./panels/ChatPanel";
import { DatasetPanel } from "./panels/DatasetPanel";
import { EnrichPanel } from "./panels/EnrichPanel";
import { ExcavatePanel } from "./panels/ExcavatePanel";
import { FossilLayersPanel } from "./panels/FossilLayersPanel";
import { IngestPanel } from "./panels/IngestPanel";
import { MutatePanel } from "./panels/MutatePanel";
import { SlideMutatePanel } from "./panels/SlideMutatePanel";

interface Tab {
  id: string;
  label: string;
  render: () => React.ReactNode;
}

const TABS: Tab[] = [
  { id: "excavate", label: "Excavate", render: () => <ExcavatePanel /> },
  { id: "mutate", label: "Mutate", render: () => <MutatePanel /> },
  { id: "chat", label: "Chat", render: () => <ChatPanel /> },
  { id: "layers", label: "Fossil Layers", render: () => <FossilLayersPanel /> },
  { id: "slide", label: "Slide Mutator", render: () => <SlideMutatePanel /> },
  { id: "dataset", label: "Dataset", render: () => <DatasetPanel /> },
  { id: "enrich", label: "Enrich", render: () => <EnrichPanel /> },
  { id: "ingest", label: "Ingest", render: () => <IngestPanel /> },
];

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
