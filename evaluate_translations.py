#!/usr/bin/env python3
"""
evaluate_translations.py

Compares a machine/system translation against a professional (reference)
translation using two metrics:
  - chrF++   : fast, no model download needed, works well for Arabic morphology
  - COMET    : neural metric, best correlation with human judgment (needs
               source text + reference + hypothesis, downloads a model on
               first run)

Input: a CSV file with three columns (source, reference, hypothesis).
Output: a CSV with per-row scores + a summary printed to the console.

Usage:
    python evaluate_translations.py input.csv output.csv
    python evaluate_translations.py input.csv output.csv --no-comet
"""

import argparse
import csv
import sys


def load_rows(input_path, source_col, reference_col, hypothesis_col):
    rows = []
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in (source_col, reference_col, hypothesis_col) if c not in reader.fieldnames]
        if missing:
            sys.exit(
                f"Missing column(s) in CSV: {missing}\n"
                f"Found columns: {reader.fieldnames}\n"
                f"Use --source-col / --reference-col / --hypothesis-col to match your file."
            )
        for row in reader:
            rows.append(row)
    if not rows:
        sys.exit("Input CSV has no data rows.")
    return rows


def compute_chrf(rows, source_col, reference_col, hypothesis_col):
    import sacrebleu

    scores = []
    for row in rows:
        ref = row[reference_col]
        hyp = row[hypothesis_col]
        result = sacrebleu.sentence_chrf(hyp, [ref], word_order=2)  # chrF++
        scores.append(result.score)
    return scores


def compute_comet(rows, source_col, reference_col, hypothesis_col, model_name):
    from comet import download_model, load_from_checkpoint

    print(f"Downloading/loading COMET model '{model_name}' (first run only, ~1-2GB)...")
    model_path = download_model(model_name)
    model = load_from_checkpoint(model_path)

    data = [
        {"src": row[source_col], "mt": row[hypothesis_col], "ref": row[reference_col]}
        for row in rows
    ]
    output = model.predict(data, batch_size=8, gpus=0)
    return list(output.scores)


def main():
    parser = argparse.ArgumentParser(description="Evaluate translation quality vs a professional reference.")
    parser.add_argument("input_csv", help="CSV with source, reference, hypothesis columns")
    parser.add_argument("output_csv", help="Where to write per-row scores")
    parser.add_argument("--source-col", default="source")
    parser.add_argument("--reference-col", default="reference")
    parser.add_argument("--hypothesis-col", default="hypothesis")
    parser.add_argument("--no-comet", action="store_true", help="Skip COMET, only compute chrF++")
    parser.add_argument(
        "--comet-model",
        default="Unbabel/wmt22-comet-da",
        help="COMET model to use (default: Unbabel/wmt22-comet-da)",
    )
    args = parser.parse_args()

    rows = load_rows(args.input_csv, args.source_col, args.reference_col, args.hypothesis_col)
    print(f"Loaded {len(rows)} rows.")

    print("Computing chrF++ ...")
    chrf_scores = compute_chrf(rows, args.source_col, args.reference_col, args.hypothesis_col)

    comet_scores = None
    if not args.no_comet:
        try:
            comet_scores = compute_comet(
                rows, args.source_col, args.reference_col, args.hypothesis_col, args.comet_model
            )
        except Exception as e:
            print(f"COMET failed ({e}). Continuing with chrF++ only.", file=sys.stderr)

    # Write per-row results
    fieldnames = [args.source_col, args.reference_col, args.hypothesis_col, "chrf++"]
    if comet_scores is not None:
        fieldnames.append("comet")

    with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, row in enumerate(rows):
            out_row = {
                args.source_col: row[args.source_col],
                args.reference_col: row[args.reference_col],
                args.hypothesis_col: row[args.hypothesis_col],
                "chrf++": round(chrf_scores[i], 2),
            }
            if comet_scores is not None:
                out_row["comet"] = round(comet_scores[i], 4)
            writer.writerow(out_row)

    # Summary
    avg_chrf = sum(chrf_scores) / len(chrf_scores)
    print("\n--- Summary ---")
    print(f"Rows evaluated : {len(rows)}")
    print(f"Average chrF++ : {avg_chrf:.2f}  (0-100, higher = closer to reference)")
    if comet_scores is not None:
        avg_comet = sum(comet_scores) / len(comet_scores)
        print(f"Average COMET  : {avg_comet:.4f}  (roughly 0-1, higher = better quality)")
    print(f"\nPer-row results written to: {args.output_csv}")


if __name__ == "__main__":
    main()
