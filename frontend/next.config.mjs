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
    // Default is 10mb; the app advertises a 25 MiB per-file upload limit,
    // so a two-file multipart body can exceed the proxy's default cap.
    middlewareClientMaxBodySize: "60mb",
  },
};

export default nextConfig;
