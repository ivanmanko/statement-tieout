"""How scanned pages are shared out across OCR workers (SPEC §9).

The reading itself needs a real engine and a real file; what is testable here
is the scheduling around it — which pages go to which worker, and when
parallelism is worth starting at all.
"""

from statement_tieout.pdf.loader import MIN_PAGES_FOR_PARALLEL_OCR, plan_ocr


class TestSharingOut:
    def test_pages_are_dealt_round_robin(self):
        """Round robin, not contiguous blocks: pages differ in how long they take."""
        assert plan_ocr([1, 2, 3, 4, 5, 6, 7], workers=3) == [[1, 4, 7], [2, 5], [3, 6]]

    def test_every_page_is_assigned_exactly_once(self):
        pages = list(range(1, 100))
        assigned = [p for slice_ in plan_ocr(pages, workers=4) for p in slice_]
        assert sorted(assigned) == pages

    def test_no_empty_workers(self):
        assert all(plan_ocr([1, 2], workers=8))

    def test_more_workers_than_pages_is_capped(self):
        assert len(plan_ocr([1, 2], workers=8)) == 2


class TestWhenNotToBother:
    def test_a_short_document_stays_sequential(self):
        assert plan_ocr([1, 2, 3], workers=4) == [[1, 2, 3]]

    def test_the_threshold_is_the_declared_one(self):
        pages = list(range(1, MIN_PAGES_FOR_PARALLEL_OCR + 1))
        assert len(plan_ocr(pages, workers=4)) > 1

    def test_one_worker_means_one_slice(self):
        assert plan_ocr([1, 2, 3, 4, 5], workers=1) == [[1, 2, 3, 4, 5]]

    def test_no_scanned_pages_is_no_work(self):
        assert plan_ocr([], workers=4) == []
