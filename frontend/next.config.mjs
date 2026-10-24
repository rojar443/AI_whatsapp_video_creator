/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://localhost:8100";

const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Same-origin API: the browser talks to /api/* on the website, Next proxies to FastAPI.
  // Session cookies therefore stay first-party and API keys never reach the browser.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "X-Frame-Options", value: "DENY" },
      ],
    }];
  },
};

export default nextConfig;
