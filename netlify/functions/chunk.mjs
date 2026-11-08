import { store, json, isRunner, accessOk, validJob, MAX_CHUNKS, MAX_CHUNK_BYTES } from "../lib/common.mjs";

export default async (req) => {
  const u = new URL(req.url);
  const kind = u.searchParams.get("kind");
  const job = u.searchParams.get("job");
  const i = Number(u.searchParams.get("i"));
  if (!validJob(job) || !["src", "out"].includes(kind) || !Number.isInteger(i) || i < 0 || i >= MAX_CHUNKS) {
    return json({ error: "Solicitud invalida" }, 400);
  }
  const s = store();
  const key = `${kind}/${job}/${String(i).padStart(4, "0")}`;

  if (req.method === "POST") {
    const allowed = kind === "out" ? isRunner(req) : accessOk(req);
    if (!allowed) return json({ error: "No autorizado" }, 401);
    const buf = await req.arrayBuffer();
    if (buf.byteLength > MAX_CHUNK_BYTES) return json({ error: "Fragmento demasiado grande" }, 413);
    if (kind === "src" && i === 0) {
      const meta = await s.get(`meta/${job}`, { type: "json" });
      if (!meta) {
        await s.setJSON(`meta/${job}`, {
          status: "uploading",
          stage: "Subiendo",
          progress: 0,
          createdAt: Date.now(),
        });
      }
    }
    await s.set(key, buf);
    return json({ ok: true });
  }

  if (req.method === "GET") {
    if (kind === "src" && !isRunner(req)) return json({ error: "No autorizado" }, 401);
    const data = await s.get(key, { type: "arrayBuffer" });
    if (!data) return json({ error: "No existe" }, 404);
    return new Response(data, {
      headers: { "content-type": "application/octet-stream", "cache-control": "no-store" },
    });
  }

  return json({ error: "Metodo no permitido" }, 405);
};
