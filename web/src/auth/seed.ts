/**
 * BIP39 mnemonic ↔ Ed25519 keypair, in the browser.
 *
 * The chain mirrors the server (lcloud/auth/seed.py):
 *
 *   mnemonic (12 or 24 EN words)
 *       ↓ BIP39 PBKDF2-HMAC-SHA512
 *   seed (64 bytes)
 *       ↓ first 32 bytes
 *   Ed25519 seed
 *       ↓ getPublicKeyAsync(seed)
 *   pubkey (32 bytes)
 *
 * The private key NEVER leaves this module. Components hold a
 * `DerivedIdentity` (containing privkey bytes) only inside login flow;
 * after the auth challenge is signed, the privkey is discarded.
 *
 * Word counts:
 *   12 words  →  128 bits entropy
 *   24 words  →  256 bits entropy
 * Both are unbruteforceable.
 */
import { generateMnemonic, mnemonicToSeed, validateMnemonic } from '@scure/bip39'
import { wordlist } from '@scure/bip39/wordlists/english'
import {
  etc as edEtc,
  getPublicKeyAsync,
  signAsync,
  verifyAsync,
} from '@noble/ed25519'
import { sha512 } from '@noble/hashes/sha512'
import { sha256 } from '@noble/hashes/sha256'

// noble-ed25519 v2 needs sha512 to be wired explicitly
edEtc.sha512Sync = (...m) => sha512(concatBytes(...m))

export type WordCount = 12 | 24

export interface DerivedIdentity {
  mnemonic: string
  pubkey: Uint8Array  // 32 bytes
  privkeySeed: Uint8Array  // 32 bytes — DO NOT persist
  pubkeyHex: string
}

/** Generate a fresh BIP39 mnemonic of the requested length. */
export function generateSeedPhrase(words: WordCount = 12): string {
  // @scure/bip39 expects strength in BITS for generateMnemonic
  const strength = words === 24 ? 256 : 128
  return generateMnemonic(wordlist, strength)
}

/** Returns true iff the mnemonic is a valid BIP39 phrase. */
export function isValidSeedPhrase(mnemonic: string): boolean {
  try {
    return validateMnemonic(mnemonic.trim(), wordlist)
  } catch {
    return false
  }
}

/**
 * Derive an Ed25519 keypair from a BIP39 mnemonic.
 * `passphrase` is the BIP39 "25th word" — keep empty by default
 * (the server uses empty passphrase too).
 */
export async function deriveKeypair(
  mnemonic: string,
  passphrase = ''
): Promise<DerivedIdentity> {
  if (!isValidSeedPhrase(mnemonic)) {
    throw new Error('Invalid BIP39 mnemonic phrase')
  }
  const trimmed = mnemonic.trim()
  // @scure/bip39 mnemonicToSeed returns 64-byte seed
  const seed64 = await mnemonicToSeed(trimmed, passphrase)
  const seed32 = seed64.slice(0, 32)
  const pubkey = await getPublicKeyAsync(seed32)
  return {
    mnemonic: trimmed,
    pubkey,
    privkeySeed: seed32,
    pubkeyHex: bytesToHex(pubkey),
  }
}

// ----------------------------------------------------------- crypto helpers

/** Hex-encode a Uint8Array (lowercase, no 0x prefix). */
export function bytesToHex(b: Uint8Array): string {
  let s = ''
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0')
  return s
}

/** Hex-decode → Uint8Array. */
export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex
  if (clean.length % 2 !== 0) throw new Error('hex must be even-length')
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) {
    const byte = parseInt(clean.substr(i * 2, 2), 16)
    if (Number.isNaN(byte)) throw new Error(`invalid hex at ${i * 2}`)
    out[i] = byte
  }
  return out
}

function concatBytes(...arrs: Uint8Array[]): Uint8Array {
  let total = 0
  for (const a of arrs) total += a.length
  const out = new Uint8Array(total)
  let pos = 0
  for (const a of arrs) {
    out.set(a, pos)
    pos += a.length
  }
  return out
}

/** Sign arbitrary bytes with an Ed25519 privkey seed (32 bytes). */
export async function signBytes(
  privkeySeed: Uint8Array,
  message: Uint8Array
): Promise<Uint8Array> {
  return await signAsync(message, privkeySeed)
}

/** Verify a detached Ed25519 signature. */
export async function verifyBytes(
  pubkey: Uint8Array,
  message: Uint8Array,
  signature: Uint8Array
): Promise<boolean> {
  try {
    return await verifyAsync(signature, message, pubkey)
  } catch {
    return false
  }
}

/** Compute SHA-256 of a Blob/File using the noble lib (no Web Crypto required). */
export async function sha256OfBlob(blob: Blob): Promise<Uint8Array> {
  const buf = new Uint8Array(await blob.arrayBuffer())
  return sha256(buf)
}

/** Compute SHA-256 of arbitrary bytes. */
export function sha256OfBytes(bytes: Uint8Array): Uint8Array {
  return sha256(bytes)
}

export { wordlist as bip39Wordlist }
