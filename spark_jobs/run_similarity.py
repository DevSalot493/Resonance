import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spark_jobs.similarity_compute import run_similarity_job

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Resonance similarity computation job"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Minimum similarity score to store (default: 0.05)",
    )
    args = parser.parse_args()

    count = run_similarity_job(threshold=args.threshold)
    sys.exit(0 if count > 0 else 1)