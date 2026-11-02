#!/usr/bin/env python3
"""Calibrate the S2 classifier threshold from a labeled sample file.

The script never invents a threshold: it reads ``config/classifier_samples.jsonl``,
grid-searches decision threshold and mixed margin, and (with ``--write``) writes
``config/classifier_calibration.json`` consumed by the runtime classifier.

Usage:
    .venv/bin/python scripts/calibrate_pipeline_v2.py
    .venv/bin/python scripts/calibrate_pipeline_v2.py --write
    .venv/bin/python scripts/calibrate_pipeline_v2.py --no-embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.pipeline_v2.classifier import ClassifierScores, InputClassifier, _cosine
from app.pipeline_v2.config import Calibration, load_pipeline_config
from app.pipeline_v2.contracts import PersonalContext

LABELS = ("macro", "micro", "mixed")


def _read_samples(path: Path) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}")
        if row.get("label") not in LABELS:
            raise SystemExit(
                f"{path}:{line_number}: label must be one of {LABELS}"
            )
        if not str(row.get("text") or "").strip():
            raise SystemExit(f"{path}:{line_number}: text is empty")
        samples.append(row)
    if not samples:
        raise SystemExit(f"{path}: no samples")
    return samples


def _predict(
    macro: float, micro: float, threshold: float, mixed_margin: float
) -> str:
    if macro >= threshold and micro >= threshold:
        return "mixed"
    if macro >= threshold:
        return "macro"
    if micro >= threshold:
        return "micro"
    if abs(macro - micro) <= mixed_margin:
        return "macro" if macro >= micro else "micro"
    return "macro" if macro > micro else "micro"


def _metrics(
    truth: Sequence[str], predicted: Sequence[str]
) -> Dict[str, Any]:
    labels = list(LABELS)
    confusion = {label: {other: 0 for other in labels} for label in labels}
    for actual, guess in zip(truth, predicted):
        confusion[actual][guess] += 1
    per_class: Dict[str, Dict[str, float]] = {}
    f1_values: List[float] = []
    recalls: List[float] = []
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
        f1_values.append(f1)
        recalls.append(recall)
    accuracy = (
        sum(1 for actual, guess in zip(truth, predicted) if actual == guess)
        / len(truth)
    )
    return {
        "accuracy": round(accuracy, 6),
        "balanced_accuracy": round(sum(recalls) / len(recalls), 6),
        "macro_f1": round(sum(f1_values) / len(f1_values), 6),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def _objective(metrics: Dict[str, Any]) -> float:
    return (
        0.5 * metrics["accuracy"]
        + 0.35 * metrics["balanced_accuracy"]
        + 0.15 * metrics["macro_f1"]
    )


async def _score_samples(
    classifier: InputClassifier,
    samples: Sequence[Dict[str, Any]],
    *,
    use_embeddings: bool,
) -> List[ClassifierScores]:
    texts = [
        (str(row["text"]) + "\n" + str(row.get("context") or "")).strip()[
            : classifier.config.max_text_chars
        ]
        for row in samples
    ]
    lexicon_pairs = [
        (
            classifier._lexicon_score(text, classifier.config.macro_lexicon),
            classifier._lexicon_score(text, classifier.config.micro_lexicon),
        )
        for text in texts
    ]

    vectors: List[Sequence[float] | None] = [None] * len(texts)
    prototype_vectors = None
    if use_embeddings:
        prototype_vectors = await classifier._get_prototype_vectors()
        if prototype_vectors is not None and classifier._embedding_client is not None:
            batch_size = 16
            collected: List[Sequence[float] | None] = []
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                try:
                    embedded = await classifier._embedding_client.get_embeddings_batch(
                        batch, input_type="search_document"
                    )
                except Exception as exc:
                    print(
                        f"[calibrate] embedding batch failed: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    embedded = [None] * len(batch)
                collected.extend(embedded)
            vectors = collected

    results: List[ClassifierScores] = []
    for index, text in enumerate(texts):
        (macro_lex, macro_hits), (micro_lex, micro_hits) = lexicon_pairs[index]
        score = ClassifierScores(
            macro_lexicon=macro_lex,
            micro_lexicon=micro_lex,
            macro_hits=macro_hits,
            micro_hits=micro_hits,
        )
        vector = vectors[index]
        if prototype_vectors is not None and vector is not None:
            macro_proto, micro_proto = prototype_vectors
            macro_emb = _cosine(vector, macro_proto)
            micro_emb = _cosine(vector, micro_proto)
            macro_norm = (macro_emb + 1.0) / 2.0
            micro_norm = (micro_emb + 1.0) / 2.0
            score.macro_embedding = macro_emb
            score.micro_embedding = micro_emb
            score.macro_score = (
                classifier.config.lexicon_weight * macro_lex
                + classifier.config.embedding_weight * macro_norm
            )
            score.micro_score = (
                classifier.config.lexicon_weight * micro_lex
                + classifier.config.embedding_weight * micro_norm
            )
            score.method = "lexicon+embedding"
        else:
            score.macro_score = macro_lex
            score.micro_score = micro_lex
            score.method = "lexicon"
        results.append(score)
    return results


async def _run(args: argparse.Namespace) -> int:
    pipeline_config = load_pipeline_config(args.config)
    sample_path = (
        Path(args.samples).expanduser()
        if args.samples
        else REPO_ROOT / "config" / "classifier_samples.jsonl"
    )
    samples = _read_samples(sample_path)
    if args.config:
        pipeline_config = load_pipeline_config(args.config)

    dummy = Calibration(
        calibrated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        method="calibration-run",
        sample_file=str(sample_path),
        n_samples=len(samples),
        threshold=0.5,
        mixed_margin=pipeline_config.classifier.mixed_margin,
    )
    classifier = InputClassifier(
        config=pipeline_config.classifier, calibration=dummy
    )
    if not args.embeddings:
        classifier.config = classifier.config.model_copy(
            update={"embedding_weight": 0.0, "lexicon_weight": 1.0}
        )
    scores = await _score_samples(
        classifier, samples, use_embeddings=args.embeddings
    )
    truth = [row["label"] for row in samples]

    best: Dict[str, Any] | None = None
    for threshold in [round(0.20 + 0.01 * i, 2) for i in range(66)]:
        for margin in [round(0.02 + 0.01 * i, 2) for i in range(19)]:
            predicted = [
                _predict(
                    score.macro_score, score.micro_score, threshold, margin
                )
                for score in scores
            ]
            metrics = _metrics(truth, predicted)
            objective = _objective(metrics)
            candidate = {
                "threshold": threshold,
                "mixed_margin": margin,
                "objective": round(objective, 6),
                "metrics": metrics,
            }
            if best is None or candidate["objective"] > best["objective"]:
                best = candidate
    if best is None:
        raise SystemExit("calibration grid produced no candidate")

    predicted = [
        _predict(score.macro_score, score.micro_score, best["threshold"], best["mixed_margin"])
        for score in scores
    ]
    report = {
        "best": best,
        "n_samples": len(samples),
        "method": (
            "grid_search_accuracy_balanced_macro_f1"
            if args.embeddings
            else "grid_search_lexicon_only"
        ),
        "embedding_used": args.embeddings,
        "sample_file": str(sample_path),
        "predictions": [
            {
                "text": row["text"][:160],
                "truth": row["label"],
                "predicted": guess,
                "macro_score": round(score.macro_score, 6),
                "micro_score": round(score.micro_score, 6),
            }
            for row, score, guess in zip(samples, scores, predicted)
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.write:
        calibration = {
            "version": "1.0",
            "calibrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "method": report["method"],
            "sample_file": str(sample_path.relative_to(REPO_ROOT))
            if sample_path.is_relative_to(REPO_ROOT)
            else str(sample_path),
            "n_samples": len(samples),
            "threshold": best["threshold"],
            "mixed_margin": best["mixed_margin"],
            "metrics": best["metrics"],
            "notes": (
                "Generated by scripts/calibrate_pipeline_v2.py from the labeled "
                "sample file. Re-run after changing the lexicon, prototypes or "
                "embedding provider. Threshold is not a hand-written constant."
            ),
            "objective": best["objective"],
            "embedding_used": args.embeddings,
        }
        output_path = (
            Path(args.output).expanduser()
            if args.output
            else REPO_ROOT / "config" / "classifier_calibration.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(calibration, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[calibrate] wrote {output_path}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write config/classifier_calibration.json",
    )
    parser.add_argument(
        "--no-embeddings",
        dest="embeddings",
        action="store_false",
        help="calibrate the lexicon fallback only (no Ollama/Cohere call)",
    )
    parser.set_defaults(embeddings=True)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
