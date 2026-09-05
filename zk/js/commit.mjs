/*
 * Poseidon commitment over an int8 embedding, byte-identical to the
 * CommitVector template in facematch.circom.
 *
 * Any drift between this file and the circuit silently breaks proving, so the
 * packing constants live in one place and both sides are covered by a
 * round-trip test (tests/test_zk_commitment.py).
 *
 * Usage:  node js/commit.mjs '{"vector":[...int8...],"salt":"<hex>"}'
 *   or    echo '<json>' | node js/commit.mjs
 * Output: {"commitment":"<decimal field element>"}
 */
import { buildPoseidon } from "circomlibjs";

// BN254 scalar field order. A 32-byte salt EXCEEDS this and would silently
// wrap, so FaceAnchor generates a distinct 31-byte salt for the zk commitment
// rather than reusing the 32-byte one behind the sha256 commitment.
export const FIELD_R =
  21888242871839275222246405745257275088548364400416034343698204186575808495617n;

export const BYTES_PER_ELEM = 31;   // 31 bytes = 248 bits, safely under the BN254 field
export const HALF = 9;              // circomlib Poseidon caps out near arity 15

/** int8 vector -> offset bytes in [0,255], matching a[i] = embedding[i] + 128. */
export function offsetBytes(vector) {
  return vector.map((v) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < -128 || n > 127) {
      throw new Error(`embedding element out of int8 range: ${v}`);
    }
    return n + 128;
  });
}

/** Little-endian pack into ceil(n/31) field elements. Mirrors PackBytes(). */
export function packBytes(bytes, per = BYTES_PER_ELEM) {
  const out = [];
  for (let k = 0; k * per < bytes.length; k++) {
    let acc = 0n;
    let mul = 1n;
    for (let j = 0; j < per; j++) {
      const idx = k * per + j;
      if (idx < bytes.length) {
        acc += BigInt(bytes[idx]) * mul;
        mul *= 256n;
      }
    }
    out.push(acc);
  }
  return out;
}

/** Poseidon(9) | Poseidon(rest + salt) -> Poseidon(2). Mirrors CommitVector(). */
export function commitPacked(poseidon, packed, salt) {
  const F = poseidon.F;
  const h1 = poseidon(packed.slice(0, HALF));
  const h2 = poseidon([...packed.slice(HALF), salt]);
  return F.toObject(poseidon([F.toObject(h1), F.toObject(h2)]));
}

export async function commit(vector, saltHex) {
  const poseidon = await buildPoseidon();
  const packed = packBytes(offsetBytes(vector));
  const salt = BigInt("0x" + saltHex.replace(/^0x/, ""));
  if (salt >= FIELD_R) {
    throw new Error(
      `zk salt does not fit the BN254 scalar field (${saltHex.length / 2} bytes). ` +
      "Use the 31-byte zk_salt, not the 32-byte sha256 salt."
    );
  }
  return commitPacked(poseidon, packed, salt);
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

if (process.argv[1] && process.argv[1].endsWith("commit.mjs")) {
  const raw = process.argv[2] ?? (await readStdin());
  const { vector, salt } = JSON.parse(raw);
  const c = await commit(vector, salt);
  process.stdout.write(JSON.stringify({ commitment: c.toString() }) + "\n");
}
