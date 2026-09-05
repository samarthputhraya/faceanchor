"""Compile FaceAnchorRegistry once, then always load the committed artifact.

The build JSON (abi + bytecode) is committed so a demo, a fresh clone or CI
never depends on downloading the Solidity compiler.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

SOLC_VERSION = "0.8.26"
SOURCE = config.CONTRACTS_DIR / "FaceAnchorRegistry.sol"
BUILD = config.CONTRACTS_DIR / "build" / "FaceAnchorRegistry.json"
NAME = "FaceAnchorRegistry"


def compile_contract(write: bool = True) -> dict:
    """Compile with py-solc-x (downloads a prebuilt solc binary, no compiler)."""
    import solcx

    installed = [str(v) for v in solcx.get_installed_solc_versions()]
    if SOLC_VERSION not in installed:
        solcx.install_solc(SOLC_VERSION)

    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {f"{NAME}.sol": {"content": SOURCE.read_text(encoding="utf-8")}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "metadata"]}},
            },
        },
        solc_version=SOLC_VERSION,
    )
    c = out["contracts"][f"{NAME}.sol"][NAME]
    artifact = {
        "contractName": NAME,
        "solcVersion": SOLC_VERSION,
        "optimizer": {"enabled": True, "runs": 200},
        "abi": c["abi"],
        "bytecode": "0x" + c["evm"]["bytecode"]["object"],
        "source": f"{NAME}.sol",
    }
    if write:
        BUILD.parent.mkdir(parents=True, exist_ok=True)
        BUILD.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def load_artifact() -> dict:
    if BUILD.exists():
        return json.loads(BUILD.read_text(encoding="utf-8"))
    return compile_contract()


def abi() -> list:
    return load_artifact()["abi"]
