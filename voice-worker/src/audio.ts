/** Decode one G.711 μ-law octet to signed 16-bit linear PCM. */
export function decodeMulawSample(value: number): number {
  const sample = (~value) & 0xff;
  const sign = sample & 0x80;
  const exponent = (sample >> 4) & 0x07;
  const mantissa = sample & 0x0f;
  const magnitude = ((mantissa << 3) + 0x84) << exponent;
  const linear = magnitude - 0x84;
  if (linear === 0) return 0;
  return sign ? -linear : linear;
}

/**
 * Convert mono 8 kHz G.711 μ-law into mono 16 kHz signed PCM16 little-endian.
 * Linear interpolation inserts one midpoint sample between each source sample.
 */
export function decodeMulaw8kToLinear16k(input: Uint8Array): Uint8Array {
  const output = new Uint8Array(input.length * 4);
  const view = new DataView(output.buffer, output.byteOffset, output.byteLength);

  for (let index = 0; index < input.length; index += 1) {
    const current = decodeMulawSample(input[index]);
    const next = index + 1 < input.length ? decodeMulawSample(input[index + 1]) : current;
    const midpoint = Math.round((current + next) / 2);
    view.setInt16(index * 4, current, true);
    view.setInt16(index * 4 + 2, midpoint, true);
  }

  return output;
}
