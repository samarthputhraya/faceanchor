# Rebuild the FaceAnchor zk toolchain from nothing.
#   powershell -ExecutionPolicy Bypass -File zk/build.ps1
#
# Produces build/facematch_final.zkey (proving key, local only) and the two
# committed artifacts: verification_key.json and ../contracts/Groth16Verifier.sol.
#
# NOTE ON THE CEREMONY: the public Hermez ptau mirrors
# (storage.googleapis.com/zkevm/ptau, hermez.s3-eu-west-1.amazonaws.com) both
# return AccessDenied as of 2026-09, so this script runs its OWN powers-of-tau.
# That makes the setup toxic-waste-trusted: fine for a demo, NOT production.
# A real deployment must use the Perpetual Powers of Tau.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path build, bin | Out-Null

$CIRCOM_VERSION = "v2.2.3"
$POWER = 15          # 2^15 = 32768 constraints; the circuit uses 17,797

if (-not (Test-Path bin/circom.exe)) {
  Write-Host "==> downloading circom $CIRCOM_VERSION"
  curl.exe -sL -o bin/circom.exe `
    "https://github.com/iden3/circom/releases/download/$CIRCOM_VERSION/circom-windows-amd64.exe"
}
if (-not (Test-Path node_modules)) {
  Write-Host "==> npm install"
  npm install --no-audit --no-fund
}

# snarkjs is invoked through node directly: piping `npx snarkjs` into another
# command buffers and stalls on Windows.
$SNARKJS = "node_modules/snarkjs/build/cli.cjs"
function Snark { node --max-old-space-size=8192 $SNARKJS @args }

Write-Host "==> compiling circuit"
./bin/circom.exe facematch.circom --r1cs --wasm --sym -o build

if (-not (Test-Path build/pot${POWER}_final.ptau)) {
  Write-Host "==> powers of tau (local ceremony, not production)"
  Snark powersoftau new bn128 $POWER build/pot${POWER}_0000.ptau
  Snark powersoftau contribute build/pot${POWER}_0000.ptau build/pot${POWER}_0001.ptau `
    --name="FaceAnchor local ceremony" -e="faceanchor-hh-goa-2026-local-entropy-not-production"
  Snark powersoftau prepare phase2 build/pot${POWER}_0001.ptau build/pot${POWER}_final.ptau
}

Write-Host "==> groth16 setup"
Snark groth16 setup build/facematch.r1cs build/pot${POWER}_final.ptau build/facematch_0000.zkey
Snark zkey contribute build/facematch_0000.zkey build/facematch_final.zkey `
  --name="FaceAnchor phase2 local" -e="faceanchor-phase2-local-entropy-not-production"

Write-Host "==> exporting verification key and Solidity verifier"
Snark zkey export verificationkey build/facematch_final.zkey build/verification_key.json
Snark zkey export solidityverifier build/facematch_final.zkey build/Groth16Verifier.sol

Copy-Item build/verification_key.json verification_key.json -Force
Copy-Item build/Groth16Verifier.sol ../contracts/Groth16Verifier.sol -Force

Write-Host "==> done. proving key: build/facematch_final.zkey"
