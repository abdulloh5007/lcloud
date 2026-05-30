/**
 * LC2 client-side signing.
 *
 * Mirrors lcloud/crypto/lc2.py canonical layout:
 *   payload = sha256_bytes (32) || ts (8 bytes, big-endian) || pubkey (32)
 *
 * Ed25519.sign(privkey_seed, payload) → 64-byte signature, transmitted
 * to the server as a hex string in the multipart upload form.
 */
import { bytesToHex, hexToBytes, sha256OfBlob, signBytes } from './seed'

/** Build the canonical 72-byte payload that gets signed. */
export function canonicalPayload(
  sha256: Uint8Array,
  ts: number,
  pubkey: Uint8Array
): Uint8Array {
  if (sha256.length !== 32) throw new Error('sha256 must be 32 bytes')
  if (pubkey.length !== 32) throw new Error('pubkey must be 32 bytes')

  const out = new Uint8Array(32 + 8 + 32)
  out.set(sha256, 0)
  // 8-byte big-endian unsigned int. JS numbers are 53-bit safe — we never
  // hit unix-time near 2^53 (year ~285,000) so a plain DataView write is fine.
  const view = new DataView(out.buffer, 32, 8)
  // High 32 bits will be 0 for any reasonable unix timestamp
  view.setUint32(0, Math.floor(ts / 0x100000000), false)
  view.setUint32(4, ts >>> 0, false)
  out.set(pubkey, 40)
  return out
}

export interface Lc2SignedUpload {
  fileSha256Hex: string
  signatureHex: string
  ts: number
  pubkeyHex: string
}

/**
 * Compute SHA-256 of the file, then sign (sha256 || ts || pubkey) with
 * the user's privkey. The privkey is consumed but not persisted.
 */
export async function signFileForUpload(
  file: Blob,
  pubkey: Uint8Array,
  privkeySeed: Uint8Array
): Promise<Lc2SignedUpload> {
  const sha = await sha256OfBlob(file)
  const ts = Math.floor(Date.now() / 1000)
  const payload = canonicalPayload(sha, ts, pubkey)
  const sig = await signBytes(privkeySeed, payload)
  return {
    fileSha256Hex: bytesToHex(sha),
    signatureHex: bytesToHex(sig),
    ts,
    pubkeyHex: bytesToHex(pubkey),
  }
}

/** Verify a signature received from the server (e.g. for a downloaded file). */
export { hexToBytes }
