# Historical benchmark accounting

The README reports preserved October 2026 comparisons. No retrieval runs or paid model requests were made to produce this documentation update. Machine-readable aggregates are in [benchmark_results.json](benchmark_results.json).

## Scope and metrics

EASY maps to v0.2 A, N001–N010; MEDIUM maps to v0.5 A, C001–C010; HARD maps to v0.5 B, C001–C010. The public datasets contain 30 queries per tier; these comparison tables cover only the first 10. Different tiers have different query wording, so differences between tiers do not establish an algorithm improvement.

Hit@5 counts a query as a hit when its designated reference paper appears in the returned first five papers. This measures target recovery, not the relevance of every returned paper. Mutual coverage uses the preserved same-query paper-identity matches, ignores ranking, and divides the summed intersections by five fixed slots per query. Missing papers are not removed from the denominator. It is not Jaccard similarity or directional coverage based on the number of papers actually returned. In MEDIUM, Gemini returned 43 papers across 50 slots; the displayed overlap still divides by 50.

EASY retains the user's agreed presentation: Idea2Prior 9/10, ChatGPT 7/10, Gemini 7/10. The ChatGPT figure uses the agreed reporting convention, with six observed hits and one convention-based counted hit. All three EASY mutual-coverage values retain the agreed nine-query denominator: 14/45, 13/45, and 17/45. MEDIUM and HARD use 50 fixed slots each.

Idea2Prior's EASY score follows operational recovery: the first batch delivered five target hits out of ten planned queries, and the final selected runs delivered nine. The summary separates the final benchmark from first-attempt delivery reliability. MEDIUM and HARD retain the selected historical runs, including authorized replacements for A005 and B005. ChatGPT HARD uses only the reviewed NEW report; the OLD report is excluded.

## Browser baselines

The user reports that ChatGPT and Gemini were tested using the October 2026 web versions of Deep Research. Model/build identifiers and subscription tiers were not recorded, so the month identifies the testing period rather than a reproducible backend snapshot. The supplied mean times are ChatGPT 5.2 minutes/query and Gemini 4.7 minutes/query. These are user-reported references without independent timing logs or separate means for each tier. Browser token counts are unavailable.

The original ChatGPT tests raised a possible cross-chat history issue: the user reported new conversations with memory/history references enabled. The adopted replacement HARD result is the NEW report. These are historical, user-supplied browser outputs; this documentation update does not establish a newly controlled contamination-free evaluation.

## Idea2Prior timing and token accounting

| Scope | Queries | Mean seconds | Reported input tokens, total | Reported output tokens, total | Reported tokens, total | Requests missing usage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY | 10 | 534.4499 | 2,888,218 | 881,536 | 3,769,754 | 754 |
| MEDIUM | 10 | 588.7486 | 3,041,229 | 916,957 | 3,958,186 | 63 |
| HARD | 10 | 477.0406 | 3,028,382 | 886,210 | 3,914,592 | 674 |
| All three | 30 | 533.4130 | 8,957,829 | 2,684,703 | 11,642,532 | 1,491 |

Means use the final selected per-query runs. They include failed requests within those runs but exclude replaced whole-query attempts and shared preflight checks. Tokens sum all model stages and exclude Codex conversation usage. These are provider-reported subtotals; missing usage is unknown, not zero. A request without usage is not assumed to have incurred a charge. Complete token consumption and provider billing cannot be recovered from these subtotals.

For EASY, a separate all-attempt accounting included 15 attempts and eight unique shared preflight checks: 450,657.4 reported tokens per planned query and 13.0445 minutes per planned query, with 1,011 requests missing usage. That elapsed time includes failed attempts, preparation, and interruption recovery, so it is not the selected-run speed shown in the README. The selected 30-run table is not an inventory of all historical spending.

The two later web/Notebook functional trials are recorded separately in [VALIDATION.md](VALIDATION.md). Their runs are excluded from these 30-query historical means. Historical accuracy and efficiency also do not validate the public release's migrated native-Gemini preset.

## Cost reference

Idea2Prior's user-reported nine-query sample averages **$0.0945 USD per query**. The cost sample is separate from the 30-query timing/token cohort. No per-tier prices or complete historical bill are inferred from it; pricing depends on the chosen API, models, and retrieval services.

Paid web-plan references, checked on **2026-10-13**:

| Web product | Example subscription price | Price per Deep Research report |
| --- | --- | --- |
| ChatGPT | Plus: $20 USD/month | Not established by the cited official pages |
| Gemini | Google AI Pro: $19.99 USD/month | Not established by the cited official pages |

Subscription fees cannot be converted into observed per-query experiment costs without the actual plan and usage records. These examples do not identify the subscriptions used in the historical tests. Free access and other paid plans may have different access and limits.

Official sources: [OpenAI plan pricing](https://learn.chatgpt.com/docs/pricing), [OpenAI web-search and research guidance](https://learn.chatgpt.com/docs/web-search?surface=web), [Google AI subscription pricing](https://gemini.google/subscriptions/?currencyCode=USD), and [Google Deep Research access and limits](https://support.google.com/gemini/answer/15719111?hl=en). API model prices are separate from these browser subscriptions.

## Source fingerprints

Original browser responses and the selected pipeline/Expert outputs are included under [datasets](../datasets/README.md#historical-comparison-outputs), with byte hashes and export manifests. Raw request ledgers, configurations, PDFs, and earlier attempts remain outside the public release. This document publishes aggregate statistics and source SHA-256 fingerprints without credentials, private endpoint URLs, or local archive paths.

| Preserved source | SHA-256 |
| --- | --- |
| Agreed comparison SUMMARY.json | `22376008b78ae50f8b76a39a199e32b84e0de8d1d1e1796ed34cec0161c0a5cf` |
| Agreed comparison REPORT.md | `60c9a83072a5f4182e0c09f13061a9e76b75606d2fb74273bcbcb51eb006ef93` |
| v0.2 usage audit JSON | `ff0957b6942c361886850d5b5706aa296d1370560a174f87ce0a8851115f478a` |
| v0.5 usage audit JSON | `ab69b4acdaa1aedfe1a3b25c1a407c74a461f8183c2e02fdc4d09e75573b286b` |

Original observations and the agreed presentation convention are preserved separately. No missing ranking is fabricated, and no original source is overwritten by this summary.
