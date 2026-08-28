/** @type {import('next').NextConfig} */
const PLATFORM_URL = process.env.AGENTBOOM_PLATFORM_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // Server mode (not static export) so the rewrites below can proxy the
  // browser's /api/* calls to the platform gateway without CORS.
  // No trailingSlash: it 308-redirects /api/* and /health to a trailing-slash
  // form that no longer matches the rewrite sources, breaking the proxy.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${PLATFORM_URL}/api/:path*` },
      { source: "/public/:path*", destination: `${PLATFORM_URL}/public/:path*` },
      { source: "/admin/:path*", destination: `${PLATFORM_URL}/admin/:path*` },
      { source: "/health", destination: `${PLATFORM_URL}/health` },
    ];
  },
};

export default nextConfig;
