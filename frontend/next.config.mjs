/** @type {import('next').NextConfig} */
const API_BASE = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  async rewrites() {
    // Proxy API calls to the FastAPI backend during development.
    return [{ source: "/api/:path*", destination: `${API_BASE}/api/:path*` }];
  },
  experimental: {
    // Default is 10mb. The largest plan accepts a 200 MB collection plus a
    // 200 MB environment in one multipart body; the API enforces each plan's limit.
    middlewareClientMaxBodySize: "450mb",
  },
};

export default nextConfig;
