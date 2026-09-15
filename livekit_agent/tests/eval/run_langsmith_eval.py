"""Top-level LangSmith-tracked eval runner -- replaces final_reconciliation_rerun.py as the
"run everything and record it" entrypoint going forward. Reuses run_eval.simulate_case() (the
real live-call simulation, unchanged) as the evaluate() `target`, and rubric.judge_transcript()
(the real grading criteria, unchanged) inside a single evaluator function that surfaces every
graded rule tag as its own named LangSmith feedback score from one judge call -- same per-case
judge cost as before.

Usage:
    python run_langsmith_eval.py --direction outbound [--limit 3] [--num-repetitions 3]

--num-repetitions defaults to 1 (matches prior behavior / avoids silently multiplying API cost).
Pass a higher value deliberately to get real signal on whether a case's result is stable -- this
directly addresses the LLM-sampling-noise problem found this session (a case flipped pass/fail
between two single-shot verification runs with zero code change in between).
"""
import argparse

from langsmith import Client, evaluate

import run_eval
import rubric
from langsmith_setup_datasets import DATASET_NAMES


def to_case_dict(inputs: dict) -> dict:
    """Example.inputs uses `case_id` (LangSmith dataset schema); simulate_case()/rubric.py's
    case-dict contract uses `id`. Translate rather than change either schema."""
    case = dict(inputs)
    case["id"] = case.pop("case_id")
    return case


def make_target(direction: str, base_rules: str, call_flow: str):
    def target(inputs: dict) -> dict:
        return run_eval.simulate_case(to_case_dict(inputs), direction, base_rules, call_flow)
    return target


def rule_grading_evaluator(run, example) -> dict:
    case = to_case_dict(example.inputs)
    transcript = (run.outputs or {}).get("transcript", "")
    verdict = rubric.judge_transcript(case, transcript)

    results = [{
        "key": "verdict",
        "score": 1.0 if verdict["overall"] == "PASS" else 0.0,
        "value": verdict["overall"],
        "comment": (verdict.get("raw") or "")[:2000],
    }]
    violated = set(verdict.get("violated_rules", []))
    for rule in case.get("rules_in_scope", []):
        results.append({
            "key": rule,
            "score": 0.0 if rule in violated else 1.0,
        })
    return {"results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", choices=["outbound", "inbound"], required=True)
    parser.add_argument("--dataset", default=None, help="Defaults to the standard per-direction dataset.")
    parser.add_argument("--prompt-source", default="live",
                         help='"live" (default) or a path to a variants/*.py file.')
    parser.add_argument("--num-repetitions", type=int, default=1,
                         help="Run each case N times to separate real fixes from sampling noise. "
                              "Defaults to 1 (no extra cost); raise deliberately when you need "
                              "confidence a result isn't a fluke.")
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="Cap to the first N examples (debug only).")
    parser.add_argument("--experiment-prefix", default=None)
    args = parser.parse_args()

    dataset_name = args.dataset or DATASET_NAMES[args.direction]
    base_rules, call_flow, label, source_kind = run_eval.load_prompt_source(args.direction, args.prompt_source)
    target = make_target(args.direction, base_rules, call_flow)
    prefix = args.experiment_prefix or f"{args.direction}-{label}"

    data = dataset_name
    if args.limit:
        client = Client()
        examples = list(client.list_examples(dataset_name=dataset_name, limit=args.limit))
        data = examples
        print(f"[langsmith-eval] limited to {len(examples)} examples")

    print(f"[langsmith-eval] direction={args.direction} dataset={dataset_name} "
          f"prompt_label={label} num_repetitions={args.num_repetitions}")

    results = evaluate(
        target,
        data=data,
        evaluators=[rule_grading_evaluator],
        experiment_prefix=prefix,
        num_repetitions=args.num_repetitions,
        max_concurrency=args.max_concurrency,
    )
    print(f"[langsmith-eval] done: experiment prefix '{prefix}'")


if __name__ == "__main__":
    main()
