from __future__ import annotations

import base64
import logging
import os
import sys
import time
from typing import Callable

from bs4 import BeautifulSoup, NavigableString
from ebooklib import epub

from .config import CorrectionConfig
from .epub_io import iter_document_items, reorder_items_by_spine
from .html_parser import extract_segment_texts, iter_rewritable_segments, split_large_group
from .llm import LLMClient
from .persistence import load_checkpoint, save_checkpoint, write_csv_report
from .safety import change_is_safe
from .types import ChangeRecord, ProcessingStats, ReviewState, StopProcessing


class DocumentProcessor:
    """Processes a single EPUB HTML document (item) using an LLM client."""

    def __init__(self, llm: LLMClient, config: CorrectionConfig) -> None:
        self.llm = llm
        self.config = config

    def process(
        self,
        item,
        doc_name: str,
        stats: ProcessingStats,
        records: list[ChangeRecord] | None,
        review: ReviewState,
        review_callback,
        should_stop: Callable[[], bool] | None = None,
        previous_context: list[str] | None = None,
    ) -> list[str]:
        raw = item.get_content()
        soup = BeautifulSoup(raw, "xml")

        segments = iter_rewritable_segments(soup)
        if not segments:
            return list(previous_context) if previous_context else []

        stats.docs_seen += 1
        recent_context: list[str] = list(previous_context) if previous_context else []
        sim_threshold = self.config.effective_similarity_threshold()
        max_change = self.config.effective_max_change_ratio()

        for batch_idx, batch in enumerate(
            split_large_group(
                segments,
                max_segments=self.config.max_segments_per_request,
                max_chars=self.config.max_chars_per_request,
            ),
            start=1,
        ):
            if should_stop and should_stop():
                raise StopProcessing()

            stats.groups_seen += 1
            stats.segments_seen += len(batch)
            originals = [s.original_text for s in batch]
            total_chars = sum(len(s) for s in originals)
            print(
                f"  [{doc_name}] batch {batch_idx} ({len(batch)} segments, {total_chars} chars)...",
                flush=True,
            )
            t0 = time.perf_counter()
            corrected = self.llm.request_corrections(originals, previous_context=recent_context)
            t1 = time.perf_counter()
            elapsed = t1 - t0
            print(
                f"    took {elapsed:.2f}s for 1 request, {elapsed:.2f}s per request, "
                f"{elapsed / total_chars:.4f}s per char (average)",
                flush=True,
            )

            if should_stop and should_stop():
                raise StopProcessing()

            seg_idx = 0
            while seg_idx < len(batch):
                segment = batch[seg_idx]
                new_text = corrected[seg_idx]

                if not change_is_safe(
                    original=segment.original_text,
                    proposed=new_text,
                    similarity_threshold=sim_threshold,
                    max_change_ratio=max_change,
                ):
                    stats.rejected_changes += 1
                    if records is not None:
                        records.append(
                            ChangeRecord(
                                doc_name=doc_name,
                                original=segment.original_text,
                                proposed=new_text,
                                accepted=False,
                            )
                        )
                    seg_idx += 1
                    continue

                if segment.original_text == new_text:
                    seg_idx += 1
                    continue

                if not review.auto_accept:
                    if review_callback is not None:
                        action = review_callback.ask(segment.original_text, new_text, doc_name)
                    else:
                        action = "accept"
                    if should_stop and should_stop():
                        raise StopProcessing()
                    if action == "accept_all":
                        review.auto_accept = True
                        print("Auto-accepting all remaining changes.")
                    elif action == "reject":
                        stats.rejected_changes += 1
                        if records is not None:
                            records.append(
                                ChangeRecord(
                                    doc_name=doc_name,
                                    original=segment.original_text,
                                    proposed=new_text,
                                    accepted=False,
                                )
                            )
                        seg_idx += 1
                        continue
                    elif action == "retry":
                        print("  Retrying batch...")
                        corrected = self.llm.request_corrections(
                            originals, previous_context=recent_context
                        )
                        if should_stop and should_stop():
                            raise StopProcessing()
                        continue
                else:
                    if review_callback is not None:
                        poll_action = review_callback.poll()
                        if poll_action == "stop_auto_accept":
                            review.auto_accept = False
                            print("Auto-accept paused. Resuming manual review.")
                            continue

                segment.node.replace_with(NavigableString(new_text))
                stats.accepted_changes += 1
                if records is not None:
                    records.append(
                        ChangeRecord(
                            doc_name=doc_name,
                            original=segment.original_text,
                            proposed=new_text,
                            accepted=True,
                        )
                    )
                seg_idx += 1

            if self.config.max_context > 0:
                recent_context.extend(originals)
                recent_context = recent_context[-self.config.max_context :]

        item.set_content(str(soup).encode("utf-8"))
        return recent_context


class BookProcessor:
    """Orchestrates processing of one or more EPUB books."""

    def __init__(self, llm: LLMClient, config: CorrectionConfig) -> None:
        self.llm = llm
        self.config = config
        self.doc_processor = DocumentProcessor(llm, config)

    def process_book(
        self,
        input_path: str,
        output_path: str,
        *,
        checkpoint_path: str | None = None,
        from_doc: int | None = None,
        to_doc: int | None = None,
        report_path: str | None = None,
        review_callback=None,
        auto_accept: bool = False,
        conserve_context: bool = False,
        should_stop: Callable[[], bool] | None = None,
    ) -> ProcessingStats:
        book = epub.read_epub(input_path)
        stats = ProcessingStats()
        records: list[ChangeRecord] | None = [] if report_path else None
        review = ReviewState(auto_accept=auto_accept)

        checkpoint: dict[str, str] = {}
        if checkpoint_path:
            checkpoint = load_checkpoint(checkpoint_path)
            if checkpoint:
                logging.info(
                    "Resuming from checkpoint: %d document(s) already processed.",
                    len(checkpoint),
                )

        conserved_context: list[str] = []
        all_items = list(iter_document_items(book))
        from_idx = (from_doc - 1) if from_doc else 0
        to_idx = to_doc if to_doc else len(all_items)

        if from_doc or to_doc:
            print(f"Processing documents {from_idx + 1}–{to_idx} of {len(all_items)}.")

        for item in all_items[from_idx:to_idx]:
            doc_name: str = item.file_name

            if doc_name in checkpoint:
                logging.info("Skipping already-processed document: %s", doc_name)
                item.set_content(base64.b64decode(checkpoint[doc_name]))
                if conserve_context:
                    texts = extract_segment_texts(item)
                    conserved_context.extend(texts)
                    if self.config.max_context > 0:
                        conserved_context = conserved_context[-self.config.max_context :]
                continue

            conserved_context = self.doc_processor.process(
                item=item,
                doc_name=doc_name,
                stats=stats,
                records=records,
                review=review,
                review_callback=review_callback,
                should_stop=should_stop,
                previous_context=conserved_context if conserve_context else None,
            )

            if checkpoint_path:
                checkpoint[doc_name] = base64.b64encode(item.get_content()).decode()
                save_checkpoint(checkpoint_path, checkpoint)

            epub.write_epub(output_path, book, {})

        reorder_items_by_spine(book)
        epub.write_epub(output_path, book, {})

        if records is not None and report_path:
            write_csv_report(records, report_path)
            print(f"Change report written to {report_path} ({len(records)} edits)")

        print(
            "Processed documents={docs}, groups={groups}, segments={segments}, "
            "accepted={accepted}, rejected={rejected}, failed_groups={failed}".format(
                docs=stats.docs_seen,
                groups=stats.groups_seen,
                segments=stats.segments_seen,
                accepted=stats.accepted_changes,
                rejected=stats.rejected_changes,
                failed=stats.failed_groups,
            )
        )
        return stats

    def process_batch(
        self,
        batch_folder: str,
        *,
        review_callback=None,
        auto_accept: bool = False,
        conserve_context: bool = False,
        should_stop: Callable[[], bool] | None = None,
        from_doc: int | None = None,
        to_doc: int | None = None,
    ) -> tuple[list[str], list[tuple[str, str]]]:
        if not os.path.isdir(batch_folder):
            raise ValueError(f"Batch folder not found: {batch_folder}")

        epubs = sorted(f for f in os.listdir(batch_folder) if f.lower().endswith(".epub"))
        if not epubs:
            raise ValueError(f"No EPUB files found in {batch_folder}")

        successes: list[str] = []
        failures: list[tuple[str, str]] = []

        translate_suffix = ""
        if self.config.translate and self.config.target_language:
            translate_suffix = f"_{self.config.target_language.replace(' ', '_')}"

        for epub_name in epubs:
            if should_stop and should_stop():
                print("Stopping as requested.")
                break

            input_path = os.path.join(batch_folder, epub_name)
            basename = os.path.basename(input_path)
            stem, ext = os.path.splitext(basename)

            os.makedirs("output", exist_ok=True)
            output_path = os.path.join("output", f"{stem}{translate_suffix}{ext}")
            os.makedirs("checkpoints", exist_ok=True)
            checkpoint_path = os.path.join("checkpoints", f"{stem}{translate_suffix}.json")

            print(f"\n{'='*60}")
            print(f"Batch: {epub_name}")
            print(f"Output: {output_path}")
            print(f"Checkpoint: {checkpoint_path}")
            print(f"{'='*60}")

            try:
                self.process_book(
                    input_path=input_path,
                    output_path=output_path,
                    checkpoint_path=checkpoint_path,
                    from_doc=from_doc,
                    to_doc=to_doc,
                    review_callback=review_callback,
                    auto_accept=auto_accept,
                    conserve_context=conserve_context,
                    should_stop=should_stop,
                )
                successes.append(epub_name)
            except StopProcessing:
                print("\nStopping batch as requested.")
                break
            except Exception as exc:
                print(f"\nFAILED: {epub_name}\nError: {exc}", file=sys.stderr)
                failures.append((epub_name, str(exc)))

        print(f"\n{'='*60}")
        print(f"BATCH COMPLETE: {len(successes)} succeeded, {len(failures)} failed")
        if successes:
            print("Succeeded:")
            for name in successes:
                print(f"  ✓ {name}")
        if failures:
            print("Failed:")
            for name, err in failures:
                print(f"  ✗ {name}: {err}")
        print(f"{'='*60}")
        return successes, failures
