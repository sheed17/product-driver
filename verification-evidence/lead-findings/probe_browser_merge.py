"""Does a multi-browser-step scenario lose earlier observations?

If it does, a `forbidden:` string that appeared transiently in an earlier
browser step is never checked — a false green on exactly the kind of scenario
the generator is meant to produce ("approve twice, and the duplicate warning
must never appear").
"""
from neyma_product_driver.models import BrowserObservation, ScenarioResult
from neyma_product_driver.scenarios import ScenarioExecutor
from neyma_product_driver.config import ScenarioRunConfig
from pathlib import Path
import tempfile

tmp = Path(tempfile.mkdtemp())
ex = ScenarioExecutor(Path.cwd(), ScenarioRunConfig(), tmp)

result = ScenarioResult(scenario_name="probe", mode="browser")

first = BrowserObservation(
    url="http://localhost/approve",
    title="Approve",
    visible_text="Approved. WARNING: duplicate charge applied",
    console_errors=["boom: first step console error"],
)
second = BrowserObservation(
    url="http://localhost/approve",
    title="Approve",
    visible_text="Approved.",
    console_errors=[],
)

ex._merge_browser(result, first)
ex._merge_browser(result, second)

haystack = ScenarioExecutor._observed_text(result)

print("merged visible_text :", repr(result.browser.visible_text))
print("merged console_errs :", result.browser.console_errors)
print()
print("haystack:")
print(haystack)
print()
forbidden = "duplicate charge applied"
present = forbidden in haystack
print(f"forbidden {forbidden!r} detected in haystack? {present}")
print()
if not present:
    print("RESULT: FALSE GREEN CONFIRMED — text observed in browser step 1 is absent")
    print("        from the global haystack, so the `forbidden:` check passes.")
else:
    print("RESULT: earlier observation retained; no evidence loss.")

lost_console = "boom: first step console error" not in haystack
print(f"first-step console error also lost? {lost_console}")
