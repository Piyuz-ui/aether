/**
 * Server-Sent Events over fetch(). EventSource can only GET, but chat is a POST,
 * so this reads the response body as a stream and parses the SSE wire format.
 * Handles \r\n or \n line endings, multi-line data, and `: ping` keep-alive comments.
 */
export type SSEMessage = { event: string; data: string; id?: string };

export async function* readSSE(response: Response, signal?: AbortSignal): AsyncGenerator<SSEMessage> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Events end with a blank line.
      let match: RegExpExecArray | null;
      const boundary = /\r?\n\r?\n/;
      while ((match = boundary.exec(buffer))) {
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const msg = parseBlock(block);
        if (msg) yield msg;
      }
    }
    const tail = parseBlock(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

function parseBlock(block: string): SSEMessage | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue; // comment / keep-alive ping
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  return data.length ? { event, data: data.join("\n"), id } : null;
}
