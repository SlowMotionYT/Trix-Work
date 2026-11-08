import { store, json, isRunner, validJob, purge } from "../lib/common.mjs";

export default async (req) => {
  if (req.method !== "POST") return json({ error: "Metodo no permitido" }, 405);
  if (!isRunner(req)) return json({ error: "No autorizado" }, 401);

  let b;
  try {
    b = await req.json();
  } catch {
    return json({ error: "Solicitud invalida" }, 400);
  }
  if (!validJob(b.job)) return json({ error: "Trabajo invalido" }, 400);

  const s = store();
  const cur = (await s.get(`meta/${b.job}`, { type: "json" })) || { createdAt: Date.now() };
  const next = { ...cur };
  for (const k of ["status", "stage", "progress", "message", "errors", "outName", "outChunks", "outSize"]) {
    if (b[k] !== undefined) next[k] = b[k];
  }
  await s.setJSON(`meta/${b.job}`, next);
  if (b.purgeSrc) await purge(s, `src/${b.job}/`);
  return json({ ok: true });
};
