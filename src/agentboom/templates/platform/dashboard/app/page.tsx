"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AgentBoomProvider,
  DashboardNav,
  Empty,
  MiniAppView,
  PlatformClient,
  type MiniAppEntry,
} from "@agentboom/ui";
import {
  DEFAULT_CHOICE,
  ThemeSelect,
  parseThemeId,
  readStoredThemeId,
  themeFor,
  themeId,
  writeStoredThemeId,
  type ThemeChoice,
} from "./theme";

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

/**
 * Best-effort mirror of the theme choice into the settings mini-app
 * (profile.theme), so the choice follows the user beyond this browser.
 * localStorage already keeps it; failures here are silent.
 */
async function saveThemeToSettings(id: string): Promise<void> {
  try {
    const data = await client.getJson<{ profile?: Record<string, unknown> }>(
      "/api/settings/profile",
    );
    const profile = { ...(data.profile ?? {}), theme: id };
    await client.sendJson("PUT", "/api/settings/profile", { profile });
  } catch {
    // settings mini-app absent or agent home unmounted — fine.
  }
}

export default function DashboardPage() {
  const [apps, setApps] = useState<MiniAppEntry[]>([]);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [activeName, setActiveName] = useState<string>(COMMANDO);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [choice, setChoice] = useState<ThemeChoice>(DEFAULT_CHOICE);
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

  // The choice flips the token values in globals.css via `data-theme` on
  // <html>, and reaches the components through the Theme handed to the
  // provider (its inline vars carry the same values).
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", themeId(choice));
  }, [choice]);

  // Load the persisted choice: localStorage first, then the settings
  // profile (so a returning user on a fresh browser keeps their theme).
  useEffect(() => {
    const stored = readStoredThemeId();
    if (stored) {
      setChoice(parseThemeId(stored));
      return;
    }
    client
      .getJson<{ profile?: { theme?: unknown } }>("/api/settings/profile")
      .then((d) => {
        if (typeof d.profile?.theme === "string") {
          setChoice(parseThemeId(d.profile.theme));
        }
      })
      .catch(() => {});
  }, []);

  const selectTheme = (c: ThemeChoice) => {
    setChoice(c);
    const id = themeId(c);
    writeStoredThemeId(id);
    void saveThemeToSettings(id);
  };

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

  // Shared by both layouts: the collapsed mobile nav must navigate too.
  const content = isCommando ? (
    <CommandoContent apps={apps} health={health} agentUiUrl={AGENT_UI_URL} />
  ) : active ? (
    <>
      <h1
        style={{ marginTop: 0, fontSize: "var(--ab-text-xl)", color: "var(--ab-text)" }}
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
  ) : null;

  return (
    <AgentBoomProvider client={client} theme={themeFor(choice)}>
      {isMobile ? (
        <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
          <MobileHeader
            apps={navApps}
            active={activeName}
            onSelect={setActiveName}
            agentUiUrl={AGENT_UI_URL}
            choice={choice}
            onTheme={selectTheme}
          />
          <main className="ab-main">{content}</main>
        </div>
      ) : (
        <div className="ab-shell">
          <aside className="ab-sidebar">
            <div
              style={{
                fontWeight: "var(--ab-fw-bold)",
                fontSize: "var(--ab-text-lg)",
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
                  fontSize: "var(--ab-text-sm)",
                  textDecoration: "none",
                }}
              >
                Open agent UI ↗
              </a>
            ) : null}
            {loading ? (
              <div style={{ color: "var(--ab-faint)" }}>Loading…</div>
            ) : error ? (
              <div style={{ color: "var(--ab-danger)", fontSize: "var(--ab-text-sm)" }}>
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
            <div style={{ marginTop: "auto", paddingTop: "var(--ab-space-5)" }}>
              <div
                style={{
                  color: "var(--ab-faint)",
                  fontSize: "var(--ab-text-xs)",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  marginBottom: 4,
                }}
              >
                Theme
              </div>
              <ThemeSelect
                value={choice}
                onChange={selectTheme}
                className="ab-theme-select"
              />
            </div>
          </aside>

          <main className="ab-main">{content}</main>
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
  choice,
  onTheme,
}: {
  apps: MiniAppEntry[];
  active: string;
  onSelect: (name: string) => void;
  agentUiUrl: string;
  choice: ThemeChoice;
  onTheme: (choice: ThemeChoice) => void;
}) {
  return (
    <header className="ab-mobile-header">
      <select
        value={active}
        onChange={(e) => onSelect(e.target.value)}
        aria-label="Section"
        className="ab-select ab-nav-select"
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
          style={{ color: "var(--ab-muted)", textDecoration: "none", fontSize: "var(--ab-text-sm)" }}
        >
          Agent UI ↗
        </a>
      ) : null}
      <ThemeSelect value={choice} onChange={onTheme} className="ab-theme-select" />
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
      <h1 style={{ marginTop: 0, fontSize: "var(--ab-text-xl)", color: "var(--ab-text)" }}>
        Commando
      </h1>
      <p style={{ color: "var(--ab-muted)", marginTop: 0 }}>
        Your whole agent at a glance. This page is generated from the live
        catalog — nothing here is hardcoded.
      </p>

      <div className="ab-cards ab-cards--stats" style={{ margin: "var(--ab-space-4) 0 var(--ab-space-5)" }}>
        <StatCard
          label="Gateway"
          value={gatewayOk ? "ok" : "down"}
          tone={gatewayOk ? "var(--ab-text)" : "var(--ab-danger)"}
        />
        <StatCard label="Apps" value={apps.length} />
        <StatCard label="Apps with UI" value={withUi.length} />
      </div>

      <h2 style={{ fontSize: "var(--ab-text-lg)", fontWeight: 600, marginBottom: 4 }}>
        Getting started
      </h2>
      <p style={{ color: "var(--ab-faint)", fontSize: "var(--ab-text-sm)", marginTop: 0 }}>
        A few minutes is all it takes — ask the agent, it can do most of this for you.
      </p>
      <div className="ab-cards" style={{ marginBottom: "var(--ab-space-6)" }}>
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
                className="ab-action"
                style={{ marginTop: "var(--ab-space-3)" }}
              >
                Open ↗
              </a>
            ) : (
              <span style={{ marginTop: "var(--ab-space-3)", display: "inline-block", fontSize: "var(--ab-text-sm)" }}>
                Set NEXT_PUBLIC_AGENT_UI to enable.
              </span>
            )
          }
        />
      </div>

      <h2 style={{ fontSize: "var(--ab-text-lg)", fontWeight: 600, marginBottom: 4 }}>
        Your apps
      </h2>
      <p style={{ color: "var(--ab-faint)", fontSize: "var(--ab-text-sm)", marginTop: 0 }}>
        Private — reachable by the assistant and this dashboard only.
      </p>
      {withUi.length === 0 ? (
        <Empty text="No apps with a UI yet — install packages with `agentboom add package`." />
      ) : (
        <div className="ab-cards">
          {apps.map((a) => (
            <div key={a.name} className="ab-card" style={{ padding: "var(--ab-space-3)" }}>
              <div style={{ fontWeight: 600 }}>
                {a.ui?.nav?.label ?? a.name}
                {a.ui?.views?.length ? null : (
                  <span
                    style={{
                      marginLeft: 8,
                      fontSize: "var(--ab-text-xs)",
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
                  fontSize: "var(--ab-text-sm)",
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
    <div className="ab-card" style={{ padding: "var(--ab-space-3)" }}>
      <div style={{ color: "var(--ab-faint)", fontSize: "var(--ab-text-sm)" }}>{label}</div>
      <div style={{ fontSize: "var(--ab-text-2xl)", fontWeight: 600, color: tone }}>{value}</div>
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
    <div className="ab-card" style={{ padding: 14 }}>
      <div style={{ fontWeight: 600 }}>{title}</div>
      <p style={{ margin: "6px 0 0", color: "var(--ab-muted)", fontSize: "var(--ab-text-sm)" }}>
        {body}
      </p>
      {action}
    </div>
  );
}
