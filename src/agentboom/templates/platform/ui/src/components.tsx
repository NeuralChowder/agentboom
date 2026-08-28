/**
 * React renderers — turn UI manifests into working screens.
 *
 * Everything reads design tokens via CSS custom properties (see tokens.ts),
 * so the entire look is controlled by the active Theme. Components are
 * client-side and fetch through PlatformClient, so they drop into a Next.js
 * app (or any React host) unchanged.
 */
"use client";

import * as React from "react";

import { PlatformClient, getPath, resolveAction, resolveTemplate } from "./client.js";
import type {
  ActionSpec,
  CellFormat,
  ColumnSpec,
  FieldSpec,
  FormViewSpec,
  ListViewSpec,
  MiniAppEntry,
  MiniAppUiSpec,
  StatsViewSpec,
  TableViewSpec,
  ViewSpec,
} from "./manifest.js";
import { defaultTheme, themeToCssVars, type Theme } from "./tokens.js";

// ── context / provider ─────────────────────────────────────────────

interface AgentBoomContextValue {
  client: PlatformClient;
  theme: Theme;
  /** Called before a destructive/action request; return false to cancel. */
  confirmAction?: (action: ActionSpec, row: Record<string, unknown>) => boolean;
  /** Collect a prompted value for an action; return undefined to cancel. */
  promptAction?: (
    action: ActionSpec,
    row: Record<string, unknown>,
  ) => string | undefined;
}

const AgentBoomContext = React.createContext<AgentBoomContextValue | null>(null);

export interface AgentBoomProviderProps {
  client: PlatformClient;
  theme?: Theme;
  confirmAction?: AgentBoomContextValue["confirmAction"];
  promptAction?: AgentBoomContextValue["promptAction"];
  children: React.ReactNode;
}

export function AgentBoomProvider(props: AgentBoomProviderProps) {
  const theme = props.theme ?? defaultTheme;
  const value = React.useMemo<AgentBoomContextValue>(
    () => ({
      client: props.client,
      theme,
      confirmAction: props.confirmAction,
      promptAction: props.promptAction,
    }),
    [props.client, theme, props.confirmAction, props.promptAction],
  );
  return (
    <AgentBoomContext.Provider value={value}>
      <GlobalStyles theme={theme}>{props.children}</GlobalStyles>
    </AgentBoomContext.Provider>
  );
}

export function useAgentBoom(): AgentBoomContextValue {
  const ctx = React.useContext(AgentBoomContext);
  if (!ctx) throw new Error("useAgentBoom must be used within <AgentBoomProvider>");
  return ctx;
}

/** Emits the theme's CSS custom properties on a wrapper element. */
export function GlobalStyles({
  theme,
  children,
}: {
  theme: Theme;
  children?: React.ReactNode;
}) {
  const vars = themeToCssVars(theme) as React.CSSProperties;
  return (
    <div
      className="ab-root"
      style={{
        ...vars,
        background: "var(--ab-bg)",
        color: "var(--ab-text)",
        fontFamily: "var(--ab-font-sans)",
        fontSize: "var(--ab-font-size-base)",
        lineHeight: "var(--ab-line-height-base)",
        minHeight: "100%",
      }}
    >
      {children}
    </div>
  );
}

// ── formatting ─────────────────────────────────────────────────────

function formatCell(value: unknown, format?: CellFormat): string {
  if (value === undefined || value === null || value === "") return "—";
  switch (format) {
    case "relative": {
      const t = Date.parse(String(value));
      if (Number.isNaN(t)) return String(value);
      const diff = Date.now() - t;
      const m = Math.round(diff / 60000);
      if (Math.abs(m) < 60) return `${m}m ago`;
      const h = Math.round(m / 60);
      if (Math.abs(h) < 24) return `${h}h ago`;
      return `${Math.round(h / 24)}d ago`;
    }
    case "date": {
      const t = Date.parse(String(value));
      return Number.isNaN(t) ? String(value) : new Date(t).toLocaleString();
    }
    case "boolean":
      return value ? "yes" : "no";
    default:
      return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
}

// ── actions ────────────────────────────────────────────────────────

export interface ActionButtonProps {
  action: ActionSpec;
  row?: Record<string, unknown>;
  onDone?: (ok: boolean, result?: unknown) => void;
}

export function ActionButton({ action, row = {}, onDone }: ActionButtonProps) {
  const { client, confirmAction, promptAction } = useAgentBoom();
  const [busy, setBusy] = React.useState(false);

  const run = async () => {
    if (action.confirm) {
      const message = resolveTemplate(action.confirm, row);
      const ok = confirmAction ? confirmAction(action, row) : window.confirm(message);
      if (!ok) return;
    }
    let promptedRow = row;
    if (action.promptFor) {
      const hint = action.promptHint ?? `Enter ${action.promptFor}`;
      const initial = action.promptFrom ? String(getPath(row, action.promptFrom) ?? "") : "";
      const value = promptAction
        ? promptAction(action, row)
        : window.prompt(hint, initial);
      if (value === undefined || value === null) return;
      promptedRow = { ...row, [action.promptFor]: value };
    }
    const { path, body } = resolveAction(action.path, action.body, promptedRow);
    setBusy(true);
    try {
      const result = await client.sendJson(action.method, path, body);
      onDone?.(true, result);
    } catch (err) {
      console.error("action failed", action, err);
      onDone?.(false, err);
    } finally {
      setBusy(false);
    }
  };

  const tone =
    action.style === "danger"
      ? "var(--ab-danger)"
      : action.style === "primary"
        ? "var(--ab-accent)"
        : "var(--ab-border)";
  return (
    <button
      type="button"
      onClick={run}
      disabled={busy}
      style={{
        border: `1px solid ${tone}`,
        background: action.style === "primary" ? "var(--ab-accent)" : "transparent",
        color: action.style === "primary" ? "var(--ab-accent-contrast)" : "var(--ab-text)",
        borderRadius: "var(--ab-radius-sm)",
        padding: "4px 10px",
        cursor: busy ? "wait" : "pointer",
        font: "inherit",
        fontSize: "0.9em",
      }}
    >
      {busy ? "…" : action.label}
    </button>
  );
}

// ── table view ─────────────────────────────────────────────────────

export function TableView({ spec }: { spec: TableViewSpec }) {
  const { client } = useAgentBoom();
  const [rows, setRows] = React.useState<Record<string, unknown>[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const data = await client.getJson<Record<string, unknown>>(spec.source);
      const list = data[spec.rows];
      setRows(Array.isArray(list) ? (list as Record<string, unknown>[]) : []);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, [client, spec.source, spec.rows]);

  React.useEffect(() => {
    load();
    if (spec.refreshMs) {
      const id = setInterval(load, spec.refreshMs);
      return () => clearInterval(id);
    }
    return undefined;
  }, [load, spec.refreshMs]);

  if (error) return <div style={{ color: "var(--ab-danger)" }}>{error}</div>;
  if (!rows.length) return <Empty text={spec.empty ?? "Nothing here yet."} />;

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {spec.columns.map((col) => (
              <Th key={col.field} col={col} />
            ))}
            {spec.actions?.length ? <th style={thStyle} /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderTop: "1px solid var(--ab-border)" }}>
              {spec.columns.map((col) => (
                <td key={col.field} style={{ ...tdStyle, width: col.width }}>
                  {renderCell(row, col)}
                </td>
              ))}
              {spec.actions?.length ? (
                <td style={{ ...tdStyle, textAlign: "right", whiteSpace: "nowrap" }}>
                  {spec.actions
                    .filter((a) => !a.overflow)
                    .map((a) => (
                      <span key={a.label} style={{ marginLeft: 6 }}>
                        <ActionButton action={a} row={row} onDone={load && (() => load())} />
                      </span>
                    ))}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 10px",
  color: "var(--ab-faint)",
  fontWeight: 500,
  fontSize: "0.85em",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};
const tdStyle: React.CSSProperties = { padding: "8px 10px", verticalAlign: "top" };

function Th({ col }: { col: ColumnSpec }) {
  return <th style={thStyle}>{col.label ?? col.field}</th>;
}

function renderCell(row: Record<string, unknown>, col: ColumnSpec) {
  const value = getPath(row, col.field);
  if (col.format === "badge") {
    return (
      <span
        style={{
          display: "inline-block",
          padding: "1px 8px",
          borderRadius: "var(--ab-radius-lg)",
          border: "1px solid var(--ab-border)",
          color: "var(--ab-muted)",
          fontSize: "0.85em",
        }}
      >
        {formatCell(value)}
      </span>
    );
  }
  return formatCell(value, col.format);
}

// ── list view ──────────────────────────────────────────────────────

export function ListView({ spec }: { spec: ListViewSpec }) {
  const { client } = useAgentBoom();
  const [rows, setRows] = React.useState<Record<string, unknown>[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const data = await client.getJson<Record<string, unknown>>(spec.source);
      const list = data[spec.rows];
      setRows(Array.isArray(list) ? (list as Record<string, unknown>[]) : []);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, [client, spec.source, spec.rows]);

  React.useEffect(() => {
    load();
    if (spec.refreshMs) {
      const id = setInterval(load, spec.refreshMs);
      return () => clearInterval(id);
    }
    return undefined;
  }, [load, spec.refreshMs]);

  if (error) return <div style={{ color: "var(--ab-danger)" }}>{error}</div>;
  if (!rows.length) return <Empty text={spec.empty ?? "Nothing here yet."} />;

  const item = spec.item;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            border: "1px solid var(--ab-border)",
            borderRadius: "var(--ab-radius-md)",
            background: "var(--ab-bg-card)",
            padding: 14,
          }}
        >
          <div style={{ fontWeight: 600 }}>{resolveTemplate(item.title, row)}</div>
          {item.subtitle ? (
            <div style={{ color: "var(--ab-faint)", fontSize: "0.9em" }}>
              {resolveTemplate(item.subtitle, row)}
            </div>
          ) : null}
          {item.body ? (
            <div style={{ marginTop: 6, color: "var(--ab-muted)" }}>
              {resolveTemplate(item.body, row)}
            </div>
          ) : null}
          {item.meta?.length ? (
            <div style={{ marginTop: 6, color: "var(--ab-faint)", fontSize: "0.85em" }}>
              {item.meta
                .map((m) => formatCell(resolveTemplate(m.value, row), m.format))
                .join(" · ")}
            </div>
          ) : null}
          {spec.actions?.length ? (
            <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {spec.actions
                .filter((a) => !a.overflow)
                .map((a) => (
                  <ActionButton key={a.label} action={a} row={row} onDone={() => load()} />
                ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// ── form view ──────────────────────────────────────────────────────

export function FormView({ spec }: { spec: FormViewSpec }) {
  const { client } = useAgentBoom();
  const [values, setValues] = React.useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const f of spec.fields) {
      initial[f.name] = f.default === undefined ? "" : String(f.default);
    }
    return initial;
  });
  const [status, setStatus] = React.useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus(null);
    try {
      await client.sendJson(spec.submit.method, spec.submit.path, values);
      setStatus("Saved.");
    } catch (err) {
      setStatus(`Error: ${err}`);
    }
  };

  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 560 }}>
      {spec.fields.map((f) => (
        <Field key={f.name} field={f} value={values[f.name] ?? ""}
          onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}
      <div>
        <button
          type="submit"
          style={{
            background: "var(--ab-accent)",
            color: "var(--ab-accent-contrast)",
            border: "none",
            borderRadius: "var(--ab-radius-sm)",
            padding: "8px 16px",
            cursor: "pointer",
            font: "inherit",
          }}
        >
          {spec.submit.label ?? "Submit"}
        </button>
        {status ? <span style={{ marginLeft: 10, color: "var(--ab-muted)" }}>{status}</span> : null}
      </div>
    </form>
  );
}

function Field({
  field,
  value,
  onChange,
}: {
  field: FieldSpec;
  value: string;
  onChange: (v: string) => void;
}) {
  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--ab-bg-soft)",
    color: "var(--ab-text)",
    border: "1px solid var(--ab-border)",
    borderRadius: "var(--ab-radius-sm)",
    padding: "8px 10px",
    font: "inherit",
  };
  const label = (
    <label style={{ display: "block", color: "var(--ab-muted)", marginBottom: 4, fontSize: "0.9em" }}>
      {field.label ?? field.name}
      {field.required ? " *" : ""}
    </label>
  );
  if (field.type === "textarea") {
    return (
      <div>
        {label}
        <textarea rows={4} style={inputStyle} value={value} placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)} required={field.required} />
      </div>
    );
  }
  if (field.type === "select") {
    return (
      <div>
        {label}
        <select style={inputStyle} value={value} onChange={(e) => onChange(e.target.value)}
          required={field.required}>
          <option value="">—</option>
          {(field.options ?? []).map((opt) => {
            const v = typeof opt === "string" ? opt : opt.value;
            const l = typeof opt === "string" ? opt : opt.label;
            return <option key={v} value={v}>{l}</option>;
          })}
        </select>
      </div>
    );
  }
  return (
    <div>
      {label}
      <input type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
        style={inputStyle} value={value} placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value)} required={field.required} />
    </div>
  );
}

// ── stats view ─────────────────────────────────────────────────────

export function StatsView({ spec }: { spec: StatsViewSpec }) {
  const { client } = useAgentBoom();
  const [stats, setStats] = React.useState<Record<string, unknown> | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    client
      .getJson<Record<string, unknown>>(spec.source)
      .then((d) => active && setStats(d))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [client, spec.source]);

  if (error) return <div style={{ color: "var(--ab-danger)" }}>{error}</div>;
  if (!stats) return <Empty text="Loading…" />;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 10 }}>
      {Object.entries(stats).map(([k, v]) => (
        <div key={k} style={{ border: "1px solid var(--ab-border)", borderRadius: "var(--ab-radius-md)",
          background: "var(--ab-bg-card)", padding: 14 }}>
          <div style={{ color: "var(--ab-faint)", fontSize: "0.85em" }}>{k}</div>
          <div style={{ fontSize: "1.4em", fontWeight: 600 }}>{formatCell(v)}</div>
        </div>
      ))}
    </div>
  );
}

// ── shared bits ────────────────────────────────────────────────────

export function Empty({ text }: { text: string }) {
  return (
    <div style={{ color: "var(--ab-faint)", padding: "24px 0", textAlign: "center" }}>
      {text}
    </div>
  );
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 style={{ fontSize: "1.1em", fontWeight: 600, margin: "0 0 12px" }}>{children}</h2>
  );
}

// ── mini-app view (renders a whole app's ui spec) ─────────────────

export function ViewRenderer({ spec }: { spec: ViewSpec }) {
  switch (spec.type) {
    case "table":
      return <TableView spec={spec} />;
    case "list":
      return <ListView spec={spec} />;
    case "form":
      return <FormView spec={spec} />;
    case "stats":
      return <StatsView spec={spec} />;
    default:
      return <Empty text="Unsupported view." />;
  }
}

export interface MiniAppViewProps {
  app: MiniAppEntry;
}

/** Renders a mini-app's views with a tab bar to switch between them. */
export function MiniAppView({ app }: MiniAppViewProps) {
  const views = app.ui?.views ?? [];
  const [active, setActive] = React.useState(views[0]?.id ?? "");
  const current = views.find((v) => v.id === active) ?? views[0];

  if (!views.length) {
    return <Empty text={`${app.name} declares no UI views.`} />;
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
        {views.map((v) => (
          <button
            key={v.id}
            type="button"
            onClick={() => setActive(v.id)}
            style={{
              border: "1px solid var(--ab-border)",
              background: v.id === current?.id ? "var(--ab-bg-card)" : "transparent",
              color: v.id === current?.id ? "var(--ab-text)" : "var(--ab-muted)",
              borderRadius: "var(--ab-radius-sm)",
              padding: "6px 12px",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            {v.title}
          </button>
        ))}
      </div>
      {current ? (
        <div>
          {current.description ? (
            <p style={{ color: "var(--ab-muted)", marginTop: 0 }}>{current.description}</p>
          ) : null}
          <ViewRenderer spec={current} />
        </div>
      ) : null}
    </div>
  );
}

/** Navigation list of mini-apps, grouped by their `ui.nav.group`. */
export interface DashboardNavProps {
  apps: MiniAppEntry[];
  active?: string;
  onSelect?: (app: MiniAppEntry) => void;
}

export function DashboardNav({ apps, active, onSelect }: DashboardNavProps) {
  const withNav = apps.filter((a) => a.ui?.nav);
  const groups = new Map<string, MiniAppEntry[]>();
  for (const app of withNav) {
    const group = app.ui?.nav?.group ?? "General";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(app);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => (a.ui?.nav?.order ?? 99) - (b.ui?.nav?.order ?? 99));
  }
  return (
    <nav style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {[...groups.entries()].map(([group, list]) => (
        <div key={group}>
          <div style={{ color: "var(--ab-faint)", fontSize: "0.8em", textTransform: "uppercase",
            letterSpacing: "0.05em", marginBottom: 6 }}>
            {group}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {list.map((app) => {
              const selected = app.name === active;
              return (
                <button
                  key={app.name}
                  type="button"
                  onClick={() => onSelect?.(app)}
                  style={{
                    textAlign: "left",
                    border: "none",
                    background: selected ? "var(--ab-bg-card)" : "transparent",
                    color: selected ? "var(--ab-text)" : "var(--ab-muted)",
                    borderRadius: "var(--ab-radius-sm)",
                    padding: "6px 10px",
                    cursor: "pointer",
                    font: "inherit",
                  }}
                >
                  {app.ui?.nav?.label ?? app.name}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

export type { MiniAppUiSpec };
