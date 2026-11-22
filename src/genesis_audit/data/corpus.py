"""Build the project's reproducible 10 train and 10 dev episode directories."""

import argparse
from pathlib import Path

from . import generate_episode

TRAIN = (1, 3, 5, 6, 9, 12, 14, 16, 17, 19)
DEV = (2, 4, 7, 8, 10, 11, 13, 15, 18, 20)
STRESS_INVOICE_COUNTS = {
    2: 55, 3: 64, 5: 78, 6: 58, 8: 71, 11: 68, 16: 63, 18: 77, 19: 80, 20: 72,
}


def build_corpus(root):
    root = Path(root)
    for split, scenarios, base_seed in (("train", TRAIN, 21_000), ("dev", DEV, 32_000)):
        for index, number in enumerate(scenarios, 1):
            config = {"scenario": f"D{number:02d}"}
            if number in STRESS_INVOICE_COUNTS:
                config.update(profile="stress", invoice_count=STRESS_INVOICE_COUNTS[number])
            generate_episode(base_seed + number, config, str(root / split / f"episode-{index:02d}"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build_corpus(parser.parse_args().output)


if __name__ == "__main__":
    main()
