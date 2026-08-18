"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AgentBoomProvider,
  DashboardNav,
  MiniAppView,
  PlatformClient,
  defaultTheme,
  type MiniAppEntry,
} from "@agentboom/ui";

// Same-origin: /api/* is proxied to the platform gateway by next.config.
const client = new PlatformClient({ baseUrl: "" });

export default function DashboardPage() {
  const [apps, setApps] = useState<MiniAppEntry[]>([]);
  const [activeName, setActiveName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client
      .catalog()
      .then((c) => {
        const withUi = (c.apps ?? []).filter((a) => a.ui && a.ui.views?.length);
        setApps(withUi);
        setActiveName(withUi[0]?.name ?? "");
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, []);

  const active = useMemo(
    () => apps.find((a) => a.name === activeName) ?? null,
    [apps, activeName],
  );

  return (
    <AgentBoomProvider client={client} theme={defaultTheme}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "240px 1fr",
          minHeight: "100vh",
          background: "var(--ab-bg)",
        }}
      >
        <aside
          style={{
            borderRight: "1px solid var(--ab-border)",
            padding: 16,
            background: "var(--ab-bg-soft)",
          }}
        >
          <div
            style={{
              fontWeight: 700,
              fontSize: "1.05em",
              marginBottom: 18,
              color: "var(--ab-text)",
            }}
          >
            agentboom
          </div>
          {loading ? (
            <div style={{ color: "var(--ab-faint)" }}>Loading…</div>
          ) : error ? (
            <div style={{ color: "var(--ab-danger)", fontSize: "0.9em" }}>
              Could not reach the platform.
              <br />
              {error}
            </div>
          ) : (
            <DashboardNav
              apps={apps}
              active={activeName}
              onSelect={(app) => setActiveName(app.name)}
            />
          )}
        </aside>

        <main style={{ padding: 24, overflowX: "auto" }}>
          {active ? (
            <>
              <h1 style={{ marginTop: 0, fontSize: "1.3em", color: "var(--ab-text)" }}>
                {active.ui?.nav?.label ?? active.name}
              </h1>
              {active.description ? (
                <p style={{ color: "var(--ab-muted)", marginTop: 0 }}>
                  {active.description}
                </p>
              ) : null}
              <MiniAppView app={active} />
            </>
          ) : !loading && !error ? (
            <div style={{ color: "var(--ab-faint)" }}>
              No mini-apps declare a UI yet. Add one with{" "}
              <code>agentboom add miniapp &lt;name&gt;</code> and give it a{" "}
              <code>ui</code> manifest.
            </div>
          ) : null}
        </main>
      </div>
    </AgentBoomProvider>
  );
}
