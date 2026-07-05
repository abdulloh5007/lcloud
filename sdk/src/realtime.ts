import type { DbChangeEvent, WatchOptions } from "./types.js";

export function watchEvents(
  url: string,
  onChange: (event: DbChangeEvent) => void,
  options: WatchOptions = {},
): EventSource {
  if (typeof EventSource === "undefined") {
    throw new Error("EventSource is not available in this runtime");
  }
  const source = new EventSource(url, { withCredentials: true });
  source.addEventListener("lcloud.db.change", (event) => {
    onChange(JSON.parse((event as MessageEvent).data) as DbChangeEvent);
  });
  if (options.onError) {
    source.addEventListener("error", options.onError);
  }
  return source;
}
