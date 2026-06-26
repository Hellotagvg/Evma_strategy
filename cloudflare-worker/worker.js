/**
 * Kinetic Terminal — Cloudflare CORS Proxy Worker
 *
 * Forwards API requests from the browser to Bybit / Hyperliquid
 * and adds the necessary CORS headers so the browser doesn't block them.
 *
 * Usage from your dashboard:
 *   fetch("https://your-worker.workers.dev/?url=https://api.bybit.com/v5/market/tickers&category=linear")
 *
 * The worker strips the `url` query param, forwards the remaining
 * query params plus headers to the target, and returns the response
 * with open CORS headers.
 */

// Allowed upstream origins — acts as a basic security allowlist.
// Only requests to these hosts will be proxied.
const ALLOWED_HOSTS = [
  "api.bybit.com",
  "api-demo.bybit.com",
  "api-testnet.bybit.com",
  "api.hyperliquid.xyz",
  "api.hyperliquid-testnet.xyz",
];

// Your dashboard's origin — keeps the worker private to your app.
// Set to "*" to allow any origin (less secure but easier for testing).
// Example: "https://yourusername.github.io"
const ALLOWED_ORIGIN = "*";

export default {
  async fetch(request, env, ctx) {
    // ── Handle CORS pre-flight (OPTIONS) ──────────────────────────────────
    if (request.method === "OPTIONS") {
      return corsResponse(null, 204);
    }

    // ── Parse the incoming request URL ───────────────────────────────────
    const incomingUrl = new URL(request.url);

    // The target URL is passed as the `url` query parameter.
    // Keep backward compatibility with older clients using `target`.
    const targetParam = incomingUrl.searchParams.get("url") || incomingUrl.searchParams.get("target");
    if (!targetParam) {
      return corsResponse(
        JSON.stringify({ error: "Missing required `url` (or legacy `target`) query parameter." }),
        400,
        { "Content-Type": "application/json" }
      );
    }

    // Build the target URL, forwarding any remaining query params
    let targetUrl;
    try {
      targetUrl = new URL(targetParam);
    } catch {
      return corsResponse(
        JSON.stringify({ error: "Invalid `url` parameter — must be a full URL." }),
        400,
        { "Content-Type": "application/json" }
      );
    }

    // Forward all query params except `url` itself to the upstream target
    for (const [key, value] of incomingUrl.searchParams) {
      if (key !== "url") {
        targetUrl.searchParams.set(key, value);
      }
    }

    // ── Security: validate the upstream host ─────────────────────────────
    const targetHost = targetUrl.hostname;
    if (!ALLOWED_HOSTS.some((h) => targetHost === h || targetHost.endsWith(`.${h}`))) {
      return corsResponse(
        JSON.stringify({
          error: `Host "${targetHost}" is not in the allowlist.`,
          allowedHosts: ALLOWED_HOSTS,
        }),
        403,
        { "Content-Type": "application/json" }
      );
    }

    // ── Forward the request upstream ─────────────────────────────────────
    // Copy safe headers from the incoming request (API keys etc.)
    const forwardHeaders = new Headers();
    const SAFE_HEADERS = [
      "content-type",
      "x-bapi-api-key",
      "x-bapi-timestamp",
      "x-bapi-sign",
      "x-bapi-recv-window",
    ];
    for (const header of SAFE_HEADERS) {
      const val = request.headers.get(header);
      if (val) forwardHeaders.set(header, val);
    }

    let upstreamResponse;
    try {
      upstreamResponse = await fetch(targetUrl.toString(), {
        method: request.method,
        headers: forwardHeaders,
        // Forward the body for POST/PUT requests (signed order submissions)
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "follow",
      });
    } catch (err) {
      return corsResponse(
        JSON.stringify({ error: "Failed to reach upstream API.", detail: err.message }),
        502,
        { "Content-Type": "application/json" }
      );
    }

    // ── Return the upstream response with CORS headers ───────────────────
    const responseBody = await upstreamResponse.arrayBuffer();
    const responseHeaders = new Headers();
    // Forward safe upstream headers
    for (const [key, value] of upstreamResponse.headers) {
      if (["content-type", "content-encoding", "x-ratelimit-limit-status"].includes(key)) {
        responseHeaders.set(key, value);
      }
    }

    return corsResponse(responseBody, upstreamResponse.status, Object.fromEntries(responseHeaders));
  },
};

// ── Helper: wrap any response with CORS headers ──────────────────────────────
function corsResponse(body, status = 200, extraHeaders = {}) {
  const headers = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type, X-BAPI-API-KEY, X-BAPI-TIMESTAMP, X-BAPI-SIGN, X-BAPI-RECV-WINDOW",
    "Access-Control-Max-Age": "86400",
    ...extraHeaders,
  };

  return new Response(body, { status, headers });
}
