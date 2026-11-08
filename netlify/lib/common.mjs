import { getStore } from "@netlify/blobs";

export const store = () => getStore({ name: "trix", consistency: "strong" });

export const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

export const isRunner = (req) => {
  const s = process.env.BUILD_SECRET;
  return !!s && req.headers.get("x-trix-secret") === s;
};

export const accessOk = (req) => {
  const k = process.env.ACCESS_KEY;
  return !k || req.headers.get("x-access-key") === k;
};

export const validJob = (j) => typeof j === "string" && /^[a-f0-9]{32}$/.test(j);

export const TARGETS = ["exe", "apk-debug", "apk-release", "aab", "jar"];
export const MAX_CHUNKS = 300;
export const MAX_CHUNK_BYTES = 4 * 1024 * 1024;

export async function purge(s, prefix) {
  const { blobs } = await s.list({ prefix });
  await Promise.all(blobs.map((b) => s.delete(b.key)));
}
