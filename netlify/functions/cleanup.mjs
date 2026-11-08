import { store, purge } from "../lib/common.mjs";

export default async () => {
  const s = store();
  const { blobs } = await s.list({ prefix: "meta/" });
  const limit = 3 * 60 * 60 * 1000;
  for (const b of blobs) {
    const meta = await s.get(b.key, { type: "json" });
    if (!meta || Date.now() - (meta.createdAt || 0) > limit) {
      const job = b.key.slice(5);
      await purge(s, `src/${job}/`);
      await purge(s, `out/${job}/`);
      await s.delete(b.key);
    }
  }
};

export const config = { schedule: "@hourly" };
