/**
 * Minimal platform client + templating used by the renderers.
 *
 * The dashboard talks to the platform gateway over HTTP. These helpers are
 * deliberately tiny and dependency-free so they work in Next.js (client or
 * server components) and in any host app.
 */
import type { MiniAppEntry } from "./manifest.js";

export interface PlatformClientOptions {
  /** Base URL of the platform gateway, e.g. "http://127.0.0.1:8000". */
  baseUrl: string;
  /**
   * Bearer token for the gateway API. Every non-public route requires it —
   * the hard public boundary is enforced in the gateway and cannot be
   * weakened by configuration.
   */
  token?: string;
  /** Optional admin credentials for /admin/* endpoints (legacy). */
  admin?: { username: string; password: string };
  /** Optional fetch implementation (defaults to global fetch). */
  fetchImpl?: typeof fetch;
}

export class PlatformClient {
  constructor(private opts: PlatformClientOptions) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = { ...extra };
    if (this.opts.token) {
      h["authorization"] = `Bearer ${this.opts.token}`;
    } else if (this.opts.admin) {
      const { username, password } = this.opts.admin;
      if (typeof btoa !== "undefined") {
        h["authorization"] = `Basic ${btoa(`${username}:${password}`)}`;
      }
    }
    return h;
  }

  private get fetch(): typeof fetch {
    return this.opts.fetchImpl ?? fetch;
  }

  /** Absolute URL for a platform path (leading slash optional). */
  url(path: string): string {
    const base = this.opts.baseUrl.replace(/\/$/, "");
    return path.startsWith("/") ? `${base}${path}` : `${base}/${path}`;
  }

  async getJson<T = unknown>(path: string): Promise<T> {
    const resp = await this.fetch(this.url(path), { headers: this.headers() });
    if (!resp.ok) throw new Error(`GET ${path} -> HTTP ${resp.status}`);
    return (await resp.json()) as T;
  }

  async sendJson<T = unknown>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const resp = await this.fetch(this.url(path), {
      method,
      headers: this.headers(
        body === undefined ? {} : { "content-type": "application/json" },
      ),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`${method} ${path} -> HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }
    return (await resp.json()) as T;
  }

  /** The mini-app catalog (all apps + their manifests). */
  async catalog(): Promise<{ apps: MiniAppEntry[] }> {
    return this.getJson("/api/catalog");
  }
}

/**
 * Resolve `{{placeholders}}` in a template string against a data row.
 * Supports dotted paths ({{user.name}}) and falls back to "" when absent.
 */
export function resolveTemplate(template: string, data: Record<string, unknown>): string {
  return template.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_m, path: string) => {
    const value = getPath(data, path);
    return value === undefined || value === null ? "" : String(value);
  });
}

/** Read a dotted path from an object ("a.b.c"). */
export function getPath(data: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => {
    if (acc && typeof acc === "object" && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, data);
}

/** Resolve every templated value inside an action's path/body against a row. */
export function resolveAction(
  path: string,
  body: Record<string, unknown> | undefined,
  row: Record<string, unknown>,
): { path: string; body?: Record<string, unknown> } {
  const resolvedPath = resolveTemplate(path, row);
  let resolvedBody: Record<string, unknown> | undefined;
  if (body) {
    resolvedBody = {};
    for (const [k, v] of Object.entries(body)) {
      resolvedBody[k] = typeof v === "string" ? resolveTemplate(v, row) : v;
    }
  }
  return { path: resolvedPath, body: resolvedBody };
}
