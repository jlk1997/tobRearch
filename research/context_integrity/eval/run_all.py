"""Run unit checks, attacks, and defence comparison.

Run: python -m research.context_integrity.eval.run_all
"""

from __future__ import annotations

from research.context_integrity.eval import test_crypto, run_attacks, run_defense


def main() -> None:
    test_crypto.main()
    run_attacks.main()
    run_defense.main()


if __name__ == "__main__":
    main()
