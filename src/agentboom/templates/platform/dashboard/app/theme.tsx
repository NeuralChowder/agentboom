/**
 * Theme presets for the dashboard.
 *
 * A choice is a (mode, accent) pair serialized to a single id —
 * "dawn", "dusk", or "<mode>-<accent>" (e.g. "dusk-ocean") — which is
 * applied as the `data-theme` attribute on <html>. That attribute flips
 * the token values in `globals.css` (the document-level source of truth,
 * which must stay in sync with this file), and `themeFor` builds the
 * matching `Theme` object handed to `<AgentBoomProvider>` so the tokens
 * the components read inline carry the same values.
 *
 * Persistence: localStorage (`agentboom.theme`) plus, best-effort, the
 * settings mini-app (`profile.theme`), so the choice follows the user.
 */
import { defaultTheme, lightTheme, type Theme } from "@agentboom/ui";

export type ModeId = "dawn" | "dusk";
export type AccentId = "amber" | "ocean" | "meadow" | "rose";

export interface ThemeChoice {
  mode: ModeId;
  accent: AccentId;
}

export const STORAGE_KEY = "agentboom.theme";

/** The product default: light. */
export const DEFAULT_CHOICE: ThemeChoice = { mode: "dawn", accent: "amber" };

export const MODES: { id: ModeId; label: string; kind: "light" | "dark" }[] = [
  { id: "dawn", label: "Dawn", kind: "light" },
  { id: "dusk", label: "Dusk", kind: "dark" },
];

export const ACCENTS: { id: AccentId; label: string }[] = [
  { id: "amber", label: "Amber" },
  { id: "ocean", label: "Ocean" },
  { id: "meadow", label: "Meadow" },
  { id: "rose", label: "Rose" },
];

/**
 * Accent-hue overrides (amber is each base theme's own accent and needs
 * none). Values mirror the accent blocks in globals.css — keep in sync.
 */
const ACCENT_OVERRIDES: Record<
  Exclude<AccentId, "amber">,
  Record<ModeId, { accent: string; accentContrast: string }>
> = {
  ocean: {
    dawn: { accent: "#1d6fd6", accentContrast: "#ffffff" },
    dusk: { accent: "#6b9aff", accentContrast: "#0a1020" },
  },
  meadow: {
    dawn: { accent: "#15803d", accentContrast: "#ffffff" },
    dusk: { accent: "#4ade80", accentContrast: "#07130b" },
  },
  rose: {
    dawn: { accent: "#be185d", accentContrast: "#ffffff" },
    dusk: { accent: "#fb7185", accentContrast: "#1f0710" },
  },
};

/** The @agentboom/ui Theme for a choice (passed to the provider). */
export function themeFor(choice: ThemeChoice): Theme {
  const base = choice.mode === "dusk" ? defaultTheme : lightTheme;
  if (choice.accent === "amber") return base;
  const o = ACCENT_OVERRIDES[choice.accent][choice.mode];
  return {
    ...base,
    name: themeId(choice),
    colors: {
      ...base.colors,
      accent: o.accent,
      accentContrast: o.accentContrast,
    },
  };
}

/** Serialize a choice to its `data-theme` id. */
export function themeId(choice: ThemeChoice): string {
  return choice.accent === "amber"
    ? choice.mode
    : `${choice.mode}-${choice.accent}`;
}

/** Parse a stored id; anything unrecognized falls back to the default. */
export function parseThemeId(id: string | null | undefined): ThemeChoice {
  if (!id) return DEFAULT_CHOICE;
  const [mode, accent = "amber"] = id.split("-");
  const m = MODES.find((x) => x.id === mode)?.id;
  const a = ACCENTS.find((x) => x.id === accent)?.id;
  return m && a ? { mode: m, accent: a } : DEFAULT_CHOICE;
}

/** Flat option list for <select> controls, grouped by mode. */
export const THEME_OPTIONS: { id: string; label: string }[] = MODES.flatMap(
  (m) =>
    ACCENTS.map((a) => ({
      id: a.id === "amber" ? m.id : `${m.id}-${a.id}`,
      label:
        a.id === "amber"
          ? `${m.label} · ${m.kind}`
          : `${m.label} · ${a.label.toLowerCase()}`,
    })),
);

export function readStoredThemeId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeStoredThemeId(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // private mode / storage full — the settings mirror still applies
  }
}

/** The switcher control itself (native select: accessible, touch-friendly). */
export function ThemeSelect({
  value,
  onChange,
  className,
}: {
  value: ThemeChoice;
  onChange: (choice: ThemeChoice) => void;
  className?: string;
}) {
  return (
    <select
      aria-label="Theme"
      className={className ? `ab-select ${className}` : "ab-select"}
      value={themeId(value)}
      onChange={(e) => onChange(parseThemeId(e.target.value))}
    >
      {THEME_OPTIONS.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
