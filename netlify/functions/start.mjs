import { store, json, accessOk, validJob, TARGETS, MAX_CHUNKS } from "../lib/common.mjs";

export default async (req) => {
  if (req.method !== "POST") return json({ error: "Metodo no permitido" }, 405);
  if (!accessOk(req)) return json({ error: "Clave incorrecta" }, 401);

  let b;
  try {
    b = await req.json();
  } catch {
    return json({ error: "Solicitud invalida" }, 400);
  }
  const { job, chunks, target } = b;
  if (!validJob(job) || !TARGETS.includes(target) || !Number.isInteger(chunks) || chunks < 1 || chunks > MAX_CHUNKS) {
    return json({ error: "Datos invalidos" }, 400);
  }
  const name = String(b.name || "app").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 60) || "app";

  const { GH_TOKEN, GH_REPO } = process.env;
  if (!GH_TOKEN || !GH_REPO || !process.env.BUILD_SECRET) {
    return json({ error: "Faltan variables de entorno en Netlify" }, 500);
  }

  const s = store();
  const { blobs } = await s.list({ prefix: `src/${job}/` });
  if (blobs.length !== chunks) return json({ error: "Subida incompleta" }, 400);

  await s.setJSON(`meta/${job}`, {
    status: "queued",
    stage: "En cola",
    progress: 1,
    target,
    createdAt: Date.now(),
  });

  const r = await fetch(`https://api.github.com/repos/${GH_REPO}/actions/workflows/build.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "trix-work",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: process.env.GH_REF || "main",
      inputs: {
        job,
        target,
        chunks: String(chunks),
        name,
        site: new URL(req.url).origin,
      },
    }),
  });

  if (r.status !== 204) {
    const detail = await r.text();
    await s.setJSON(`meta/${job}`, {
      status: "failed",
      stage: "Error",
      progress: 0,
      errors: `No se pudo iniciar la compilacion (GitHub ${r.status})\n${detail}`,
      createdAt: Date.now(),
    });
    return json({ error: `No se pudo iniciar la compilacion (GitHub ${r.status})` }, 502);
  }
  return json({ ok: true });
};
