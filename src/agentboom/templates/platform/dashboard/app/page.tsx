"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AgentBoomProvider,
  DashboardNav,
  Empty,
  MiniAppView,
  PlatformClient,
  defaultTheme,
  type MiniAppEntry,
} from "@agentboom/ui";

// Same-origin: /api/* and /public/* are proxied to the platform gateway by
// next.config. The gateway's hard public boundary requires the bearer token
// on every non-public call, so the client carries it (inlined at build time
// from NEXT_PUBLIC_PLATFORM_TOKEN).
const client = new PlatformClient({
  baseUrl: "",
  token: process.env.NEXT_PUBLIC_PLATFORM_TOKEN ?? "",
});

// The agent's own web UI (qwen serve), opened in a new tab. Inlined at build
// time from NEXT_PUBLIC_AGENT_UI, which compose derives from PORT_AGENT.
const AGENT_UI_URL = process.env.NEXT_PUBLIC_AGENT_UI ?? "";

// The Commando is a built-in view, not a mini-app: it is always first and
// always the default landing page.
const COMMANDO = "__commando__";

function useIsMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 720px)");
    setMobile(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

export default function DashboardPage() {
  const [apps, setApps] = useState<MiniAppEntry[]>([]);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [activeName, setActiveName] = useState<string>(COMMANDO);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const isMobile = useIsMobile();

  useEffect(() => {
    Promise.all([
      client.catalog().catch((e) => ({ error: String(e) })),
      client
        .getJson<Record<string, unknown>>("/health")
        .catch((e) => ({ error: String(e) })),
    ])
      .then(([c, h]) => {
        const catalog = "apps" in c ? c : null;
        const health = "status" in h ? h : null;
        const withUi = (catalog?.apps ?? []).filter(
          (a) => a.ui && a.ui.views?.length,
        );
        setApps(catalog?.apps ?? []);
        setHealth(health);
        setActiveName(COMMANDO);
        if (!catalog) {
          setError(
            "error" in c ? String(c.error) : "Could not reach the platform.",
          );
        }
        setLoading(false);
      });
  }, []);

  const navApps = useMemo<MiniAppEntry[]>(
    () => [
      {
        name: COMMANDO,
        description: "The whole agent at a glance.",
        ui: { nav: { label: "Commando", icon: "grid", group: "Main", order: 0 }, views: [] },
      },
      ...apps,
    ],
    [apps],
  );

  const active =
    activeName === COMMANDO
      ? navApps[0]
      : navApps.find((a) => a.name === activeName) ?? null;
  const isCommando = activeName === COMMANDO;

  return (
    <AgentBoomProvider client={client} theme={defaultTheme}>
      {isMobile ? (
        <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
          <MobileHeader
            apps={navApps}
            active={activeName}
            onSelect={setActiveName}
            agentUiUrl={AGENT_UI_URL}
          />
          <main style={{ padding: 16, overflowX: "auto" }}>
            <CommandoContent
              apps={apps}
              health={health}
              agentUiUrl={AGENT_UI_URL}
            />
          </main>
        </div>
      ) : (
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
                marginBottom: 6,
                color: "var(--ab-text)",
              }}
            >
              {String(health?.agent ?? "agent")}
            </div>
            {AGENT_UI_URL ? (
              <a
                href={AGENT_UI_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-block",
                  marginBottom: 14,
                  color: "var(--ab-muted)",
                  fontSize: "0.85em",
                  textDecoration: "none",
                }}
              >
                Open agent UI ↗
              </a>
            ) : null}
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
                apps={navApps}
                active={activeName}
                onSelect={(app) => setActiveName(app.name)}
              />
            )}
          </aside>

          <main style={{ padding: 24, overflowX: "auto" }}>
            {isCommando ? (
              <CommandoContent
                apps={apps}
                health={health}
                agentUiUrl={AGENT_UI_URL}
              />
            ) : active ? (
              <>
                <h1
                  style={{ marginTop: 0, fontSize: "1.3em", color: "var(--ab-text)" }}
                >
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
      )}
    </AgentBoomProvider>
  );
}

function MobileHeader({
  apps,
  active,
  onSelect,
  agentUiUrl,
}: {
  apps: MiniAppEntry[];
  active: string;
  onSelect: (name: string) => void;
  agentUiUrl: string;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "10px 14px",
        borderBottom: "1px solid var(--ab-border)",
        background: "var(--ab-bg-soft)",
      }}
    >
      <select
        value={active}
        onChange={(e) => onSelect(e.target.value)}
        style={{
          flex: 1,
          font: "inherit",
          padding: "6px 8px",
          borderRadius: "var(--ab-radius-sm)",
          border: "1px solid var(--ab-border)",
          background: "var(--ab-bg-card)",
          color: "var(--ab-text)",
        }}
      >
        {apps.map((a) => (
          <option key={a.name} value={a.name}>
            {a.ui?.nav?.label ?? a.name}
          </option>
        ))}
      </select>
      {agentUiUrl ? (
        <a
          href={agentUiUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--ab-muted)", textDecoration: "none", fontSize: "0.9em" }}
        >
          Agent UI ↗
        </a>
      ) : null}
    </header>
  );
}

function CommandoContent({
  apps,
  health,
  agentUiUrl,
}: {
  apps: MiniAppEntry[];
  health: Record<string, unknown> | null;
  agentUiUrl: string;
}) {
  const withUi = apps.filter((a) => a.ui && a.ui.views?.length);
  const gatewayOk = health?.status === "ok";

  return (
    <div>
      <h1 style={{ marginTop: 0, fontSize: "1.3em", color: "var(--ab-text)" }}>
        Commando
      </h1>
      <p style={{ color: "var(--ab-muted)", marginTop: 0 }}>
        Your whole agent at a glance. This page is generated from the live
        catalog — nothing here is hardcoded.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
          gap: 10,
          margin: "16px 0 24px",
        }}
      >
        <StatCard
          label="Gateway"
          value={gatewayOk ? "ok" : "down"}
          tone={gatewayOk ? "var(--ab-text)" : "var(--ab-danger)"}
        />
        <StatCard label="Apps" value={apps.length} />
        <StatCard label="Apps with UI" value={withUi.length} />
      </div>

      <h2 style={{ fontSize: "1.05em", fontWeight: 600, marginBottom: 4 }}>
        Getting started
      </h2>
      <p style={{ color: "var(--ab-faint)", fontSize: "0.85em", marginTop: 0 }}>
        A few minutes is all it takes — ask the agent, it can do most of this for you.
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
          gap: 10,
          marginBottom: 28,
        }}
      >
        <OnboardCard
          title="Talk to it from your phone"
          body='Install the telegram package, then ask the agent: "set up telegram". It walks you through @BotFather and finishes the wiring itself.'
        />
        <OnboardCard
          title="Let it improve itself"
          body="Install the self-evolve package. Every night it reviews the platform, finds real defects, and makes at most one verified change — or none."
        />
        <OnboardCard
          title="Open the agent UI"
          body="The full chat with your agent, in the browser."
          action={
            agentUiUrl ? (
              <a
                href={agentUiUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-block",
                  marginTop: 10,
                  background: "var(--ab-accent)",
                  color: "var(--ab-accent-contrast)",
                  borderRadius: "var(--ab-radius-sm)",
                  padding: "6px 14px",
                  textDecoration: "none",
                  fontSize: "0.9em",
                }}
              >
                Open ↗
              </a>
            ) : (
              <span style={{ marginTop: 10, display: "inline-block", fontSize: "0.85em" }}>
                Set NEXT_PUBLIC_AGENT_UI to enable.
              </span>
            )
          }
        />
      </div>

      <h2 style={{ fontSize: "1.05em", fontWeight: 600, marginBottom: 4 }}>
        Your apps
      </h2>
      <p style={{ color: "var(--ab-faint)", fontSize: "0.85em", marginTop: 0 }}>
        Private — reachable by the assistant and this dashboard only.
      </p>
      {withUi.length === 0 ? (
        <Empty text="No apps with a UI yet — install packages with `agentboom add package`." />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: 10,
          }}
        >
          {apps.map((a) => (
            <div
              key={a.name}
              style={{
                border: "1px solid var(--ab-border)",
                borderRadius: "var(--ab-radius-md)",
                background: "var(--ab-bg-card)",
                padding: 12,
              }}
            >
              <div style={{ fontWeight: 600 }}>
                {a.ui?.nav?.label ?? a.name}
                {a.ui?.views?.length ? null : (
                  <span
                    style={{
                      marginLeft: 8,
                      fontSize: "0.7em",
                      color: "var(--ab-faint)",
                      border: "1px solid var(--ab-border)",
                      borderRadius: "var(--ab-radius-lg)",
                      padding: "1px 8px",
                      verticalAlign: "middle",
                    }}
                  >
                    API only
                  </span>
                )}
              </div>
              <p
                style={{
                  margin: "6px 0 0",
                  color: "var(--ab-muted)",
                  fontSize: "0.85em",
                  overflow: "hidden",
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                }}
              >
                {a.description || a.name}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--ab-border)",
        borderRadius: "var(--ab-radius-md)",
        background: "var(--ab-bg-card)",
        padding: 12,
      }}
    >
      <div style={{ color: "var(--ab-faint)", fontSize: "0.85em" }}>{label}</div>
      <div style={{ fontSize: "1.4em", fontWeight: 600, color: tone }}>{value}</div>
    </div>
  );
}

function OnboardCard({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--ab-border)",
        borderRadius: "var(--ab-radius-md)",
        background: "var(--ab-bg-card)",
        padding: 14,
      }}
    >
      <div style={{ fontWeight: 600 }}>{title}</div>
      <p style={{ margin: "6px 0 0", color: "var(--ab-muted)", fontSize: "0.85em" }}>
        {body}
      </p>
      {action}
    </div>
  );
}
