import { useEffect, useState } from "react";
import { api } from "../api";
import type { PrivacyCategory } from "../types";

/** Everything Max keeps on this laptop: see it, export it, delete it. */
export default function PrivacyTab() {
  const [cats, setCats] = useState<PrivacyCategory[]>([]);
  const [arming, setArming] = useState<string | null>(null);   // category waiting for the second click
  const [message, setMessage] = useState("");

  const load = () => api.privacy().then(setCats).catch((e) => setMessage(e instanceof Error ? e.message : "Couldn't load"));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!arming) return;
    const t = window.setTimeout(() => setArming(null), 4000);
    return () => window.clearTimeout(t);
  }, [arming]);

  const remove = async (id: string) => {
    if (arming !== id) { setArming(id); return; }
    setArming(null);
    try {
      const r = await api.privacyDelete(id);
      setMessage(r.message);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't delete");
    }
  };

  return (
    <div className="drawer-body">
      <p className="drawer-note">
        Everything below lives only on this laptop. Nothing is sent to a cloud service; the phone reaches it over your own
        Tailscale network. Export makes one zip (browser data, the phone token and secrets are left out).
      </p>
      <a className="btn primary privacy-export" href="/api/privacy/export" download>⤓ Export everything</a>
      {message && <div className="privacy-msg">{message}</div>}
      <div className="list">
        {cats.map((c) => (
          <div key={c.id} className="list-row static privacy-row">
            <div className="privacy-text">
              <div className="list-text">{c.label} <span className="label dim">· {c.amount}</span></div>
              <div className="note-summary">{c.what}</div>
              <div className="label dim privacy-where">{c.where}</div>
            </div>
            <button className={`btn ${arming === c.id ? "deny" : ""}`} onClick={() => remove(c.id)}>
              {arming === c.id ? "Click again to delete" : "Delete"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
