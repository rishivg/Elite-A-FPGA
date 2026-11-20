// Bounded, synchronous capture keeps guest scheduling independent of file I/O.
import fs from "node:fs";
import { gzipSync } from "node:zlib";

export class BufferedCapture {
  constructor(file, compressed = false) {
    this.fd = fs.openSync(file, "w");
    this.compressed = compressed;
    this.buffer = Buffer.allocUnsafe(1 << 20);
    this.used = 0;
    this.bytes = 0;
  }

  append(data) {
    if (this.fd === null) throw Error("Capture already closed");
    let offset = 0;
    while (offset < data.length) {
      const count = Math.min(data.length - offset, this.buffer.length - this.used);
      this.buffer.set(data.subarray(offset, offset + count), this.used);
      offset += count;
      this.used += count;
      this.bytes += count;
      if (this.used === this.buffer.length) this.flush();
    }
  }

  flush() {
    if (!this.used) return;
    const chunk = this.buffer.subarray(0, this.used);
    // Concatenated gzip members decompress to the exact original byte stream.
    const output = this.compressed ? gzipSync(chunk, { level: 1 }) : chunk;
    let offset = 0;
    while (offset < output.length) {
      const written = fs.writeSync(this.fd, output, offset, output.length - offset);
      if (!written) throw Error("Capture write made no progress");
      offset += written;
    }
    this.used = 0;
  }

  close() {
    if (this.fd === null) throw Error("Capture already closed");
    try {
      this.flush();
      if (this.compressed && !this.bytes)
        fs.writeSync(this.fd, gzipSync(Buffer.alloc(0), { level: 1 }));
    } finally {
      fs.closeSync(this.fd);
      this.fd = null;
    }
  }
}
