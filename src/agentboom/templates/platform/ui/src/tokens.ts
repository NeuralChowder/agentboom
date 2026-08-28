/**
 * Design tokens — the single source of truth for the look & feel.
 *
 * A *design system* in agentboom is just a Theme: swap the token values
 * and you get a different design system without touching any component.
 * Components never hard-code colors/sizes; they read CSS custom
 * properties emitted from a Theme via `themeToCssVars`.
 *
 *   import { defaultTheme, themeToCssVars, GlobalStyles } from "@agentboom/ui";
 *
 * To author a new design system, start from `defaultTheme` (or
 * `lightTheme`) and override the tokens you care about.
 */

export interface ColorTokens {
  bg: string;
  bgSoft: string;
  bgCard: string;
  border: string;
  text: string;
  muted: string;
  faint: string;
  accent: string;
  accentContrast: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
}

export interface TypographyTokens {
  fontFamilySans: string;
  fontFamilyMono: string;
  fontSizeBase: string;
  lineHeightBase: number;
  fontWeightRegular: number;
  fontWeightMedium: number;
  fontWeightBold: number;
}

export interface SpacingTokens {
  unit: string;      // base spacing unit, e.g. "4px"
  radiusSm: string;
  radiusMd: string;
  radiusLg: string;
}

export interface Theme {
  name: string;
  mode: "dark" | "light";
  colors: ColorTokens;
  typography: TypographyTokens;
  spacing: SpacingTokens;
}

export const defaultTheme: Theme = {
  name: "agentboom-dark",
  mode: "dark",
  colors: {
    bg: "#0b0d12",
    bgSoft: "#10131b",
    bgCard: "#12161f",
    border: "#1e2431",
    text: "#f4f6f9",
    muted: "#c9cfdb",
    faint: "#8a93a6",
    accent: "#ffb84d",
    accentContrast: "#1a1206",
    success: "#2dd4bf",
    warning: "#ffb84d",
    danger: "#ff5d7d",
    info: "#5b8cff",
  },
  typography: {
    fontFamilySans:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif',
    fontFamilyMono:
      'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace',
    fontSizeBase: "16px",
    lineHeightBase: 1.65,
    fontWeightRegular: 400,
    fontWeightMedium: 500,
    fontWeightBold: 700,
  },
  spacing: {
    unit: "4px",
    radiusSm: "4px",
    radiusMd: "8px",
    radiusLg: "12px",
  },
};

export const lightTheme: Theme = {
  ...defaultTheme,
  name: "agentboom-light",
  mode: "light",
  colors: {
    bg: "#f7f8fa",
    bgSoft: "#ffffff",
    bgCard: "#ffffff",
    border: "#e3e6ec",
    text: "#16181d",
    muted: "#3d4350",
    faint: "#7a8291",
    accent: "#c77800",
    accentContrast: "#ffffff",
    success: "#0d9488",
    warning: "#c77800",
    danger: "#dc2652",
    info: "#2f6fed",
  },
};

/**
 * Emit a Theme as CSS custom properties (`--ab-*`). Apply the result to a
 * root element's `style` (or render `<GlobalStyles theme={...} />`).
 */
export function themeToCssVars(theme: Theme): Record<string, string> {
  const c = theme.colors;
  const t = theme.typography;
  const s = theme.spacing;
  return {
    "--ab-bg": c.bg,
    "--ab-bg-soft": c.bgSoft,
    "--ab-bg-card": c.bgCard,
    "--ab-border": c.border,
    "--ab-text": c.text,
    "--ab-muted": c.muted,
    "--ab-faint": c.faint,
    "--ab-accent": c.accent,
    "--ab-accent-contrast": c.accentContrast,
    "--ab-success": c.success,
    "--ab-warning": c.warning,
    "--ab-danger": c.danger,
    "--ab-info": c.info,
    "--ab-font-sans": t.fontFamilySans,
    "--ab-font-mono": t.fontFamilyMono,
    "--ab-font-size-base": t.fontSizeBase,
    "--ab-line-height-base": String(t.lineHeightBase),
    "--ab-fw-regular": String(t.fontWeightRegular),
    "--ab-fw-medium": String(t.fontWeightMedium),
    "--ab-fw-bold": String(t.fontWeightBold),
    "--ab-space-unit": s.unit,
    "--ab-radius-sm": s.radiusSm,
    "--ab-radius-md": s.radiusMd,
    "--ab-radius-lg": s.radiusLg,
    "--ab-mode": theme.mode,
  };
}
