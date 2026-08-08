"""`python -m app.cli.query "your question"` — the full pipeline from a shell.

Drives the same `QueryService` the API drives, so what you see here is what
`POST /query` returns — there is no second code path (§7.2).

Three things this does that a bare `input()` prompt did not, all of which
matter when the caller is a terminal rather than a person watching:

- **Takes the question as an argument**, so it composes with a shell. Falls
  back to prompting only when nothing is passed, and reads stdin when piped.
- **Silences pipeline logs by default.** Retrieval and verification emit a
  structured log line per stage and per claim; that is the right default for
  a server and pure noise ahead of an answer. `-v` puts them back.
- **Prints the answer, not the audit trail.** The verdict breakdown is what
  makes this system trustworthy, but it is not what you asked for. `--json`
  gives the full §6.4 contract for piping into `jq`.

Requires the datastores populated (`python -m app.cli.ingest`).
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.cli import quiet_third_party
from app.core.observability import configure_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.query",
        description="Ask KnowRAG a question and print the fact-checked answer.",
        epilog=(
            'examples:\n'
            '  python -m app.cli.query "What is an embedded system?"\n'
            '  python -m app.cli.query --json "..." | jq .claims\n'
            '  echo "..." | python -m app.cli.query'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="The question to ask. Read from stdin, or prompted for, if omitted.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full FactCheckedResponse (§6.4) instead of a summary.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only the answer text — nothing else.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the per-stage pipeline logs (retrieval, verdicts, timings).",
    )
    return parser.parse_args(argv)


def _read_question(argument: str | None) -> str:
    if argument:
        return argument
    # Piped input beats prompting: `echo ... | python -m app.cli.query` should
    # not hang waiting on a tty that is not there.
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    return input("Question: ").strip()


def _print_summary(response, quiet: bool) -> None:
    print(response.answer)

    if quiet:
        return

    supported = [c for c in response.claims if c.status == "SUPPORTED"]
    print()
    print(f"  state     {response.state}")
    print(
        f"  claims    {len(supported)} supported"
        f", {len(response.rejected_claims)} rejected"
        f", {len(response.refusals)} refusal(s)"
    )
    print(f"  evidence  {', '.join(response.retrieved_chunk_ids)}")
    print(f"  took      {sum(response.latency_ms.values()) / 1000:.1f}s")

    # Why a claim was thrown away is the whole point of the system, so it is
    # surfaced here rather than hidden behind --json. A rejection printed as
    # a count alone tells you something was dropped but not what to do.
    for verdict in response.rejected_claims:
        print(f"\n  rejected: {verdict.text}")
        print(f"            {verdict.reason}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.verbose:
        quiet_third_party()

    # Errors always surface; the per-stage INFO stream is opt-in. Console
    # rendering rather than JSON because the reader here is a person.
    configure_logging(
        level=logging.INFO if args.verbose else logging.ERROR,
        json_logs=False,
    )

    question = _read_question(args.question)
    if not question:
        print("No question given.", file=sys.stderr)
        return 2

    # Imported after logging is configured, so module-level loggers bind to
    # the right level, and after the argument check, so `--help` costs nothing.
    from app.services.query_service import build_default_query_service

    response = build_default_query_service().run(question)

    if args.json:
        print(response.model_dump_json(indent=2))
    else:
        _print_summary(response, quiet=args.quiet)

    # A question the corpus cannot answer is a normal outcome, not a failure
    # — but a shell caller needs to branch on it, and `state` is not visible
    # to `&&`. Exit 1 makes it scriptable without parsing stdout.
    return 0 if response.state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
