import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import type { ChatMessage } from "../api/types";
import { Badge } from "../components/Badge";
import { FossilCard } from "../components/FossilCard";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /chat — conversational excavation: each turn is answered, grounded in fossils. */
export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sourceId, setSourceId] = useState("");
  const chat = useAsyncAction(api.chat);
  const res = chat.data;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const content = draft.trim();
    if (!content) return;
    const next: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages(next);
    setDraft("");
    const reply = await chat.run({ messages: next, k: 5, source_id: sourceId.trim() || null });
    if (reply) setMessages([...next, { role: "assistant", content: reply.answer }]);
  };

  return (
    <section className="panel" aria-labelledby="chat-heading">
      <h2 id="chat-heading">Chat Excavation</h2>
      <p className="panel__lede">
        Ask questions across a conversation; answers cite the fossils (and geological age) used.
      </p>

      <ol className="chat" aria-label="conversation">
        {messages.map((m, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: append-only ordered transcript
          <li key={i} className={`chat__turn chat__turn--${m.role}`}>
            <span className="chat__role">{m.role}</span>
            <p className="chat__content">{m.content}</p>
          </li>
        ))}
        {chat.status === "loading" ? (
          <li className="chat__turn chat__turn--assistant">
            <span className="chat__role">assistant</span>
            <p className="chat__content status--loading">thinking…</p>
          </li>
        ) : null}
      </ol>

      {chat.status === "error" ? (
        <p className="status status--error" role="alert">
          {chat.error}
        </p>
      ) : null}

      {res && chat.status === "success" ? (
        <div className="result__bar">
          <Badge tone={res.mock ? "mock" : "live"}>{res.model_id}</Badge>
          {res.cached ? <Badge tone="cached">cached</Badge> : null}
          {res.citations.length > 0 ? (
            <details className="result__sources">
              <summary>{res.citations.length} citations</summary>
              <ul className="hits">
                {res.citations.map((hit) => (
                  <FossilCard key={`${hit.chunk_id}:${hit.layer_version}`} hit={hit} />
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      <form className="form" onSubmit={onSubmit}>
        <label className="field field--grow">
          <span>Message</span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="ask about the excavated documents…"
            aria-label="Message"
          />
        </label>
        <label className="field">
          <span>Source (optional)</span>
          <input
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            placeholder="scope to one document"
            aria-label="Source id"
          />
        </label>
        <button type="submit" disabled={chat.status === "loading" || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
