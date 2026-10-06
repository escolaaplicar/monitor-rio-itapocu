// Worker "nivel-rio-corupa": entrega escolaaplicar.com.br/nivel-rio-corupa/ a partir do GitHub Pages.
// Não tem acesso a nada da conta (sem KV, sem segredos, sem banco). Só lê 3 arquivos públicos.
const ORIGEM = "https://escolaaplicar.github.io/monitor-rio-itapocu";
const BASE = "/nivel-rio-corupa";
const ARQUIVOS = { "/": "/index.html", "/index.html": "/index.html", "/dados.json": "/dados.json", "/radar.gif": "/radar.gif" };

const SEGURANCA = {
  "Content-Security-Policy":
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; " +
    "img-src 'self' data: https://escolaaplicar.github.io; connect-src 'self' https://escolaaplicar.github.io https://cloudflareinsights.com; " +
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
  "Strict-Transport-Security": "max-age=31536000",
};
const TIPOS = { "/index.html": "text/html; charset=utf-8", "/dados.json": "application/json; charset=utf-8", "/radar.gif": "image/gif" };

export default {
  async fetch(req) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      return new Response("Método não permitido", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const url = new URL(req.url);
    if (url.pathname === BASE) return Response.redirect(url.origin + BASE + "/", 301);
    const arquivo = ARQUIVOS[url.pathname.slice(BASE.length)];
    if (!arquivo) return new Response("Não encontrado", { status: 404 });

    const html = arquivo === "/index.html";
    const ttl = html ? 300 : 60;
    // cache na borda da Cloudflare: o GitHub recebe no máximo 1 pedido por arquivo a cada 1-5 min por região
    const r = await fetch(ORIGEM + arquivo, { cf: { cacheTtl: ttl, cacheEverything: true } });
    if (!r.ok) return new Response("Painel indisponível no momento. Tente em alguns minutos.", { status: 502 });

    const h = new Headers(SEGURANCA);
    h.set("Content-Type", TIPOS[arquivo]);
    h.set("Cache-Control", `public, max-age=${ttl}`);
    return new Response(req.method === "HEAD" ? null : r.body, { status: 200, headers: h });
  },
};
