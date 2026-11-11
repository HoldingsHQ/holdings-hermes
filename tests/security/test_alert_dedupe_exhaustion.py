"""HOLD-01: a full retention window must not suppress new critical alerts."""
from contextlib import nullcontext
from copy import deepcopy
from tests.security import harness

watch = harness.mod("alert_watch")
schedule = harness.mod("alert_schedule")


def finding(i, severity="warning"):
    return watch._alert("security", "flagged-token-" + str(i), "Possible scam airdrop",
                        "Fictional GoPlus evidence", "2026-11-09T00:00:00Z", ["fixture"], severity=severity)


class DedupeExhaustionTests(harness.SecurityTest):
    def full_state(self):
        state = watch.default_state(watch.MODE)
        for start in range(0, watch.MAX_SEEN, 100):
            watch.ingest(state, [finding(i) for i in range(start, min(start + 100, watch.MAX_SEEN))])
        self.assertEqual(len(state["seen"]), watch.MAX_SEEN)
        return state

    def test_new_critical_is_ingested_at_capacity_and_repeat_is_deduped(self):
        state = self.full_state()
        old = list(state["seen"])
        critical = finding("new-critical", "critical")
        self.assertEqual(len(watch.ingest(state, [critical])), 1, "new critical finding silently dropped")
        self.assertEqual(state["seen"], old[1:] + [critical["id"]])
        self.assertEqual(watch.ingest(state, [critical]), [])
        self.assertEqual(watch.ingest(state, [finding(watch.MAX_SEEN - 1)]), [])

    def test_cron_delivers_at_capacity_and_bounds_its_own_cursor(self):
        saved = self.full_state()
        saved["cronSeen"] = list(saved["seen"])
        critical = finding("new-critical", "critical")
        def store(mode):
            return deepcopy(saved), lambda value: saved.update(deepcopy(value))
        def run():
            return watch.request({"action": "cron"}, open_store=store,
                context=lambda: (None, None, None), lock=lambda home: nullcontext(),
                evaluate_live=lambda state, **kwargs: ({"security": "ready"}, [critical]),
                get_secret=lambda name: None, allow_cron=True)
        result = run()
        self.assertEqual(result["newCount"], 1, "full dedupe suppressed scheduled critical alert")
        self.assertNotEqual(schedule.render(result), "[SILENT]")
        self.assertEqual(len(saved["cronSeen"]), watch.MAX_SEEN)
        self.assertEqual(saved["cronSeen"][-1], critical["id"])
        self.assertEqual(run()["newCount"], 0)

    def test_legacy_oversized_cron_cursor_is_trimmed_in_order(self):
        state = watch.default_state(watch.MODE)
        state["cronSeen"] = [str(i) for i in range(watch.MAX_SEEN + 10)]
        before = list(state["cronSeen"])
        result = watch.request({"action": "cron"}, open_store=lambda mode: (state, lambda value: None),
            context=lambda: (None, None, None), lock=lambda home: nullcontext(),
            evaluate_live=lambda state, **kwargs: ({}, []), get_secret=lambda name: None, allow_cron=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(state["cronSeen"], before[-watch.MAX_SEEN:])
