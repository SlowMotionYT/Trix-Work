import { store, json, validJob } from "../lib/common.mjs";

export default async (req) => {
  const job = new URL(req.url).searchParams.get("job");
  if (!validJob(job)) return json({ error: "Solicitud invalida" }, 400);
  const meta = await store().get(`meta/${job}`, { type: "json" });
  if (!meta) return json({ status: "unknown" });
  const { status, stage, progress, message, errors, outName, outChunks, outSize } = meta;
  return json({ status, stage, progress, message, errors, outName, outChunks, outSize });
};
