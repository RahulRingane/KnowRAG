"""`python -m app.cli.query "your question"` — the full pipeline from a shell.

Drives the same `QueryService` the API drives, so what you see here is what
`POST /query` returns — there is no second code path (§7.2). That includes
the query/fact routing: the classifier runs inside the service, not here, so
the CLI and the API cannot disagree about what an input is. This module reads
the decision off `response.input_type` and prints it; it never makes one.

    python -m app.cli.query "what is RISC?"                -> question, answered
    python -m app.cli.query "RISC has instruction pipelining" -> fact, checked

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

Output is three blocks, in pipeline order. `TOP N RETRIEVAL CANDIDATES` is the
evidence, printed mid-flight as retrieval returns it and before anything has
been scored. `NLI SCORING TRACE` is each `(premise, hypothesis)` pair as the
verifier scores it, with the model's label and confidence and any conflation
the composition guard raised against it. `VERIFICATION RESULT` is what the
system then decided.

All three share `[C1]..[Cn]`, so a claim's citation reads directly against the
chunk text above it — the one thing the wire contract cannot give you, since
it carries chunk *ids* and not chunk text. The trace is the middle term that
was missing: it is what turns "this came back SUPPORTED at 0.987" into "this
came back SUPPORTED at 0.987 *on [C2], which says this*".

Requires the datastores populated (`python -m app.cli.ingest`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from app.cli import quiet_third_party
from app.core.observability import configure_logging
from app.domain.models import Chunk
from app.domain.ports import Retriever


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.query",
        description=(
            "Ask KnowRAG a question, or give it a statement to check. Questions "
            "are answered from the corpus; statements are verified against it."
        ),
        epilog=(
            'examples:\n'
            '  python -m app.cli.query "What is an embedded system?"\n'
            '  python -m app.cli.query "An embedded system is task-specific"\n'
            '  python -m app.cli.query --json "..." | jq .input_type\n'
            '  echo "..." | python -m app.cli.query'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "question",
        nargs="?",
        help=(
            "The question to ask or the statement to check. Read from stdin, "
            "or prompted for, if omitted."
        ),
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
    return input("Question or statement: ").strip()


# Pipeline order, not `latency_ms`'s insertion order: `model_load_ms` is
# accumulated across whichever stages happened to touch a cold model, so it
# lands wherever the first one did. Reading a breakdown means reading it
# against the shape of the pipeline, so the order is fixed here and any key
# this list does not know about is appended rather than dropped.
_STAGE_ORDER = (
    "model_load_ms",
    "retrieval_ms",
    "rerank_ms",
    "format_context_ms",
    "generation_ms",
    "verification_ms",
    "assembly_ms",
)

# Stages under this are reported as a single "<0.1s" tail rather than as a row
# each. They are real and they are in `took`; they are just not where 24
# seconds went, and listing five of them at 0.0s buries the two that matter.
_TIMING_FLOOR_MS = 50.0


def _format_timing(latency_ms: dict[str, float]) -> str:
    """The per-stage breakdown, in pipeline order, as one line.

    Every key in `latency_ms` is a disjoint span (see
    `app.services.query_service._stage`), so these add up to `took` and a
    reader can treat the largest row as the thing to fix.
    """
    ordered = [k for k in _STAGE_ORDER if k in latency_ms]
    ordered += [k for k in latency_ms if k not in _STAGE_ORDER]

    shown = [
        f"{key.removesuffix('_ms')} {latency_ms[key] / 1000:.1f}s"
        for key in ordered
        if latency_ms[key] >= _TIMING_FLOOR_MS
    ]
    hidden = sum(1 for key in ordered if latency_ms[key] < _TIMING_FLOOR_MS)
    if hidden:
        shown.append(f"{hidden} stage(s) <0.1s")

    return ", ".join(shown) if shown else "n/a"


# --- retrieval candidates ---------------------------------------------------

# How many chunks to show and how much of each. Three, out of the five
# `top_k_rerank` returns, because the block exists to be read at a glance
# directly above the verdict — five full PDF chunks is a page of prose that
# pushes the answer it is supposed to explain out of a terminal scrollback.
_CANDIDATES_SHOWN = 3
_CONTENT_CHARS = 320
_WRAP_WIDTH = 88


def _preview(text: str, limit: int = _CONTENT_CHARS) -> str:
    """`text` as one paragraph, cut to `limit` characters.

    Whitespace is collapsed before the cut, not after, because these are
    chunks of a PDF: they carry the source's own line breaks, so a raw slice
    renders as a ragged half-width column and spends much of the character
    budget on newlines rather than on the words the reader is checking.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        return collapsed[:limit].rstrip() + "…"
    return collapsed


def _print_candidates(chunks: list[Chunk]) -> None:
    """Show the evidence the pipeline retrieved, before it is scored.

    Printed from inside the query flow (see `_PrintingRetriever`) rather than
    from the response afterwards, because `FactCheckedResponse` carries
    `retrieved_chunk_ids` and not chunk text — by design, the wire contract
    reports *which* evidence was used, not a second copy of the corpus. The
    text only exists at this point in the pipeline.

    Tags come from `format_context`, which is the one module allowed to decide
    what `[C1]` means (§5.1). Numbering the same list a second time here would
    agree today and is exactly the divergence the tag map is threaded through
    the pipeline to prevent — so `[C1]` above the verdict is guaranteed to be
    the chunk `[C1]` in that verdict cites, which is the whole reason to print
    this next to it. The context block itself is discarded; only the numbering
    is wanted.
    """
    from app.domain.context import format_context

    _, tag_map = format_context(chunks)
    shown = list(tag_map.items())[:_CANDIDATES_SHOWN]

    print(f"=== TOP {len(shown)} RETRIEVAL CANDIDATES ===\n")

    if not shown:
        # A real outcome, not an error: nothing was retrieved, and the
        # `insufficient_evidence` below is about to be explained by this line.
        print("(nothing retrieved)\n")
        return

    for tag, chunk in shown:
        # `rerank_score` is the cross-encoder's — the ordering these are in.
        # It falls back to the originating store's own score only for a
        # retriever that does not rerank, where the alternative is printing a
        # rank with nothing to justify it.
        score = chunk.rerank_score if chunk.rerank_score is not None else chunk.score
        print(f"[{tag}] Score: {score:.4f}" if score is not None else f"[{tag}] Score: n/a")
        print(
            textwrap.fill(
                f"Content: {_preview(chunk.text)}",
                width=_WRAP_WIDTH,
                subsequent_indent=" " * len("Content: "),
            )
        )
        print("---\n")


class _PrintingRetriever:
    """A `Retriever` that prints what it returned, then returns it unchanged.

    A decorator rather than a hook inside `HybridRetriever` or a branch inside
    `QueryService`: printing to a terminal is a property of this entrypoint and
    of nothing else, so it stays in `app.cli` and the pipeline keeps having no
    opinion about whether anyone is watching. It satisfies `Retriever`, so the
    service it is handed to cannot tell — which is also why the rest of the
    graph still comes from `build_default_query_service` and the CLI and the
    API keep driving identical wiring (§7.2).
    """

    def __init__(self, inner: Retriever):
        self._inner = inner

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        chunks = self._inner.search(query, k)
        _print_candidates(chunks)
        return chunks


# --- NLI scoring trace ------------------------------------------------------

_PREMISE_CHARS = 200


class _PrintingScorerObserver:
    """Prints each `(premise, hypothesis)` pair as the verifier scores it.

    The counterpart to `_PrintingRetriever`, and here for the same reason: what
    the candidates block shows is the evidence *before* anything looked at it,
    and the verdict shows the conclusion, but the step in between — which chunk
    was paired with which sentence, and what the NLI model said about that pair
    — was visible only to a debugger. A claim coming back SUPPORTED at 0.987
    tells you nothing about *which* premise earned that, and on the fact route
    that turned out to be the question worth asking.

    Header printing is deferred to the first event rather than done up front:
    the question route can produce claims with no citations at all, and a
    heading over an empty block reads like a bug in the pipeline rather than a
    claim the generator declined to cite.
    """

    def __init__(self) -> None:
        self._started = False

    def __call__(self, event) -> None:
        if not self._started:
            print("=== NLI SCORING TRACE ===\n")
            self._started = True

        flag = "  ⚠ CONFLATION" if event.flagged else ""
        chunk = (
            f"  (doc {event.document_id}, chunk {event.chunk_index})"
            if event.document_id is not None
            else ""
        )
        print(f"[{event.tag}] {event.label} {event.score:.4f}{flag}{chunk}")
        print(
            textwrap.fill(
                f"hypothesis: {event.hypothesis}",
                width=_WRAP_WIDTH,
                subsequent_indent=" " * len("hypothesis: "),
            )
        )
        # The premise is cut here and nowhere else — `ScoringEvent.premise`
        # carries the whole chunk, because what was scored was the whole chunk
        # and a record that quietly truncated it would misreport the input.
        print(
            textwrap.fill(
                f"premise:    {_preview(event.premise, _PREMISE_CHARS)}",
                width=_WRAP_WIDTH,
                subsequent_indent=" " * len("premise:    "),
            )
        )
        if event.flagged:
            print(
                textwrap.fill(
                    f"rejected:   {event.mismatch_reason}",
                    width=_WRAP_WIDTH,
                    subsequent_indent=" " * len("rejected:   "),
                )
            )
        print("---\n")


def _print_summary(response, quiet: bool) -> None:
    print(response.answer)

    if quiet:
        return

    print()
    print(f"  state     {response.state}")

    if response.input_type == "fact":
        # The fact route produces exactly one verdict on the caller's own
        # sentence, so the question route's "N supported, M rejected" tally
        # would report a count where there is only ever a single answer. The
        # verdict and the score it turned on are what a caller came for.
        verdict = response.claims[0]
        print(f"  verdict   {verdict.status}")
        if verdict.evidence_score is not None:
            # `evidence_score` is whichever score decided the verdict, and on
            # the CONTRADICTED path that is a contradiction confidence, not an
            # entailment one. Labelling it "best entailment" regardless was
            # always wrong and became easy to hit once the composition guard
            # started letting contradictions decide fact-route verdicts.
            measure = (
                "strongest contradiction"
                if verdict.status == "CONTRADICTED"
                else "best entailment"
            )
            print(f"  score     {verdict.evidence_score:.3f}  ({measure})")
        if verdict.reason:
            print(f"  reason    {verdict.reason}")
    else:
        # Three buckets, not two, because "failed verification" stopped meaning
        # "kept out of the answer" on this route. An unconfirmed claim is in
        # the answer above and is reported as a caveat on it; a contradicted
        # one was withheld. Printing both as "rejected" would tell a reader
        # that a sentence they can see was thrown away.
        supported = [c for c in response.claims if c.status == "SUPPORTED"]
        unconfirmed = [c for c in response.claims if c.status == "UNSUPPORTED"]
        contradicted = [c for c in response.claims if c.status == "CONTRADICTED"]
        print(
            f"  claims    {len(supported)} supported"
            f", {len(unconfirmed)} unconfirmed"
            f", {len(contradicted)} contradicted"
            f", {len(response.refusals)} refusal(s)"
        )

    print(f"  evidence  {', '.join(response.retrieved_chunk_ids)}")
    print(f"  took      {sum(response.latency_ms.values()) / 1000:.1f}s")
    # Unconditional, not behind a flag: `took` says a query was slow and never
    # says which stage was, and the answer is not the same one twice running —
    # a cold process spends most of it loading models, a warm one spends it in
    # the generation call. Printing only the total meant every "why is this
    # slow" question needed a re-run with `-v` to answer.
    print(f"  timing    {_format_timing(response.latency_ms)}")

    # What the evidence did or did not do to each claim is the whole point of
    # the system, so it is surfaced here rather than hidden behind --json. A
    # count alone tells you a claim was doubted but not what to do about it.
    #
    # Skipped on the fact route: its single verdict, including the reason it
    # went the way it did, is already printed above, and re-listing it here
    # would frame the caller's own statement as something the system threw out
    # of an answer it never assembled.
    if response.input_type == "fact":
        return

    for verdict in response.claims:
        if verdict.status == "UNSUPPORTED":
            # Labelled by what actually happened. The sentence is in the answer
            # printed above; what the NLI model could not do is confirm it.
            print(f"\n  unconfirmed: {verdict.text}")
        elif verdict.status == "CONTRADICTED":
            print(f"\n  contradicted (withheld from answer): {verdict.text}")
        else:
            continue
        print(f"               {verdict.reason}")


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
    from app.infrastructure.search.hybrid_retriever import build_default_retriever
    from app.services.query_service import build_default_query_service

    # Suppressed for both output modes that promise something other than a
    # human-readable report: `--json` writes one JSON document to stdout and
    # exists to be piped into `jq`, which a banner would break, and `-q` is
    # documented as "only the answer text". Everywhere else it is on, with no
    # flag to ask for it — the evidence is the thing that makes the verdict
    # below mean anything, and hiding it behind an opt-in makes the default
    # output the one you cannot check.
    retriever = build_default_retriever()
    observer = None
    if not (args.json or args.quiet):
        retriever = _PrintingRetriever(retriever)
        observer = _PrintingScorerObserver()

    response = build_default_query_service(
        retriever=retriever, scoring_observer=observer
    ).run(question)

    if args.json:
        # `input_type` is a field on the response, so the JSON form carries the
        # classification without any help from here — the API and the CLI print
        # the same thing because they serialize the same object.
        print(response.model_dump_json(indent=2))
    else:
        # Before the result, per the routing contract: a reader has to know
        # whether they are looking at an answer to a question or a verdict on
        # a statement before the text below means anything. `-q` opts out of
        # everything but the answer text, and that includes this.
        if not args.quiet:
            print("=== VERIFICATION RESULT ===\n")
            print(f"input type: {response.input_type}\n")
        _print_summary(response, quiet=args.quiet)

    # A question the corpus cannot answer is a normal outcome, not a failure
    # — but a shell caller needs to branch on it, and `state` is not visible
    # to `&&`. Exit 1 makes it scriptable without parsing stdout.
    #
    # The fact route lands on the same rule with the same meaning: 0 when the
    # corpus bears the statement out, 1 when it refutes it ("contradicted") or
    # is silent on it ("insufficient_evidence"). A script that wants to tell
    # refutation from silence reads `state` out of `--json`.
    return 0 if response.state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
