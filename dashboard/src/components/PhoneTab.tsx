import { useEffect, useState } from "react";
import { api } from "../api";
import type { Pairing } from "../types";

/** Pair the Android app: scan the QR code (server address + access token). */
export default function PhoneTab() {
  const [p, setP] = useState<Pairing | null>(null);
  const [error, setError] = useState("");
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    api.pair().then(setP).catch((e) => setError(e instanceof Error ? e.message : "Couldn't load pairing"));
  }, []);

  if (error) return <div className="drawer-body"><div className="error-line">{error}</div></div>;
  if (!p) return <div className="drawer-body"><p className="drawer-note">Loading…</p></div>;
  if (!p.enabled) {
    return <div className="drawer-body"><p className="drawer-note">Phone access is off. Set <code>phone.enabled: true</code> in config.yaml and restart Max.</p></div>;
  }
  return (
    <div className="drawer-body">
      <p className="drawer-note">
        Open the Max app on your phone, tap <b>Pair</b> and scan this code. The phone talks to this laptop through
        Tailscale, so it works from anywhere while the laptop is on. Anyone with this code can control Max: don’t share it.
      </p>
      {p.link ? (
        <div className="pair-qr" dangerouslySetInnerHTML={{ __html: p.qr_svg }} />
      ) : (
        <div className="rec-card">
          <div className="rec-top"><b>Tailscale isn’t set up yet</b></div>
          <ol className="pair-steps">
            <li>Install Tailscale on this laptop and your phone, and sign in with the same account.</li>
            <li>In PowerShell on the laptop run <code>tailscale serve --bg 8765</code></li>
            <li>Reopen this tab: the QR code appears.</li>
          </ol>
        </div>
      )}
      <div className="list">
        <div className="list-row static"><span className="label">address</span><span className="list-text mono">{p.url || "—"}</span></div>
        <div className="list-row static">
          <span className="label">token</span>
          <button className="linkish mono" onClick={() => setShowToken(!showToken)}>{showToken ? p.token : "•••••••• show"}</button>
        </div>
      </div>
    </div>
  );
}
