import type { NextConfig } from "next";

// Where the API is. Set at BUILD time (NEXT_PUBLIC_API_URL is inlined into the bundle), and the
// Content-Security-Policy below lets the page talk to exactly this origin and no other.
const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const isProduction = process.env.NODE_ENV === "production";

// Response headers for every page (YGH-115). The policy is production-only: the dev server
// needs eval and websockets for hot reload, which a strict policy would break.
//   script/style 'unsafe-inline'  Next.js writes small inline bootstrap scripts and styles; moving
//                                 to per-request nonces is the stricter option, and a known gap
//   img-src / frame-src blob:     the original document is shown from a blob: URL of the user's file
//   frame-ancestors 'none'        nobody can put this app inside their own page (clickjacking)
const contentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "frame-src blob:",
  `connect-src 'self' ${apiUrl}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  ...(isProduction
    ? [
        { key: "Content-Security-Policy", value: contentSecurityPolicy },
        { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
      ]
    : []),
];

const nextConfig: NextConfig = {
  // The server is just `node server.js` in the production image (see Dockerfile).
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
